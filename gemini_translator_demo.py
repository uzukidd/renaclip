"""
Demo: call an "English to Chinese" translator Gem; create the gem if it does not exist.

Uses gemini_client.get_or_create_gem to ensure the translator gem exists, then
generates one translation with that gem.

Usage:
  pip install -r requirements-gemini-demo.txt
  python gemini_translator_demo.py
  python gemini_translator_demo.py "Your English sentence to translate"
"""

import asyncio
import sys

from gemini_client import _delete_chat_after, get_client, get_or_create_gem

# Translator gem: name and system prompt; created automatically when missing
TRANSLATOR_GEM_NAME = "English to Chinese Translator"
TRANSLATOR_GEM_PROMPT = (
    "You are a professional translator. Your only task is to translate text from English to Chinese. "
    "Reply with only the Chinese translation, no explanations or extra text. "
    "Keep the tone and style of the original; use natural, fluent Chinese."
)
TRANSLATOR_GEM_DESCRIPTION = "Translates English text to Chinese (Simplified)."


async def main():
    default_en = "The quick brown fox jumps over the lazy dog."
    text_to_translate = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else default_en

    client = get_client()
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)

    try:
        gem, created = await get_or_create_gem(
            client,
            name=TRANSLATOR_GEM_NAME,
            prompt=TRANSLATOR_GEM_PROMPT,
            description=TRANSLATOR_GEM_DESCRIPTION,
        )
        if created:
            print(f"Gem not found; created: {gem.name!r} (id={gem.id!r})")
        else:
            print(f"Using existing gem: {gem.name!r} (id={gem.id!r})")
        print("-" * 40)
        print(f"English: {text_to_translate}")
        print("Chinese:", end=" ")

        chat = client.start_chat(gem=gem)
        try:
            response = await chat.send_message(text_to_translate)
            print(response.text.strip())
        finally:
            await _delete_chat_after(client, chat)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
