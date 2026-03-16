from __future__ import annotations

import os
import asyncio
import tempfile
import time
import unittest
import uuid

os.environ["OPENAI_API_KEY"] = ""
os.environ["COOKIE_SECURE"] = "false"
_db_handle = tempfile.NamedTemporaryFile(prefix="visibility-tests-", suffix=".db", delete=False)
os.environ["APP_DB_PATH"] = _db_handle.name

from fastapi.testclient import TestClient

from app.main import app
from app.services.visibility_tracker import run_visibility_prompt_list_job


class VisibilityTrackerAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.email = "user-{}@example.com".format(uuid.uuid4().hex[:10])
        response = self.client.post(
            "/api/auth/register",
            json={"email": self.email, "password": "password123"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def create_tracker_stack(self) -> dict[str, str]:
        profile = self.client.put(
            "/api/visibility/profile",
            json={
                "brand_name": "Xpaan",
                "brand_url": "https://xpaan.com",
                "default_schedule_frequency": "weekly",
            },
        )
        self.assertEqual(profile.status_code, 200, profile.text)

        competitor = self.client.post(
            "/api/visibility/competitors",
            json={"name": "Profound", "domain": "tryprofound.com"},
        )
        self.assertEqual(competitor.status_code, 200, competitor.text)

        topic = self.client.post("/api/visibility/topics", json={"name": "AI visibility"})
        self.assertEqual(topic.status_code, 200, topic.text)
        topic_id = topic.json()["id"]

        subtopic = self.client.post(
            "/api/visibility/subtopics",
            json={"topic_id": topic_id, "name": "Tools"},
        )
        self.assertEqual(subtopic.status_code, 200, subtopic.text)
        subtopic_id = subtopic.json()["id"]

        prompt_list = self.client.post(
            "/api/visibility/lists",
            json={
                "subtopic_id": subtopic_id,
                "name": "Commercial comparisons",
                "schedule_frequency": "weekly",
            },
        )
        self.assertEqual(prompt_list.status_code, 200, prompt_list.text)
        prompt_list_id = prompt_list.json()["id"]

        prompts = self.client.post(
            "/api/visibility/prompts/bulk",
            json={
                "prompt_list_id": prompt_list_id,
                "prompts": [
                    "What are the best AI visibility tools for brands like Xpaan?",
                    "Which AI SEO tools cite Xpaan and Profound most often?",
                ],
            },
        )
        self.assertEqual(prompts.status_code, 200, prompts.text)
        prompt_ids = [item["id"] for item in prompts.json()]

        return {
            "topic_id": topic_id,
            "subtopic_id": subtopic_id,
            "prompt_list_id": prompt_list_id,
            "competitor_id": competitor.json()["id"],
            "prompt_id": prompt_ids[0],
        }

    def wait_for_job(self, job_id: str) -> dict:
        asyncio.run(run_visibility_prompt_list_job(job_id, force=True))
        for _ in range(40):
            response = self.client.get(f"/api/visibility/jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            if payload["status"] in {"completed", "failed"}:
                return payload
            time.sleep(0.1)
        self.fail("Visibility job did not finish in time")

    def test_profile_competitor_and_hierarchy_creation(self) -> None:
        self.create_tracker_stack()

        overview = self.client.get("/api/visibility/overview")
        self.assertEqual(overview.status_code, 200, overview.text)
        payload = overview.json()

        self.assertEqual(payload["profile"]["brand_name"], "Xpaan")
        self.assertEqual(payload["profile"]["default_schedule_frequency"], "weekly")
        self.assertEqual(len(payload["profile"]["competitors"]), 1)
        self.assertEqual(len(payload["topics"]), 1)
        self.assertEqual(payload["topics"][0]["name"], "AI visibility")
        self.assertEqual(len(payload["topics"][0]["subtopics"]), 1)
        self.assertEqual(len(payload["topics"][0]["subtopics"][0]["prompt_lists"]), 1)
        self.assertEqual(len(payload["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"]), 2)
        self.assertEqual(payload["topics"][0]["subtopics"][0]["prompt_lists"][0]["schedule_frequency"], "weekly")
        self.assertIsNotNone(payload["topics"][0]["subtopics"][0]["prompt_lists"][0]["next_run_at"])

    def test_prompt_list_run_creates_snapshots_and_report(self) -> None:
        ids = self.create_tracker_stack()

        run_response = self.client.post(
            f"/api/visibility/lists/{ids['prompt_list_id']}/run",
            json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
        )
        self.assertEqual(run_response.status_code, 200, run_response.text)
        job = self.wait_for_job(run_response.json()["id"])

        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["completed_prompts"], 2)

        overview = self.client.get("/api/visibility/overview").json()
        self.assertEqual(len(overview["recent_jobs"]), 1)
        self.assertEqual(len(overview["recent_runs"]), 2)

        report = self.client.get(
            "/api/visibility/reports",
            params={"level": "prompt_list", "entity_id": ids["prompt_list_id"]},
        )
        self.assertEqual(report.status_code, 200, report.text)
        report_payload = report.json()
        self.assertEqual(report_payload["total_runs"], 2)
        self.assertTrue(any(item["brand"] == "Xpaan" for item in report_payload["brand_presence"]))
        self.assertGreaterEqual(len(report_payload["daily_metrics"]), 1)

    def test_prompt_and_prompt_list_deletion_remove_snapshots(self) -> None:
        ids = self.create_tracker_stack()
        run_response = self.client.post(
            f"/api/visibility/lists/{ids['prompt_list_id']}/run",
            json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
        )
        self.wait_for_job(run_response.json()["id"])

        delete_prompt = self.client.delete(f"/api/visibility/prompts/{ids['prompt_id']}")
        self.assertEqual(delete_prompt.status_code, 200, delete_prompt.text)

        overview_after_prompt_delete = self.client.get("/api/visibility/overview").json()
        prompts_left = overview_after_prompt_delete["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"]
        self.assertEqual(len(prompts_left), 1)

        delete_list = self.client.delete(f"/api/visibility/lists/{ids['prompt_list_id']}")
        self.assertEqual(delete_list.status_code, 200, delete_list.text)

        overview_after_list_delete = self.client.get("/api/visibility/overview").json()
        self.assertEqual(len(overview_after_list_delete["topics"][0]["subtopics"][0]["prompt_lists"]), 0)
        self.assertEqual(overview_after_list_delete["reports"]["all"]["total_runs"], 0)

    def test_topic_and_competitor_deletion_cleanup_overview(self) -> None:
        ids = self.create_tracker_stack()

        delete_competitor = self.client.delete(f"/api/visibility/competitors/{ids['competitor_id']}")
        self.assertEqual(delete_competitor.status_code, 200, delete_competitor.text)

        delete_topic = self.client.delete(f"/api/visibility/topics/{ids['topic_id']}")
        self.assertEqual(delete_topic.status_code, 200, delete_topic.text)

        overview = self.client.get("/api/visibility/overview").json()
        self.assertEqual(len(overview["profile"]["competitors"]), 0)
        self.assertEqual(len(overview["topics"]), 0)


if __name__ == "__main__":
    unittest.main()
