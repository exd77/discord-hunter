import base64
import json
import os
from pathlib import Path
from typing import List, Type, TypeVar

import httpx
from pydantic import BaseModel

ResponseT = TypeVar("ResponseT", bound=BaseModel)


def extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None

    candidates = [text]
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end != -1:
            candidates.insert(0, text[start:end].strip())
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            candidates.insert(0, text[start:end].strip())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None


class OpenAICompatProvider:
    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model
        self._response = None
        self._response_text = None
        self._url = os.getenv("GEMINI_BASE_URL", "").strip().rstrip("/")
        if not self._url:
            raise ValueError("GEMINI_BASE_URL is required for xAI/OpenAI-compatible provider")

    @staticmethod
    def _image_to_data_url(path: Path) -> str:
        suffix = path.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix, "application/octet-stream")
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"

    @staticmethod
    def _extract_text_from_response(payload: dict) -> str:
        output = payload.get("output") or []
        texts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = part.get("type")
                if part_type in ("output_text", "text"):
                    text = part.get("text")
                    if text:
                        texts.append(text)
        if texts:
            return "\n".join(texts).strip()

        # fallback for alt shapes
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"].strip()
        return ""

    def cache_response(self, path: Path) -> None:
        if self._response is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._response, indent=2, ensure_ascii=False), encoding="utf-8")
        if self._response_text is not None:
            path.with_suffix('.txt').write_text(self._response_text, encoding='utf-8')

    async def generate_with_images(
        self,
        *,
        images: List[Path],
        response_schema: Type[ResponseT],
        user_prompt: str | None = None,
        description: str | None = None,
        **kwargs,
    ) -> ResponseT:
        schema = response_schema.model_json_schema()
        content = []

        text_prompt = "\n\n".join(
            part for part in [
                description.strip() if isinstance(description, str) and description.strip() else "",
                user_prompt.strip() if isinstance(user_prompt, str) and user_prompt.strip() else "",
                "Be precise with coordinates and spatial relationships. For drag tasks, choose the exact object center and exact target center.",
                "Return only valid JSON matching this schema:",
                json.dumps(schema, ensure_ascii=False),
            ] if part
        )
        content.append({"type": "input_text", "text": text_prompt})

        for image in images:
            path = Path(image)
            if not path.exists():
                continue
            content.append(
                {
                    "type": "input_image",
                    "image_url": self._image_to_data_url(path),
                }
            )

        payload = {
            "model": self._model,
            "input": [
                {
                    "role": "user",
                    "content": content,
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(120.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self._url, headers=headers, json=payload)
            if resp.status_code >= 400:
                body = resp.text[:4000]
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} error from API: {body}",
                    request=resp.request,
                    response=resp,
                )
            self._response = resp.json()

        text = self._extract_text_from_response(self._response)
        self._response_text = text
        data = extract_json(text)
        if not data:
            raise ValueError(f"Failed to parse JSON from response: {text}")
        return response_schema(**data)
