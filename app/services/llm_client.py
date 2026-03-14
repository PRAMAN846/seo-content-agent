from __future__ import annotations

import json

from openai import OpenAI

from app.core.config import settings


class LLMClient:
    def __init__(self) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def complete(self, *, model: str, instruction: str, input_text: str) -> str:
        if not self._client:
            return "LLM disabled. Add OPENAI_API_KEY to enable model output."

        response = self._client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": input_text},
            ],
        )
        return response.output_text.strip()

    def complete_json(self, *, model: str, instruction: str, input_text: str) -> dict:
        raw = self.complete(model=model, instruction=instruction, input_text=input_text)
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            for part in parts:
                candidate = part.strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
                if candidate.startswith("{") and candidate.endswith("}"):
                    text = candidate
                    break
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("Model did not return JSON")
        return json.loads(text[start : end + 1])


llm_client = LLMClient()
