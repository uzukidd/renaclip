"""Async network execution with all callbacks dispatched onto the Qt UI thread."""
import asyncio
import concurrent.futures
import threading

from PySide6.QtCore import QObject, Signal, Slot, Qt


class AsyncExecutor(QObject):
    dispatch = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dispatch.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)
        self.loop = asyncio.new_event_loop()
        self.closed = False
        self.thread = threading.Thread(target=self._run, name="renaclip-network", daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    @Slot(object, object)
    def _dispatch(self, callback, args):
        if not self.closed:
            callback(*args)

    def post(self, callback, *args):
        if not self.closed:
            self.dispatch.emit(callback, args)

    def submit(self, coro, on_result=None, on_error=None):
        if self.closed:
            coro.close()
            raise RuntimeError("Network executor is closed")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def completed(result):
            try:
                value = result.result()
            except concurrent.futures.CancelledError:
                return
            except Exception as exc:
                if on_error is not None:
                    self.post(on_error, exc)
            else:
                if on_result is not None:
                    self.post(on_result, value)
        future.add_done_callback(completed)
        return future

    def close(self):
        if self.closed:
            return
        self.closed = True

        async def shutdown():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.loop.shutdown_asyncgens()
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=5)
        except (concurrent.futures.TimeoutError, RuntimeError):
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
