from __future__ import annotations

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


llm_client = LLMClient()
