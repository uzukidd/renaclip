"""
Gemini client module: client factory, single/stream/chat runners, Gem API.

- get_client(): build GeminiClient from env cookies or browser (browser-cookie3).
- run_single / run_stream / run_chat: demo runners.
- Gem API: create_gem, update_gem, delete_gem, ensure_gem_exists, generate_with_gem, chat_with_gem.
  Call sites that use a gem by id/name raise GemNotFoundError if the gem does not exist.
"""

import json
import os
import re
import sys
import time

from .browser_utils import get_cookies_from_browser

def get_client(psid=None, psidts=None, proxy=None, cookie_browser=None):
    """Build GeminiClient from env cookies or browser (browser-cookie3)."""
    from gemini_webapi import GeminiClient
    
    if proxy:
        print(f"Using proxy: {proxy}", flush=True)
        proxy = proxy.strip() or None

    if psid:
        has_psidts = "yes" if psidts else "no"
        print(f"Using cookies: GEMINI_1PSID=***, GEMINI_1PSIDTS={has_psidts}", flush=True)
        if psid == "auto":
            # Use selenium to launch Edge browser and get cookies
            try:
                psid, psidts = get_cookies_from_browser(cookie_browser)
                if not psid:
                    raise ValueError("Failed to retrieve __Secure-1PSID cookie from browser")
            except RuntimeError as e:
                # Re-raise RuntimeError (browser window closed by user)
                print(f"Login process interrupted: {e}")
                return None
            
        return GeminiClient(psid, psidts, proxy=proxy)
    else:
        try:
            print("Using cookies from your browser via browser-cookie3", flush=True)
            return GeminiClient(proxy=proxy)
        except Exception as e:
            print(
                "Error: Set GEMINI_1PSID (and optionally GEMINI_1PSIDTS), "
                "or install browser-cookie3 and login at https://gemini.google.com",
                file=sys.stderr,
            )
            return None


# ---------------------------------------------------------------------------
# Runners: single-turn, stream, chat
# ---------------------------------------------------------------------------


async def delete_chat_after(client, chat):
    """Delete conversation from Gemini history after use."""
    try:
        await client.delete_chat(chat.cid)
    except Exception as e:
        print(f"[Warning] delete_chat failed: {e}", file=sys.stderr)


async def run_single(client, prompt: str = "Say hello in one sentence."):
    """Single-turn via one-shot chat; conversation is deleted after use."""
    print("Single-turn:", repr(prompt))
    print("-" * 40)
    chat = client.start_chat()
    try:
        response = await chat.send_message(prompt)
        print(response.text)
        if response.images:
            print("\n[Images in response]:", len(response.images))
    finally:
        await _delete_chat_after(client, chat)


async def run_stream(client, prompt: str = "Count from 1 to 5, one number per line."):
    """Streaming via chat; conversation is deleted after use."""
    print("Streaming:", repr(prompt))
    print("-" * 40)
    chat = client.start_chat()
    try:
        async for chunk in chat.send_message_stream(prompt):
            if chunk.text_delta:
                print(chunk.text_delta, end="", flush=True)
        print()
    finally:
        await _delete_chat_after(client, chat)


# ---------------------------------------------------------------------------
# Gem API: create, update, get by id/name, generate with gem (checks existence)
# ---------------------------------------------------------------------------

# Name prefix for gems created/managed by RenaClip. Display and config use names without this.
RENACLIP_PREFIX = "[RenaClip]"


class GemNotFoundError(Exception):
    """Raised when a gem does not exist (by id or name)."""

    def __init__(self, identifier: str, by: str = "id"):
        self.identifier = identifier
        self.by = by
        super().__init__(f"Gem not found: {by}={identifier!r}")


async def fetch_gems_cached(client, *, include_hidden: bool = False, language: str = "en"):
    """Fetch gems and cache in client.gems. Idempotent."""
    await client.fetch_gems(include_hidden=include_hidden, language=language)
    return client.gems


