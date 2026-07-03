"""
OpenAI-compatible client for RenaClip.

Supports any OpenAI-compatible API endpoint (OpenAI, Azure, local models via Ollama/vLLM, etc.).

- get_openai_client(): build an openai.AsyncOpenAI from settings.
- fetch_available_models(): list model IDs from the /v1/models endpoint.
- process_clipboard_openai(): send text + system prompt to the model and return the response.
"""

from __future__ import annotations

import sys
from typing import Optional

from openai import AsyncOpenAI


def get_openai_client(
    api_key: str | None = None,
    base_url: str | None = None,
) -> AsyncOpenAI | None:
    """Build an AsyncOpenAI client.

    api_key: OpenAI API key. If empty/None, tries OPENAI_API_KEY env var.
    base_url: Custom endpoint (e.g. http://localhost:11434/v1 for Ollama).
              If empty/None, uses the default OpenAI endpoint.
    """
    key = (api_key or "").strip() or None
    url = (base_url or "").strip() or None

    if not key:
        print("[OpenAI] No API key provided — set OPENAI_API_KEY in settings.", file=sys.stderr, flush=True)
        return None

    client_kwargs: dict = {"api_key": key}
    if url:
        client_kwargs["base_url"] = url
        print(f"[OpenAI] Using base URL: {url}", flush=True)
    else:
        print("[OpenAI] Using default OpenAI endpoint.", flush=True)

    try:
        return AsyncOpenAI(**client_kwargs)
    except Exception as e:
        print(f"[OpenAI] Failed to create client: {e}", file=sys.stderr, flush=True)
        return None


async def fetch_available_models(
    api_key: str | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Fetch available model IDs from the OpenAI-compatible /v1/models endpoint.

    Returns a sorted list of model ID strings. Returns an empty list on failure.
    """
    client = get_openai_client(api_key=api_key, base_url=base_url)
    if client is None:
        return []

    try:
        models = await client.models.list()
        ids = sorted(
            [m.id for m in models.data if hasattr(m, "id") and m.id],
            key=str.lower,
        )
        print(f"[OpenAI] Fetched {len(ids)} available models.", flush=True)
        return ids
    except Exception as e:
        print(f"[OpenAI] Failed to fetch model list: {e}", file=sys.stderr, flush=True)
        return []
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def process_clipboard_openai(
    text: str,
    system_prompt: str,
    model: str = "gpt-4o",
    api_key: str | None = None,
    base_url: str | None = None,
) -> str:
    """Send clipboard text to an OpenAI-compatible model and return the response.

    text:          User text from clipboard.
    system_prompt: System message (equivalent to gem prompt).
    model:         Model ID string.
    api_key:       OpenAI API key.
    base_url:      Optional custom endpoint.

    Returns the model's text response, or an error message string on failure.
    """
    client = get_openai_client(api_key=api_key, base_url=base_url)
    if client is None:
        return "[Error] OpenAI client could not be initialized."

    messages: list[dict] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": text})

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = response.choices[0].message.content
        return (content or "").strip()
    except Exception as e:
        err_msg = f"[OpenAI Error] {e}"
        print(err_msg, file=sys.stderr, flush=True)
        return err_msg
    finally:
        try:
            await client.close()
        except Exception:
            pass
