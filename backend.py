"""Backend configuration and clipboard request helpers, independent of the UI."""
import sys
from dataclasses import dataclass
from pathlib import Path
import pyperclip
from config_loader import AVAILABLE_MODELS, VALID_MODIFIERS, normalize_reasoning_effort, reasoning_request_options, get_active_openai_provider, load_config as load_gem_config, normalize_openai_providers
from gemini_client import get_client, delete_chat_after

_notification_handler = None

def show_notification(title, message):
    if _notification_handler is not None:
        _notification_handler(title, message)

@dataclass
class RenaClipApp:
    """Holds model, hotkey modifier, gem specs, gem instances, and client-related config."""

    model: str
    openai_model: str
    openai_provider_name: str
    openai_api_interface: str
    openai_api_key: str
    openai_base_url: str
    openai_streaming: bool
    hotkey_modifier: str
    backend: str
    gemini_gems: list   # Gemini API gem objects (requires client init)
    openai_chats: list  # dicts {name, prompt} for each OpenAI gem
    specs: list[dict]
    gemini_psid: str | None = None
    gemini_psidts: str | None = None
    gemini_proxy: str | None = None
    gemini_cookie_browser: str | None = None
    gemini_use_browser_cookie: bool = False
    continuous_chat_enabled: bool = True
    screenshot_qa_enabled: bool = False
    screenshot_qa_web_search: bool = False
    openai_reasoning_effort: str = "default"

    @classmethod
    def load_config(cls, path: str | Path, arg_gems: list[str] | None = None) -> "RenaClipApp":
        """
        Load config from a JSON file at the given path.
        Returns RenaClipApp with specs and settings filled; gems list is empty (filled later).
        If arg_gems is provided, specs are built from it; otherwise from config file gems.
        """
        path = Path(path)  # kept for API compatibility; config read via config_loader
        gems, settings = load_gem_config()
        if arg_gems:
            specs = [{"name": s.strip()} for s in arg_gems if s and s.strip()]
        else:
            specs = gems

        def _str(v, default: str = "") -> str:
            return (v or "").strip() or default

        gemini_psid = _str(settings.get("GEMINI_PSID")) or None
        gemini_psidts = _str(settings.get("GEMINI_PSIDTS")) or None
        gemini_proxy = _str(settings.get("GEMINI_PROXY")) or None
        gemini_cookie_browser = _str(settings.get("GEMINI_COOKIE_BROWSER")) or None
        gemini_use_browser_cookie = settings.get("GEMINI_USE_BROWSER_COOKIE", False)

        mod = _str(settings.get("HOTKEY_MODIFIER"), "ctrl").lower()
        if mod not in VALID_MODIFIERS:
            mod = "ctrl"

        model = _str(settings.get("GEMINI_MODEL"), "unspecified")
        if model not in AVAILABLE_MODELS:
            model = "unspecified"
        normalize_openai_providers(settings)
        active_provider = get_active_openai_provider(settings)
        openai_model = _str(active_provider.get("model"), "gpt-4o")
        openai_provider_name = active_provider["name"]
        openai_api_interface = active_provider.get("api_interface", "chat_completions")
        openai_api_key = _str(active_provider.get("api_key")) or None
        openai_base_url = _str(active_provider.get("base_url")) or None
        openai_streaming = bool(active_provider.get("streaming", False))

        if gemini_use_browser_cookie:
            gemini_psid = None
            gemini_psidts = None

        return cls(
            model=model,
            openai_model=openai_model,
            openai_provider_name=openai_provider_name,
            openai_api_interface=openai_api_interface,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            openai_streaming=openai_streaming,
            hotkey_modifier=mod,
            backend=settings.get("BACKEND", "gemini"),
            gemini_gems=[],
            openai_chats=[],
            specs=specs,
            gemini_psid=gemini_psid,
            gemini_psidts=gemini_psidts,
            gemini_proxy=gemini_proxy,
            gemini_cookie_browser=gemini_cookie_browser,
            gemini_use_browser_cookie=gemini_use_browser_cookie,
            continuous_chat_enabled=settings.get("CONTINUOUS_CHAT_ENABLED", True) is True,
            screenshot_qa_enabled=settings.get("SCREENSHOT_QA_ENABLED", False) is True,
            screenshot_qa_web_search=settings.get("SCREENSHOT_QA_WEB_SEARCH", False) is True,
            openai_reasoning_effort=normalize_reasoning_effort(settings.get("OPENAI_REASONING_EFFORT")),
        )


async def initialize_client(app: RenaClipApp):
    client = get_client(
        proxy=app.gemini_proxy,
        psid=app.gemini_psid,
        psidts=app.gemini_psidts,
        cookie_browser=app.gemini_cookie_browser,
    )
    if client is None:
        raise Exception("Client is None.")
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)
    
    return client
    

