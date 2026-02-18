"""
Gemini-API demo (https://github.com/HanaokaYuzu/Gemini-API).

All client and Gem APIs live in gemini_client module; this script parses args and calls them.

Proxy: SOCKS5_PROXY env (default socks5://127.0.0.1:8889). Needs httpx[socks] for SOCKS.

Usage:
  pip install -r requirements-gemini-demo.txt
  python gemini_demo.py              # single-turn
  python gemini_demo.py --stream     # streaming
  python gemini_demo.py --chat       # multi-turn chat

Gem API (create/update/call; call checks existence, exits with error if gem not found):
  python gemini_demo.py --create-gem NAME PROMPT [DESCRIPTION]
  python gemini_demo.py --update-gem ID_OR_NAME NEW_NAME NEW_PROMPT [DESCRIPTION]
  python gemini_demo.py --gem ID_OR_NAME [PROMPT]
"""

import asyncio
import sys

from gemini_client import (
    GemNotFoundError,
    _delete_chat_after,
    create_gem,
    ensure_gem_exists,
    get_client,
    run_chat,
    run_single,
    run_stream,
    update_gem,
)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Gemini-API demo (uses gemini_client)")
    parser.add_argument("--stream", action="store_true", help="Use streaming mode")
    parser.add_argument("--chat", action="store_true", help="Run multi-turn chat demo")
    parser.add_argument(
        "--create-gem",
        nargs="+",
        metavar=("NAME", "PROMPT", "[DESC]"),
        help="Create custom gem: --create-gem NAME PROMPT [DESCRIPTION]",
    )
    parser.add_argument(
        "--update-gem",
        nargs="+",
        metavar=("ID_OR_NAME", "NAME", "PROMPT", "[DESC]"),
        help="Update gem: --update-gem ID_OR_NAME NEW_NAME NEW_PROMPT [DESCRIPTION]",
    )
    parser.add_argument(
        "--gem",
        type=str,
        metavar="ID_OR_NAME",
        help="Generate one reply with this gem (prompt from args); exit with error if gem missing",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        default=[],
        help="Custom prompt (optional); default demo prompts if not given",
    )
    args = parser.parse_args()

    client = get_client()
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)

    try:
        if args.create_gem is not None:
            if len(args.create_gem) < 2:
                print("Usage: --create-gem NAME PROMPT [DESCRIPTION]", file=sys.stderr)
                raise SystemExit(2)
            name = args.create_gem[0]
            if len(args.create_gem) == 2:
                prompt, desc = args.create_gem[1], ""
            else:
                prompt = " ".join(args.create_gem[1:-1])
                desc = args.create_gem[-1]
            new_gem = await create_gem(client, name, prompt, desc)
            print(f"Created gem: id={new_gem.id!r} name={new_gem.name!r}")
            return

        if args.update_gem is not None:
            if len(args.update_gem) < 3:
                print("Usage: --update-gem ID_OR_NAME NEW_NAME NEW_PROMPT [DESCRIPTION]", file=sys.stderr)
                raise SystemExit(2)
            id_or_name = args.update_gem[0]
            new_name = args.update_gem[1]
            if len(args.update_gem) == 3:
                new_prompt, desc = args.update_gem[2], ""
            else:
                new_prompt = " ".join(args.update_gem[2:-1])
                desc = args.update_gem[-1]
            try:
                gem = await ensure_gem_exists(client, id_or_name)
            except GemNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                raise SystemExit(1) from e
            updated = await update_gem(client, gem, new_name, new_prompt, desc)
            print(f"Updated gem: id={updated.id!r} name={updated.name!r}")
            return

        if args.gem is not None:
            prompt = " ".join(args.prompt).strip() or "Say hello in one sentence."
            try:
                gem = await ensure_gem_exists(client, args.gem)
            except GemNotFoundError as e:
                print(f"Error: {e}", file=sys.stderr)
                raise SystemExit(1) from e
            chat = client.start_chat(gem=gem)
            try:
                response = await chat.send_message(prompt)
                print(response.text)
                if response.images:
                    print("\n[Images in response]:", len(response.images))
            finally:
                await _delete_chat_after(client, chat)
            return

        if args.chat:
            prompt_list = args.prompt if args.prompt else None
            await run_chat(client, prompt_list)
        elif args.stream:
            prompt = " ".join(args.prompt).strip() or "Count from 1 to 5, one number per line."
            await run_stream(client, prompt)
        else:
            prompt = " ".join(args.prompt).strip() or "Say hello in one sentence."
            await run_single(client, prompt)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
