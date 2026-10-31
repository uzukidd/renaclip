"""Persistent UI state and stateless, streaming Responses requests for desktop chat."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from config_loader import reasoning_request_options
from .openai_client import get_openai_client


_DEFAULT_STATE = {
    "messages": [], "previous_response_id": None, "provider_identity": None,
    "model": "", "reasoning_effort": "default",
}
_CREDENTIAL_FIELDS = {
    "apikey", "key", "authorization", "proxyauthorization", "password",
    "secret", "clientsecret", "token", "accesstoken", "refreshtoken",
    "credentials", "credential", "headers", "cookies",
}


def _without_credentials(value: Any) -> Any:
    """Strip credential-bearing fields recursively without changing the caller."""
    if isinstance(value, dict):
        return {key: _without_credentials(item) for key, item in value.items()
                if str(key).lower().replace("_", "").replace("-", "")
                not in _CREDENTIAL_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_without_credentials(item) for item in value]
    return deepcopy(value)


class DesktopChatStore:
    """Atomic JSON UI state storage; credentials must stay in provider settings.

    Unknown UI fields round-trip, except credential-bearing dictionary fields.
    An absent file returns fresh defaults. Unreadable or invalid files return
    defaults with ``load_warning`` and are left untouched. ``save`` and ``clear``
    are explicit writes and propagate errors, preserving the previous file when
    serialization or replacement fails. Callers should display load warnings.
    """

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        state = deepcopy(_DEFAULT_STATE)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("Chat state must be a JSON object")
            state.update(_without_credentials(saved))
            if not isinstance(state['messages'], list) or any(
                    not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                    or not isinstance(message.get('text', ''), str)
                    or not isinstance(message.get('images', []), list)
                    for message in state['messages']):
                raise ValueError('Invalid chat history messages')
            for message in state['messages']:
                for image in message.get('images', []):
                    if not isinstance(image, dict) or not isinstance(image.get('data_url'), str) or not isinstance(image.get('name', ''), str):
                        raise ValueError('Invalid saved image metadata')
            if not isinstance(state.get('draft_text', ''), str) or not isinstance(state.get('draft_images', []), list):
                raise ValueError('Invalid saved draft')
            for image in state.get('draft_images', []):
                if not isinstance(image, dict) or not isinstance(image.get('data_url'), str):
                    raise ValueError('Invalid draft image metadata')
            if state['previous_response_id'] is not None and not isinstance(state['previous_response_id'], str):
                raise ValueError('Invalid saved response ID')
        except FileNotFoundError:
            pass
        except (OSError, ValueError, UnicodeError) as error:
            state = deepcopy(_DEFAULT_STATE)
            state["load_warning"] = f"Could not load desktop chat state: {error}"
        return state

    def save(self, state: dict) -> None:
        if not isinstance(state, dict):
            raise TypeError("Chat state must be a dictionary")
        saved = deepcopy(_DEFAULT_STATE)
        saved.update(_without_credentials(state))
        serialized = json.dumps(saved, ensure_ascii=False, indent=2, allow_nan=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.path.parent,
                                             prefix=f".{self.path.name}.", suffix=".tmp",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        """Persist a fresh conversation, removing previous UI state as well."""
        self.save(deepcopy(_DEFAULT_STATE))


def provider_identity(provider: dict) -> str:
    """Stable opaque endpoint/name identity, independent of API key rotation.

    No key or key fingerprint is included. Switching accounts at the same named
    endpoint requires the UI to clear the conversation explicitly.
    """
    identity = {
        "base_url": str(provider.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/"),
        "name": str(provider.get("name") or "").strip(),
    }
    payload = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _get(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


async def _close(resource: Any) -> None:
    if resource is not None:
        try:
            result = resource.close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            # A cleanup failure must not hide the request/callback failure.
            pass


def _final_text(response: Any) -> str:
    text = _get(response, "output_text")
    if isinstance(text, str) and text.strip():
        return text
    parts = []
    for item in _get(response, "output", []) or []:
        if _get(item, "type") != "message" or _get(item, "role", "assistant") != "assistant":
            continue
        for block in _get(item, "content", []) or []:
            if _get(block, "type") == "output_text":
                text = _get(block, "text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


async def request_chat(provider: dict, text: str, images: list[dict],
                       previous_response_id: str | None,
                       on_delta: Callable | None = None, *, history: list[dict] | None = None,
                       replay_input: list[dict] | None = None, use_history: bool = False) -> dict:
    """Request one turn; return ``{'text': str, 'response_id': str}`` on success.

    Always uses Responses with stream/store enabled and only the current user
    input. ``on_delta`` may be sync or async; its fragments are provisional.
    Failures (including callback errors) propagate and cancellation propagates
    after stream/client cleanup. This function never mutates or persists history,
    provider settings, or image entries. The UI owns transactional commits and
    cancellation generations. Completed output takes precedence over deltas.
    """
    if not isinstance(text, str):
        raise ValueError("Chat text must be a string")
    if not isinstance(images, list):
        raise ValueError("Chat images must be a list")
    if not text.strip() and not images:
        raise ValueError("A message or image is required")
    if previous_response_id is not None and (
            not isinstance(previous_response_id, str) or not previous_response_id.strip()):
        raise ValueError("Previous response ID must be nonempty or None")
    content = [{"type": "input_text", "text": text}]
    for image in images:
        data_url = image.get("data_url") if isinstance(image, dict) else None
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            raise ValueError("Each image requires an image data URL")
        content.append({"type": "input_image", "image_url": data_url})
    request = {
        "model": provider.get("model") or "gpt-4o",
        "input": [{"role": "user", "content": content}],
        "stream": True, "store": True,
    }
    request.update(reasoning_request_options("responses", provider.get("reasoning_effort")))
    if provider.get('web_search', False):
        request['tools'] = [{'type': 'web_search'}]
    if previous_response_id is not None:
        request["previous_response_id"] = previous_response_id
    if history is not None:
        request['include'] = ['reasoning.encrypted_content']
    current_input = deepcopy(request['input'])
    prior_input = deepcopy(replay_input) if replay_input else []
    if not prior_input and history:
        for message in history:
            if message.get('role') == 'user':
                blocks = [{'type': 'input_text', 'text': message.get('text', '')}]
                blocks.extend({'type': 'input_image', 'image_url': image['data_url']} for image in message.get('images', []))
                prior_input.append({'role': 'user', 'content': blocks})
            elif message.get('role') == 'assistant':
                prior_input.append({'role': 'assistant', 'content': [{'type': 'output_text', 'text': message.get('text', '')}]})
    history_mode = use_history
    if use_history:
        request['input'] = prior_input + current_input
        request.pop('previous_response_id', None)
    client = get_openai_client(api_key=provider.get("api_key"), base_url=provider.get("base_url"))
    if client is None:
        raise RuntimeError("OpenAI client could not be initialized")
    stream = None
    try:
        try:
            stream = await client.responses.create(**request)
        except Exception as error:
            body = getattr(error, 'body', None)
            detail = json.dumps(body, ensure_ascii=False) if body is not None else str(error)
            detail = detail.lower()
            # Retry only this explicit, pre-stream transport limitation. Other
            # errors and partial generations must never trigger duplicate turns.
            if (getattr(error, 'status_code', None) == 400 and previous_response_id
                    and not use_history and history is not None
                    and 'previous_response_id' in detail and 'websocket' in detail
                    and ('only supported' in detail or 'only available' in detail)):
                request.pop('previous_response_id', None)
                request['input'] = prior_input + current_input
                history_mode = True
                stream = await client.responses.create(**request)
            else:
                raise
        final = None
        deltas = []
        async for event in stream:
            kind = _get(event, "type")
            if kind in ("error", "response.failed", "response.incomplete"):
                raise RuntimeError(f"Responses stream failed: {kind}")
            if kind == "response.output_text.delta":
                delta = _get(event, "delta", "")
                if isinstance(delta, str) and delta:
                    deltas.append(delta)
                    if on_delta is not None:
                        result = on_delta(delta)
                        if inspect.isawaitable(result):
                            await result
            elif kind == "response.completed":
                final = _get(event, "response")
        if final is None:
            raise RuntimeError("Responses stream ended without completion")
        if _get(final, "status") != "completed" or _get(final, "error"):
            raise RuntimeError("Responses request did not complete successfully")
        response_id = _get(final, "id")
        if not isinstance(response_id, str) or not response_id.strip():
            raise RuntimeError("Responses request returned no response ID")
        answer = _final_text(final)
        if not answer.strip():
            answer = "".join(deltas)
        if not answer.strip():
            raise RuntimeError("The model returned an empty answer")
        result = {"text": answer, "response_id": response_id}
        if history is not None:
            output = [deepcopy(item) if isinstance(item, dict) else item.model_dump(mode='json', exclude_none=True)
                      for item in _get(final, 'output', []) or []]
            if not output:
                output = [{'role': 'assistant', 'content': [{'type': 'output_text', 'text': answer}]}]
            result['replay_input'] = prior_input + current_input + output
            result['use_history'] = history_mode
        return result
    finally:
        try:
            await _close(stream)
        finally:
            await _close(client)
