"""Qt interfaces; imports are lazy to keep backend/config loading lightweight."""
APP_NAME = "RenaClip"


def launch_ui(lock_path=None):
    from .material_window import launch_ui as launch
    return launch(lock_path=lock_path)
