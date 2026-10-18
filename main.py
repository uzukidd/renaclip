"""RenaClip: one native Qt application for UI, tray and screenshot Q&A."""
from backend import RenaClipApp, register_app_hotkeys, process_clipboard_openai


def main():
    from interfaces.application import run
    return run()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
