"""
Shared config loader for gem_config.json. Used by flet_demo and renaclip_app.
"""

import json
from pathlib import Path

from constants import CONFIG_PATH

DEFAULT_SETTINGS = {
    "GEMINI_PSID": "",
    "GEMINI_PSIDTS": "",
    "GEMINI_PROXY": "",
    "GEMINI_MODEL": "unspecified",
    "GEMINI_COOKIE_BROWSER": "edge",
    "GEMINI_USE_BROWSER_COOKIE": False,
    "BACKEND": "openai",
    "HOTKEY_MODIFIER": "ctrl",
    "SCREENSHOT_QA_ENABLED": False,
    "SCREENSHOT_QA_WEB_SEARCH": False,
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "",
    "OPENAI_MODEL": "gpt-4o",
    "OPENAI_REASONING_EFFORT": "default",
    "OPENAI_MODELS": [],
    "OPENAI_STREAMING": False,
    "OPENAI_PROVIDERS": [],
    "OPENAI_ACTIVE_PROVIDER": "",
}

VALID_MODIFIERS = ("ctrl", "ctrl+shift", "ctrl+alt", "ctrl+shift+alt")
VALID_BACKENDS = ("gemini", "openai")
VALID_OPENAI_INTERFACES = ("chat_completions", "responses")
VALID_REASONING_EFFORTS = ("default", "low", "medium", "high")


def normalize_reasoning_effort(value) -> str:
    value = str(value or "default").strip().lower()
    return value if value in VALID_REASONING_EFFORTS else "default"


def reasoning_request_options(interface: str, effort) -> dict:
    effort = normalize_reasoning_effort(effort)
    if effort == "default":
        return {}
    return {"reasoning": {"effort": effort}} if interface == "responses" else {"reasoning_effort": effort}

AVAILABLE_MODELS = (
    "unspecified",
    "gemini-3.0-pro",
    "gemini-3.0-flash",
    "gemini-3.0-flash-thinking",
)

DEFAULT_GEMS = [
    {
      "name": "Chinese to English Translator",
      "description": "Translates Chinese text to English.",
      "prompt": "You are a professional translator. Reply with only the English translation."
    },
    {
      "name": "Terminology expert",
      "description": "Terminology expert",
      "prompt": "You are a concise terminology expert. Your sole mission is to explain the nouns provided by the user.\n\nWhen drafting explanations, you must strictly adhere to the following guidelines:\n\nUnified Structure: The use of numbered lists (e.g., 1. 2. 3.), bullet points, or any form of paragraph breaks is strictly prohibited. All content must be integrated into a single paragraph.\n\nPlain and Direct Language: Use smooth, natural modern English, avoiding obscure academic jargon.\n\nMinimalism: The word count must be strictly controlled between 50 and 150 words. Get straight to the point with a direct definition, followed by a brief description of the core principle or significance; do not provide divergent summaries.\n\nLogical Cohesion: The explanation should read like the body of a formal written definition, possessing strong narrative continuity and flowing seamlessly from start to finish.\n\nSingle Output: Output only the explanation itself. Do not include any additional content, including asking me new questions."
    },
    {
      "name": "极简词解",
      "description": "拒绝列表与废话，专注用一段精炼、连贯的文字为你拆解任何名词。",
      "prompt": "你是一个精炼的名词解释专家。你的唯一任务是解释用户提供的名词。\n\n在撰写解释时，必须严格遵守以下准则：\n\n结构单一化： 严禁使用分点列表（如 1. 2. 3.）、破折号列表或任何形式的分段。所有内容必须整合在一段文字内。\n\n语言明文直白： 使用通顺、自然的现代汉语，避免晦涩的学术黑话。\n\n极简主义： 字数严格控制在 50-150 字之间。开门见山，直接定义，随后简述核心原理或意义，不做发散性总结。\n\n逻辑连贯： 解释应当像书面定义的正文一样，具有极强的叙述连贯性，一气呵成。\n\n输出单一：只要输出解释即可，不要输出任何多于的内容，包括询问我新问题"
    },
]


