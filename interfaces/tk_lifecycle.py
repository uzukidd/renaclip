"""Keep cyclic Tcl/Tk finalization on the owning UI thread.

Use only inside the isolated screenshot process. CPython's automatic cyclic GC
can run in any allocating thread, including the network thread, after Tk.destroy
has left widget/interpreter cycles behind. Tk interpreters must not die there.
"""
from contextlib import contextmanager
import gc


@contextmanager
def ui_thread_gc():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        gc.collect()
        if enabled:
            gc.enable()


def collect_ui_cycles():
    """Call from the screenshot worker main thread between overlays."""
    gc.collect()