def get_gem_by_id_or_name(client, gem_identifier: str):
    """
    Look up a gem by id or name from client.gems (must call fetch_gems_cached first).
    Returns the gem object or None if not found.
    """
    if not hasattr(client, "gems") or client.gems is None:
        return None
    g = client.gems.get(id=gem_identifier)
    if g is not None:
        return g
    return client.gems.get(name=gem_identifier)


async def ensure_gem_exists(client, gem_identifier: str):
    """
    Fetch gems, then resolve gem by id or name. Raise GemNotFoundError if not found.
    """
    await fetch_gems_cached(client)
    gem = get_gem_by_id_or_name(client, gem_identifier)
    if gem is None:
        raise GemNotFoundError(gem_identifier, "id or name")
    return gem


async def get_or_create_gem(client, name: str, prompt: str, description: str = ""):
    """
    Get a gem by name from cache; if not found, create it with the given name, prompt, description.
    name: API name (e.g. [RenaClip]DisplayName). Returns (gem, created: bool).
    """
    await fetch_gems_cached(client)
    gem = get_gem_by_id_or_name(client, name)
    if gem is not None:
        return gem, False
    new_gem = await create_gem(client, name, prompt, description)
    return new_gem, True


async def create_gem(client, name: str, prompt: str, description: str = ""):
    """
    Create a custom gem. name: full API name (e.g. [RenaClip]DisplayName).
    Returns the created gem object.
    """
    new_gem = await client.create_gem(
        name=name,
        prompt=prompt,
        description=description or "",
    )
    return new_gem


async def update_gem(client, gem_or_id, name: str, prompt: str, description: str = ""):
    """
    Update an existing custom gem. name: full API name (e.g. [RenaClip]DisplayName).
    """
    updated = await client.update_gem(
        gem=gem_or_id,
        name=name,
        prompt=prompt,
        description=description or "",
    )
    return updated


async def delete_gem(client, gem_or_id):
    """Delete a custom gem. gem_or_id: Gem object or gem id string."""
    await client.delete_gem(gem_or_id)
    return True


def _iter_all_gems(client):
    """Yield all gem objects from client.gems after fetch_gems_cached. Best-effort."""
    if not hasattr(client, "gems") or client.gems is None:
        return
    try:
        if hasattr(client.gems, "values"):
            yield from client.gems.values()
        elif hasattr(client.gems, "__iter__") and not isinstance(client.gems, (str, bytes)):
            yield from client.gems
        else:
            pass
    except Exception:
        pass


async def delete_renaclip_gems_not_in_config(client, config_display_names: list[str]):
    """
    Fetch all gems; for each gem whose name starts with RENACLIP_PREFIX, if the
    display name (name without prefix) is not in config_display_names, delete it.
    """
    await fetch_gems_cached(client)
    config_set = set(config_display_names)
    for gem in _iter_all_gems(client):
        try:
            name = getattr(gem, "name", None) or ""
            if not name.startswith(RENACLIP_PREFIX):
                continue
            display_name = name[len(RENACLIP_PREFIX) :].strip()
            if display_name and display_name not in config_set:
                await delete_gem(client, gem)
                print(f"[RenaClip] Deleted orphan gem: {name!r}", flush=True)
        except Exception as e:
            print(f"[RenaClip] Failed to check/delete gem: {e}", file=sys.stderr, flush=True)


async def generate_with_gem(client, prompt: str, gem_identifier: str, **kwargs):
    """
    Generate content using a gem specified by id or name.
    If the gem does not exist, raises GemNotFoundError.
    kwargs are passed through to client.generate_content (e.g. model, files).
    """
    gem = await ensure_gem_exists(client, gem_identifier)
    return await client.generate_content(prompt, gem=gem, **kwargs)


async def chat_with_gem(client, gem_identifier: str, **kwargs):
    """
    Start a chat session with a gem. If the gem does not exist, raises GemNotFoundError.
    kwargs are passed through to client.start_chat (e.g. model).
    Returns (gem, chat_session).
    """
    gem = await ensure_gem_exists(client, gem_identifier)
    chat = client.start_chat(gem=gem, **kwargs)
    return gem, chat
