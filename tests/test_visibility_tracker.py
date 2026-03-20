from __future__ import annotations

import os
import asyncio
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import PropertyMock, patch

os.environ["OPENAI_API_KEY"] = ""
os.environ["COOKIE_SECURE"] = "false"
_db_handle = tempfile.NamedTemporaryFile(prefix="visibility-tests-", suffix=".db", delete=False)
os.environ["APP_DB_PATH"] = _db_handle.name

from fastapi.testclient import TestClient

from app.main import app
from app.services import visibility_tracker as visibility_tracker_service
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

    def test_public_app_config_endpoint(self) -> None:
        response = self.client.get("/api/app-config")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("brand_name", payload)
        self.assertIn("product_name", payload)
        self.assertIn("visibility_only", payload)

    def test_login_page_renders_server_side_title_and_meta(self) -> None:
        client = TestClient(app)
        response = client.get("/login")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("<title>Login | Xpaan Content Agent</title>", response.text)
        self.assertIn('meta property="og:title" content="Login | Xpaan Content Agent"', response.text)

    def create_tracker_stack(self) -> dict[str, str]:
        project = self.client.post(
            "/api/visibility/projects",
            json={
                "name": "Xpaan Core",
                "brand_name": "Xpaan",
                "brand_url": "https://xpaan.com",
                "default_schedule_frequency": "weekly",
            },
        )
        self.assertEqual(project.status_code, 200, project.text)
        project_id = project.json()["id"]

        competitor = self.client.post(
            f"/api/visibility/projects/{project_id}/competitors",
            json={"name": "Profound", "domain": "tryprofound.com"},
        )
        self.assertEqual(competitor.status_code, 200, competitor.text)

        topic = self.client.post("/api/visibility/topics", json={"project_id": project_id, "name": "AI visibility"})
        self.assertEqual(topic.status_code, 200, topic.text)
        topic_id = topic.json()["id"]

        subtopic = self.client.post(
            "/api/visibility/subtopics",
            json={"project_id": project_id, "topic_id": topic_id, "name": "Tools"},
        )
        self.assertEqual(subtopic.status_code, 200, subtopic.text)
        subtopic_id = subtopic.json()["id"]

        prompt_list = self.client.post(
            "/api/visibility/lists",
            json={
                "project_id": project_id,
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
            "project_id": project_id,
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
        ids = self.create_tracker_stack()

        projects = self.client.get("/api/visibility/projects")
        self.assertEqual(projects.status_code, 200, projects.text)
        self.assertEqual(len(projects.json()["projects"]), 1)

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace")
        self.assertEqual(workspace.status_code, 200, workspace.text)
        payload = workspace.json()

        self.assertEqual(payload["project"]["brand_name"], "Xpaan")
        self.assertEqual(payload["project"]["default_schedule_frequency"], "weekly")
        self.assertEqual(len(payload["project"]["competitors"]), 1)
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

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        self.assertEqual(len(workspace["recent_jobs"]), 1)
        self.assertEqual(len(workspace["recent_runs"]), 2)
        prompt_payload = workspace["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"][0]
        self.assertEqual(prompt_payload["run_count"], 1)
        self.assertIsNotNone(prompt_payload["latest_run_at"])
        self.assertEqual(prompt_payload["latest_status"], "completed")

        report = self.client.get(
            "/api/visibility/reports",
            params={"project_id": ids["project_id"], "level": "prompt_list", "entity_id": ids["prompt_list_id"]},
        )
        self.assertEqual(report.status_code, 200, report.text)
        report_payload = report.json()
        self.assertEqual(report_payload["total_runs"], 2)
        self.assertTrue(any(item["brand"] == "Xpaan" for item in report_payload["brand_presence"]))
        self.assertGreaterEqual(len(report_payload["daily_metrics"]), 1)
        if report_payload["domain_drilldown"]:
            prompt_ref = report_payload["domain_drilldown"][0]["prompts"][0]
            self.assertIn("response_text", prompt_ref)
            self.assertIn("brands", prompt_ref)

    def test_workspace_shows_recent_job_immediately_after_run_start(self) -> None:
        ids = self.create_tracker_stack()

        run_response = self.client.post(
            f"/api/visibility/lists/{ids['prompt_list_id']}/run",
            json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
        )
        self.assertEqual(run_response.status_code, 200, run_response.text)
        job_payload = run_response.json()

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace")
        self.assertEqual(workspace.status_code, 200, workspace.text)
        workspace_payload = workspace.json()
        recent_jobs = workspace_payload["recent_jobs"]
        self.assertTrue(any(job["id"] == job_payload["id"] for job in recent_jobs))

    def test_cancel_queued_job_marks_it_cancelled(self) -> None:
        ids = self.create_tracker_stack()

        with patch("app.api.routes_visibility.asyncio.create_task", side_effect=lambda coro: coro.close()):
            run_response = self.client.post(
                f"/api/visibility/lists/{ids['prompt_list_id']}/run",
                json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
            )
        self.assertEqual(run_response.status_code, 200, run_response.text)
        job_id = run_response.json()["id"]

        cancel_response = self.client.post(f"/api/visibility/jobs/{job_id}/cancel")
        self.assertEqual(cancel_response.status_code, 200, cancel_response.text)
        self.assertEqual(cancel_response.json()["status"], "cancelled")

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        self.assertEqual(workspace["reports"]["all"]["total_runs"], 0)
        self.assertEqual(len(workspace["recent_runs"]), 0)

    def test_cancel_requested_running_job_deletes_partial_runs(self) -> None:
        ids = self.create_tracker_stack()

        def slow_complete(*args, **kwargs):
            time.sleep(0.05)
            return "Xpaan answer\n\nCitations:\nhttps://xpaan.com"

        with patch("app.api.routes_visibility.asyncio.create_task", side_effect=lambda coro: coro.close()):
            run_response = self.client.post(
                f"/api/visibility/lists/{ids['prompt_list_id']}/run",
                json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
            )
        self.assertEqual(run_response.status_code, 200, run_response.text)
        job_id = run_response.json()["id"]

        async def run_and_cancel():
            with patch("app.services.visibility_tracker.llm_client.complete", side_effect=slow_complete):
                task = asyncio.create_task(run_visibility_prompt_list_job(job_id, force=True))
                await asyncio.sleep(0.01)
                cancel_response = await asyncio.to_thread(self.client.post, f"/api/visibility/jobs/{job_id}/cancel")
                self.assertEqual(cancel_response.status_code, 200, cancel_response.text)
                await task

        asyncio.run(run_and_cancel())

        job = self.client.get(f"/api/visibility/jobs/{job_id}")
        self.assertEqual(job.status_code, 200, job.text)
        self.assertEqual(job.json()["status"], "cancelled")

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        self.assertEqual(workspace["reports"]["all"]["total_runs"], 0)
        self.assertEqual(len(workspace["recent_runs"]), 0)

    def test_prompt_summaries_and_drilldown_include_latest_response_details(self) -> None:
        ids = self.create_tracker_stack()

        with patch(
            "app.services.visibility_tracker.llm_client.complete",
            return_value=(
                "Xpaan is a strong option for AI visibility tracking.\n\n"
                "Citations:\n"
                "https://xpaan.com/features\n"
                "https://example.com/research"
            ),
        ):
            run_response = self.client.post(
                f"/api/visibility/lists/{ids['prompt_list_id']}/run",
                json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
            )
            self.assertEqual(run_response.status_code, 200, run_response.text)
            self.wait_for_job(run_response.json()["id"])

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        prompt_payload = workspace["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"][0]
        self.assertEqual(prompt_payload["run_count"], 1)
        self.assertEqual(prompt_payload["latest_status"], "completed")
        self.assertIn("Xpaan", prompt_payload["latest_brands"])
        self.assertIn("xpaan.com", prompt_payload["latest_cited_domains"])
        self.assertIn("https://xpaan.com/features", prompt_payload["latest_cited_urls"])
        self.assertIn("Citations", prompt_payload["latest_response_text"])

        report = self.client.get(
            "/api/visibility/reports",
            params={"project_id": ids["project_id"], "level": "prompt_list", "entity_id": ids["prompt_list_id"]},
        )
        self.assertEqual(report.status_code, 200, report.text)
        report_payload = report.json()
        self.assertGreaterEqual(len(report_payload["domain_drilldown"]), 1)
        prompt_ref = report_payload["domain_drilldown"][0]["prompts"][0]
        self.assertEqual(prompt_ref["status"], "completed")
        self.assertIn("Xpaan", prompt_ref["brands"])
        self.assertIn("xpaan.com", prompt_ref["cited_domains"])
        self.assertIn("https://xpaan.com/features", prompt_ref["cited_urls"])
        self.assertIn("Citations", prompt_ref["response_text"])

    def test_workspace_date_filter_resets_prompt_latest_run_outside_range(self) -> None:
        ids = self.create_tracker_stack()

        run_response = self.client.post(
            f"/api/visibility/lists/{ids['prompt_list_id']}/run",
            json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
        )
        self.assertEqual(run_response.status_code, 200, run_response.text)
        self.wait_for_job(run_response.json()["id"])

        future_day = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
        workspace = self.client.get(
            f"/api/visibility/projects/{ids['project_id']}/workspace",
            params={"start_date": future_day, "end_date": future_day},
        )
        self.assertEqual(workspace.status_code, 200, workspace.text)
        payload = workspace.json()
        self.assertEqual(payload["reports"]["all"]["total_runs"], 0)
        prompt_payload = payload["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"][0]
        self.assertEqual(prompt_payload["run_count"], 0)
        self.assertIsNone(prompt_payload["latest_run_at"])
        self.assertEqual(prompt_payload["latest_response_text"], "")

    def test_bulk_prompt_add_appends_to_existing_prompt_list(self) -> None:
        ids = self.create_tracker_stack()

        add_more = self.client.post(
            "/api/visibility/prompts/bulk",
            json={
                "prompt_list_id": ids["prompt_list_id"],
                "prompts": [
                    "How visible is Xpaan in AI answer engines for enterprise SEO?",
                    "Which sources cite Xpaan most often for AI visibility?",
                ],
            },
        )
        self.assertEqual(add_more.status_code, 200, add_more.text)
        added_prompts = add_more.json()
        self.assertEqual(len(added_prompts), 2)
        self.assertEqual(added_prompts[0]["position"], 3)
        self.assertEqual(added_prompts[1]["position"], 4)

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace")
        self.assertEqual(workspace.status_code, 200, workspace.text)
        prompt_items = workspace.json()["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"]
        self.assertEqual(len(prompt_items), 4)
        self.assertEqual(prompt_items[-1]["prompt_text"], "Which sources cite Xpaan most often for AI visibility?")

    def test_b2b_prompt_generator_returns_grouped_structured_prompts(self) -> None:
        ids = self.create_tracker_stack()

        response = self.client.post(
            f"/api/visibility/projects/{ids['project_id']}/prompt-generator",
            json={
                "project_type": "b2b_saas",
                "desired_prompt_count": 40,
                "product_name": "Xpaan",
                "category": "AI visibility software",
                "quick_audience": "Marketing Head",
                "quick_context": "SaaS",
                "quick_use_case": "measure AI brand mentions and cited domains",
                "competitors": ["Profound", "Gauge"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["generated_prompt_count"], 40)
        self.assertEqual(payload["requested_prompt_count"], 40)
        self.assertGreaterEqual(len(payload["intent_groups"]), 3)
        self.assertTrue(any(prompt["prompt_type"] == "comparison" for prompt in payload["prompts"]))
        self.assertTrue(any("Xpaan vs Profound" in prompt["prompt_text"] for prompt in payload["prompts"]))

    def test_b2b_prompt_generator_rebalances_toward_awareness_and_consideration(self) -> None:
        ids = self.create_tracker_stack()

        response = self.client.post(
            f"/api/visibility/projects/{ids['project_id']}/prompt-generator",
            json={
                "project_type": "b2b_saas",
                "desired_prompt_count": 20,
                "product_name": "Xpaan",
                "category": "AI visibility software",
                "quick_audience": "Marketing Head",
                "quick_context": "SaaS",
                "quick_use_case": "measure AI brand mentions and cited domains",
                "competitors": ["Profound", "Gauge", "Brand Radar"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        prompts = response.json()["prompts"]
        comparison_count = sum(1 for prompt in prompts if prompt["prompt_type"] == "comparison")
        primary_stage_count = sum(1 for prompt in prompts if prompt["intent_stage"] in {"awareness", "consideration"})
        self.assertLessEqual(comparison_count, 8)
        self.assertGreaterEqual(primary_stage_count, 12)

    def test_prompt_generator_uses_gpt_polish_when_llm_is_enabled(self) -> None:
        ids = self.create_tracker_stack()
        polished_text = "Which AI visibility tools should a SaaS marketing head shortlist first?"

        with patch.object(type(visibility_tracker_service.llm_client), "enabled", new_callable=PropertyMock, return_value=True):
            with patch("app.services.visibility_tracker.llm_client.complete_json", return_value={
                "prompts": [
                    {"id": "draft-1", "prompt_text": polished_text},
                ]
            }):
                response = self.client.post(
                    f"/api/visibility/projects/{ids['project_id']}/prompt-generator",
                    json={
                        "project_type": "b2b_saas",
                        "desired_prompt_count": 20,
                        "product_name": "Xpaan",
                        "category": "AI visibility software",
                        "quick_audience": "Marketing Head",
                        "quick_context": "SaaS",
                        "quick_use_case": "measure AI brand mentions and cited domains",
                        "competitors": ["Profound", "Gauge"],
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        prompts = response.json()["prompts"]
        self.assertEqual(prompts[0]["prompt_text"], polished_text)

    def test_ecommerce_prompt_generator_returns_comparison_and_validation_prompts(self) -> None:
        ids = self.create_tracker_stack()

        response = self.client.post(
            f"/api/visibility/projects/{ids['project_id']}/prompt-generator",
            json={
                "project_type": "ecommerce",
                "desired_prompt_count": 30,
                "product_name": "Xpaan Fit",
                "category": "running shoes",
                "quick_audience": "urban runners",
                "quick_context": "premium shoppers",
                "quick_use_case": "marathon training",
                "competitors": ["Nike", "Adidas"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["generated_prompt_count"], 30)
        self.assertTrue(any(group["intent_stage"] == "comparison" for group in payload["intent_groups"]))
        self.assertTrue(any(group["intent_stage"] == "validation" for group in payload["intent_groups"]))
        self.assertTrue(any("Xpaan Fit vs Nike" in prompt["prompt_text"] for prompt in payload["prompts"]))
        self.assertTrue(any(prompt["ai_format_likely"] == "comparison" for prompt in payload["prompts"]))

    def test_services_prompt_generator_returns_hiring_and_comparison_prompts(self) -> None:
        ids = self.create_tracker_stack()

        response = self.client.post(
            f"/api/visibility/projects/{ids['project_id']}/prompt-generator",
            json={
                "project_type": "services",
                "desired_prompt_count": 30,
                "product_name": "Xpaan Advisory",
                "category": "SEO consulting agency",
                "quick_audience": "startup founders",
                "quick_context": "B2B growth-stage companies",
                "quick_use_case": "improve AI visibility",
                "competitors": ["Profound", "Gauge"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["generated_prompt_count"], 30)
        self.assertTrue(any(group["intent_stage"] == "decision" for group in payload["intent_groups"]))
        self.assertTrue(any("how to choose a seo consulting agency" in prompt["prompt_text"].lower() for prompt in payload["prompts"]))
        self.assertTrue(any("Xpaan Advisory vs Profound" in prompt["prompt_text"] for prompt in payload["prompts"]))

    def test_local_business_prompt_generator_returns_location_aware_prompts(self) -> None:
        ids = self.create_tracker_stack()

        response = self.client.post(
            f"/api/visibility/projects/{ids['project_id']}/prompt-generator",
            json={
                "project_type": "local_business",
                "desired_prompt_count": 30,
                "product_name": "Oppositive Dental",
                "category": "dental clinic",
                "quick_audience": "families",
                "quick_context": "Mumbai",
                "quick_use_case": "teeth alignment",
                "competitors": ["Smile Studio"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["generated_prompt_count"], 30)
        self.assertTrue(any(group["intent_stage"] == "discovery" for group in payload["intent_groups"]))
        self.assertTrue(any("best dental clinic in Mumbai" in prompt["prompt_text"] for prompt in payload["prompts"]))
        self.assertTrue(any("Oppositive Dental vs Smile Studio in Mumbai" in prompt["prompt_text"] for prompt in payload["prompts"]))

    def test_prompt_and_prompt_list_deletion_remove_snapshots(self) -> None:
        ids = self.create_tracker_stack()
        run_response = self.client.post(
            f"/api/visibility/lists/{ids['prompt_list_id']}/run",
            json={"provider": "openai", "model": "gpt-5-mini", "surface": "api", "run_source": "manual"},
        )
        self.wait_for_job(run_response.json()["id"])

        delete_prompt = self.client.delete(f"/api/visibility/prompts/{ids['prompt_id']}")
        self.assertEqual(delete_prompt.status_code, 200, delete_prompt.text)

        workspace_after_prompt_delete = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        prompts_left = workspace_after_prompt_delete["topics"][0]["subtopics"][0]["prompt_lists"][0]["prompts"]
        self.assertEqual(len(prompts_left), 1)

        delete_list = self.client.delete(f"/api/visibility/lists/{ids['prompt_list_id']}")
        self.assertEqual(delete_list.status_code, 200, delete_list.text)

        workspace_after_list_delete = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        self.assertEqual(len(workspace_after_list_delete["topics"][0]["subtopics"][0]["prompt_lists"]), 0)
        self.assertEqual(workspace_after_list_delete["reports"]["all"]["total_runs"], 0)

    def test_topic_and_competitor_deletion_cleanup_overview(self) -> None:
        ids = self.create_tracker_stack()

        delete_competitor = self.client.delete(f"/api/visibility/competitors/{ids['competitor_id']}")
        self.assertEqual(delete_competitor.status_code, 200, delete_competitor.text)

        delete_topic = self.client.delete(f"/api/visibility/topics/{ids['topic_id']}")
        self.assertEqual(delete_topic.status_code, 200, delete_topic.text)

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace").json()
        self.assertEqual(len(workspace["project"]["competitors"]), 0)
        self.assertEqual(len(workspace["topics"]), 0)

    def test_project_deletion_removes_workspace(self) -> None:
        ids = self.create_tracker_stack()

        delete_project = self.client.delete(f"/api/visibility/projects/{ids['project_id']}")
        self.assertEqual(delete_project.status_code, 200, delete_project.text)

        projects = self.client.get("/api/visibility/projects")
        self.assertEqual(projects.status_code, 200, projects.text)
        self.assertEqual(projects.json()["projects"], [])

        workspace = self.client.get(f"/api/visibility/projects/{ids['project_id']}/workspace")
        self.assertEqual(workspace.status_code, 404, workspace.text)


if __name__ == "__main__":
    unittest.main()
