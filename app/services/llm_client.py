from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Callable, Optional

from openai import OpenAI

from app.core.config import settings
from app.services.billing import log_openai_response_usage


class GenerationCancelled(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        self._client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def complete(
        self,
        *,
        model: str,
        instruction: str,
        input_text: str,
        reasoning_effort: Optional[str] = None,
    ) -> str:
        if not self._client:
            return "LLM disabled. Add OPENAI_API_KEY to enable model output."

        params: dict[str, Any] = dict(
            model=model,
            input=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": input_text},
            ],
        )
        if reasoning_effort:
            params["reasoning"] = {"effort": reasoning_effort}

        response = self._client.responses.create(**params)
        log_openai_response_usage(
            response=response,
            model=model,
            operation="responses.create",
        )
        return response.output_text.strip()

    def stream_complete(
        self,
        *,
        model: str,
        instruction: str,
        input_text: str,
        reasoning_effort: Optional[str] = None,
        on_text: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        if not self._client:
            return "LLM disabled. Add OPENAI_API_KEY to enable model output."

        params: dict[str, Any] = dict(
            model=model,
            input=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": input_text},
            ],
        )
        if reasoning_effort:
            params["reasoning"] = {"effort": reasoning_effort}

        chunks: list[str] = []
        try:
            with self._client.responses.stream(**params) as stream:
                for event in stream:
                    if should_stop and should_stop():
                        raise GenerationCancelled("Generation cancelled")
                    if getattr(event, "type", "") == "response.output_text.delta":
                        chunks.append(getattr(event, "delta", "") or "")
                        if on_text:
                            on_text("".join(chunks))
                final_response = stream.get_final_response()
                log_openai_response_usage(
                    response=final_response,
                    model=model,
                    operation="responses.stream",
                )
                final = final_response.output_text.strip()
        except GenerationCancelled:
            raise
        except Exception:
            final = self.complete(
                model=model,
                instruction=instruction,
                input_text=input_text,
                reasoning_effort=reasoning_effort,
            )
            if on_text:
                on_text(final)
            return final

        if not final:
            final = "".join(chunks).strip()
        if on_text:
            on_text(final)
        return final

    def complete_json(
        self,
        *,
        model: str,
        instruction: str,
        input_text: str,
        reasoning_effort: Optional[str] = None,
    ) -> dict:
        raw = self.complete(
            model=model,
            instruction=instruction,
            input_text=input_text,
            reasoning_effort=reasoning_effort,
        )
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

    def generate_image(
        self,
        *,
        prompt: str,
        output_path: Path,
        model: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
    ) -> dict[str, str]:
        if not self._client:
            raise RuntimeError("LLM disabled. Add OPENAI_API_KEY to enable image generation.")

        response = self._client.images.generate(
            model=model or settings.image_model,
            prompt=prompt,
            size=size or settings.article_image_size,
            quality=quality or settings.article_image_quality,
        )
        log_openai_response_usage(
            response=response,
            model=model or settings.image_model,
            operation="images.generate",
            metadata={
                "image_count": len(getattr(response, "data", []) or []),
                "size": size or settings.article_image_size,
                "quality": quality or settings.article_image_quality,
            },
        )
        image = response.data[0] if getattr(response, "data", None) else None
        b64_json = getattr(image, "b64_json", None)
        if not image or not b64_json:
            raise ValueError("Image generation response did not include image data")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(b64_json))
        return {
            "path": str(output_path.resolve()),
            "revised_prompt": getattr(image, "revised_prompt", "") or "",
        }


llm_client = LLMClient()
