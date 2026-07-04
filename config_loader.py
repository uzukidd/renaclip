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
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "",
    "OPENAI_MODEL": "gpt-4o",
    "OPENAI_MODELS": [],
    "OPENAI_STREAMING": False,
}

VALID_MODIFIERS = ("ctrl", "ctrl+shift", "ctrl+alt", "ctrl+shift+alt")
VALID_BACKENDS = ("gemini", "openai")
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
    return gems, settings


def save_config(gems: list[dict], settings: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"gems": gems, "settings": settings}, f, ensure_ascii=False, indent=2)