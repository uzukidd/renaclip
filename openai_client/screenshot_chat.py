"""Transactional, stateless screenshot conversations for OpenAI-compatible APIs."""

from __future__ import annotations

from copy import deepcopy
import inspect
from typing import Any, Callable

from .openai_client import get_openai_client
from config_loader import reasoning_request_options


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


async def _close(resource: Any) -> None:
    if resource is not None:
        try:
            await resource.close()
        except Exception:
            pass


async def _emit(callback: Callable | None, text: str) -> None:
    if callback is not None and text:
        result = callback(text)
        if inspect.isawaitable(result):
            await result


class ScreenshotConversation:
    """One image and a provider snapshot, with no dependency on server history.

    ``send`` returns the complete answer and raises on failure or concurrent use.
    ``on_delta`` receives text fragments when streaming and may be sync or async.
    Callback failures and cancellation also roll back history. Already delivered
    fragments are provisional until ``send`` succeeds. ``history`` is a defensive
    copy of API-native items, including Responses reasoning/output items.
    """

    def __init__(self, provider: dict, image_data_url: str):
        self._provider = deepcopy(provider)
        self._interface = str(provider.get("api_interface") or provider.get("api_type")
                              or "chat_completions").strip().lower()
        if self._interface not in ("responses", "chat_completions"):
            raise ValueError("Unsupported OpenAI API interface")
        if not isinstance(image_data_url, str) or not image_data_url.strip():
            raise ValueError("An image data URL is required")
        self._image_data_url = image_data_url
        self._history: list[dict] = []
        self._sending = False

    @property
    def history(self) -> list[dict]:
        """Committed history only; callers cannot mutate conversation state."""
        return deepcopy(self._history)

    async def send(self, question: str, on_delta: Callable | None = None) -> str:
        if self._sending:
            raise RuntimeError("A screenshot question is already in progress")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Question must not be empty")
        self._sending = True
        client = None
        try:
            responses = self._interface == "responses"
            content = [{"type": "input_text" if responses else "text",
                        "text": question.strip()}]
            if not self._history:
                content.append({"type": "input_image", "image_url": self._image_data_url}
                               if responses else
                               {"type": "image_url", "image_url": {"url": self._image_data_url}})
            pending = self.history + [{"role": "user", "content": content}]
            client = get_openai_client(api_key=self._provider.get("api_key"),
                                       base_url=self._provider.get("base_url"))
            if client is None:
                raise RuntimeError("OpenAI client could not be initialized")
            try:
                if responses:
                    answer, output = await self._responses(client, pending, on_delta)
                else:
                    answer, output = await self._chat(client, pending, on_delta)
                if not answer.strip():
                    raise RuntimeError("The model returned an empty answer")
            finally:
                resource, client = client, None
                await _close(resource)
            self._history = pending + output
            return answer
        finally:
            try:
                await _close(client)
            finally:
                self._sending = False

    async def _responses(self, client: Any, pending: list[dict], callback: Callable | None):
        streaming = bool(self._provider.get("streaming", False))
        request = {
            "model": self._provider.get("model") or "gpt-4o", "input": deepcopy(pending),
            "stream": streaming, "store": False, "include": ["reasoning.encrypted_content"],
        }
        request.update(reasoning_request_options(self._interface, self._provider.get("reasoning_effort")))
        if self._provider.get("web_search", False):
            request["tools"] = [{"type": "web_search"}]
        result = await client.responses.create(**request)
        if streaming:
            final = None
            try:
                async for event in result:
                    kind = _get(event, "type")
                    if kind in ("error", "response.failed", "response.incomplete"):
                        raise RuntimeError(f"Responses stream failed: {kind}")
                    if kind == "response.output_text.delta":
                        await _emit(callback, _get(event, "delta", ""))
                    elif kind == "response.completed":
                        final = _get(event, "response")
                if final is None:
                    raise RuntimeError("Responses stream ended without completion")
                result_response = final
            finally:
                await _close(result)
        else:
            result_response = result
        if _get(result_response, "status") != "completed" or _get(result_response, "error"):
            raise RuntimeError("Responses request did not complete successfully")
        output = []
        for item in _get(result_response, "output", []) or []:
            output.append(deepcopy(item) if isinstance(item, dict)
                          else item.model_dump(mode="json", exclude_none=True))
        # Replay all output, not just visible text: reasoning can be required on
        # subsequent turns when store=False prevents previous_response_id usage.
        answer = "".join(block.get("text", "") for item in output
                         if item.get("type") == "message" and item.get("role") == "assistant"
                         for block in item.get("content", []) if block.get("type") == "output_text")
        return answer, output

    async def _chat(self, client: Any, pending: list[dict], callback: Callable | None):
        streaming = bool(self._provider.get("streaming", False))
        request = {
            "model": self._provider.get("model") or "gpt-4o", "messages": deepcopy(pending),
            "stream": streaming,
        }
        request.update(reasoning_request_options(self._interface, self._provider.get("reasoning_effort")))
        if self._provider.get("web_search", False):
            request["tools"] = [{"type": "web_search"}]
        result = await client.chat.completions.create(**request)
        if streaming:
            parts = []
            finished = False
            try:
                async for chunk in result:
                    choices = _get(chunk, "choices", [])
                    if not choices:
                        continue
                    choice = choices[0]
                    text = _get(_get(choice, "delta"), "content") or ""
                    if text:
                        parts.append(text)
                        await _emit(callback, text)
                    reason = _get(choice, "finish_reason")
                    if reason is not None:
                        if reason != "stop":
                            raise RuntimeError(f"Chat response did not complete: {reason}")
                        finished = True
                if not finished:
                    raise RuntimeError("Chat stream ended without completion")
            finally:
                await _close(result)
            answer = "".join(parts)
        else:
            choices = _get(result, "choices", [])
            if not choices or _get(choices[0], "finish_reason") != "stop":
                raise RuntimeError("Chat response did not complete successfully")
            answer = _get(_get(choices[0], "message"), "content") or ""
        return answer, [{"role": "assistant", "content": answer}]
