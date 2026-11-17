from typing import Any

import httpx

from app.config import settings


class LLMError(RuntimeError):
    pass


async def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.2,
) -> str:
    """OpenAI-compatible chat completion against OpenCode Zen."""
    if not settings.llm_api_key:
        raise LLMError("LLM_API_KEY is not set")

    payload = {
        "model": model or settings.llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc

    if response.status_code != 200:
        raise LLMError(f"LLM returned {response.status_code}: {response.text[:300]}")

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected LLM response: {str(data)[:300]}") from exc