async def process_clipboard_gemini(client, gemini_gem, text: str, model: str = "unspecified") -> None:
    """Gemini backend: send text to an actual Gemini gem, put response back to clipboard."""
    if client is None:
        print("[Warning] Gemini client is None, cannot process clipboard.", flush=True)
        show_notification("RenaClip", "Gemini client is not initialized.")
        return
    gem_name = getattr(gemini_gem, "name", None) or "(unknown gem)"
    chat = client.start_chat(gem=gemini_gem, model=model)
    try:
        response = await chat.send_message(text)
        result = (response.text or "").strip()
        pyperclip.copy(result)
        print(f"[Gemini] Clipboard updated by {gem_name}.", flush=True)
        show_notification("RenaClip", f"Clipboard updated by {gem_name}.")
    except Exception as e:
        err_msg = f"[Gemini error] {e}"
        print(err_msg, file=sys.stderr, flush=True)
        show_notification("RenaClip", "Gemini processing failed.")
    finally:
        if client is not None:
            await delete_chat_after(client, chat)


async def process_clipboard_openai(app: RenaClipApp, chat_index: int, text: str) -> None:
    """OpenAI backend: create a fresh client each time, send system prompt + user text, put response back."""
    from openai_client import get_openai_client

    if chat_index >= len(app.openai_chats):
        show_notification("RenaClip", "No OpenAI gem at this slot.")
        return

    entry = app.openai_chats[chat_index]
    gem_name = entry["name"]
    prompt = entry["prompt"]
    model = app.openai_model

    client = get_openai_client(api_key=app.openai_api_key, base_url=app.openai_base_url or None)
    if client is None:
        show_notification("RenaClip", "OpenAI client could not be initialized.")
        return

    try:
        if app.openai_api_interface == "responses":
            request = {"model": model, "input": text}
            request.update(reasoning_request_options("responses", getattr(app, "openai_reasoning_effort", "default")))
            if prompt.strip():
                request["instructions"] = prompt.strip()
            if entry.get("web_search", False):
                request["tools"] = [{"type": "web_search"}]
            if app.openai_streaming:
                stream = await client.responses.create(**request, stream=True)
                accumulated = ""
                async for event in stream:
                    if getattr(event, "type", "") == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        accumulated += delta
                        pyperclip.copy(accumulated)
                result = accumulated.strip()
                print(f"[OpenAI Responses] Streamed & clipboard updated by {gem_name} ({len(result)} chars).", flush=True)
            else:
                response = await client.responses.create(**request)
                result = (getattr(response, "output_text", "") or "").strip()
                pyperclip.copy(result)
                print(f"[OpenAI Responses] Clipboard updated by {gem_name}.", flush=True)
        else:
            messages: list = []
            if prompt.strip():
                messages.append({"role": "system", "content": prompt.strip()})
            messages.append({"role": "user", "content": text})
            request = {"model": model, "messages": messages}
            request.update(reasoning_request_options("chat_completions", getattr(app, "openai_reasoning_effort", "default")))
            if entry.get("web_search", False):
                request["tools"] = [{"type": "web_search"}]

            if app.openai_streaming:
                # Stream chunks and progressively update clipboard
                stream = await client.chat.completions.create(**request, stream=True)
                accumulated = ""
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        accumulated += delta
                        pyperclip.copy(accumulated)
                result = accumulated
                print(f"[OpenAI Completions] Streamed & clipboard updated by {gem_name} ({len(result)} chars).", flush=True)
            else:
                resp = await client.chat.completions.create(**request)
                result = (resp.choices[0].message.content or "").strip()
                pyperclip.copy(result)
                print(f"[OpenAI Completions] Clipboard updated by {gem_name}.", flush=True)
        show_notification("RenaClip", f"Clipboard updated by {gem_name} (OpenAI).")
    except Exception as e:
        err_msg = f"[OpenAI error] {e}"
        print(err_msg, file=sys.stderr, flush=True)
        show_notification("RenaClip", "OpenAI processing failed.")
    finally:
        try:
            await client.close()
        except Exception:
            pass


def register_app_hotkeys(keyboard_module, modifier, gem_callback, screenshot_callback=None, chat_callback=None):
    """Return only this app's hook handles, including optional screenshot Q&A."""
    handles = []
    try:
        for index in range(8):
            handles.append(keyboard_module.add_hotkey(
                f"{modifier}+{index + 1}", lambda index=index: gem_callback(index),
            ))
        if screenshot_callback is not None:
            # Non-suppressing keyboard hooks lose the full chord on key release.
            handles.append(keyboard_module.add_hotkey(
                f"{modifier}+0", screenshot_callback, trigger_on_release=False,
            ))
        if chat_callback is not None:
            handles.append(keyboard_module.add_hotkey(
                f"{modifier}+9", chat_callback, trigger_on_release=False,
            ))
    except Exception:
        for handle in handles:
            keyboard_module.remove_hotkey(handle)
        raise
    return handles