def _clean_model_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for model in value:
        model_id = str(model or "").strip()
        if model_id and model_id not in result:
            result.append(model_id)
    return result


def normalize_openai_providers(settings: dict) -> list[dict]:
    """Return normalized providers and migrate the previous single-provider format."""
    raw_providers = settings.get("OPENAI_PROVIDERS")
    providers: list[dict] = []
    if isinstance(raw_providers, list):
        for index, raw in enumerate(raw_providers):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip() or f"Provider {index + 1}"
            models = _clean_model_ids(raw.get("models"))
            model = str(raw.get("model") or "").strip()
            if model and model not in models:
                models.insert(0, model)
            api_interface = str(
                raw.get("api_interface") or raw.get("api_type") or "chat_completions"
            ).strip().lower()
            if api_interface not in VALID_OPENAI_INTERFACES:
                api_interface = "chat_completions"
            providers.append({
                "name": name,
                "api_key": str(raw.get("api_key") or "").strip(),
                "base_url": str(raw.get("base_url") or "").strip(),
                "api_interface": api_interface,
                "model": model or (models[0] if models else "gpt-4o"),
                "models": models,
                "streaming": bool(raw.get("streaming", settings.get("OPENAI_STREAMING", False))),
            })

    if not providers:
        models = _clean_model_ids(settings.get("OPENAI_MODELS"))
        legacy_model = str(settings.get("OPENAI_MODEL") or "").strip() or "gpt-4o"
        if legacy_model not in models:
            models.insert(0, legacy_model)
        providers = [{
            "name": "Default",
            "api_key": str(settings.get("OPENAI_API_KEY") or "").strip(),
            "base_url": str(settings.get("OPENAI_BASE_URL") or "").strip(),
            "api_interface": "chat_completions",
            "model": legacy_model,
            "models": models,
            "streaming": bool(settings.get("OPENAI_STREAMING", False)),
        }]

    # Keep names unique so they can safely be used as selector values.
    used_names: set[str] = set()
    for index, provider in enumerate(providers):
        base_name = provider["name"]
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name} ({suffix})"
            suffix += 1
        provider["name"] = name
        used_names.add(name)

    active_name = str(settings.get("OPENAI_ACTIVE_PROVIDER") or "").strip()
    if active_name not in used_names:
        active_name = providers[0]["name"]
    settings["OPENAI_PROVIDERS"] = providers
    settings["OPENAI_ACTIVE_PROVIDER"] = active_name

    # Mirror the active provider into legacy keys for older callers/configs.
    active = next(p for p in providers if p["name"] == active_name)
    settings["OPENAI_API_KEY"] = active["api_key"]
    settings["OPENAI_BASE_URL"] = active["base_url"]
    settings["OPENAI_MODEL"] = active["model"]
    settings["OPENAI_MODELS"] = list(active["models"])
    settings["OPENAI_STREAMING"] = active["streaming"]
    return providers


def get_active_openai_provider(settings: dict) -> dict:
    providers = normalize_openai_providers(settings)
    active_name = settings["OPENAI_ACTIVE_PROVIDER"]
    return next(provider for provider in providers if provider["name"] == active_name)



def load_config() -> tuple[list[dict], dict]:
    gems: list[dict] = []
    settings = dict(DEFAULT_SETTINGS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                gems = data.get("gems", gems)
                settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        except (json.JSONDecodeError, KeyError):
            pass
    else:
        gems = list(DEFAULT_GEMS)
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"gems": gems, "settings": settings}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
    if not gems:
        gems = list(DEFAULT_GEMS)
    normalize_openai_providers(settings)
    return gems, settings


def save_config(gems: list[dict], settings: dict) -> None:
    normalize_openai_providers(settings)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"gems": gems, "settings": settings}, f, ensure_ascii=False, indent=2)