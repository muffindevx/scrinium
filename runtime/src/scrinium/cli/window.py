"""Scrinium's own window (pywebview). Closing it stops Scrinium.

Windows and macOS use the system web engine. Linux needs the `window` extra (Qt):
without it, or without a desktop, Scrinium uses the browser.
"""

import importlib.util
import os
import signal
import sys
from typing import Any

# The open window, to close it from another thread (`scrinium stop`).
_open_window: Any = None


def close_window() -> None:
    if _open_window is not None:
        _open_window.destroy()


def window_available(platform: str = sys.platform) -> bool:
    if importlib.util.find_spec("webview") is None:
        return False
    if platform.startswith("linux"):
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        return has_display and importlib.util.find_spec("qtpy") is not None
    return True


def quiet_window_engine() -> None:
    """Silences Qt WebEngine's internal messages (JS console, Chromium). Before importing it."""
    # Qt critical errors still show; with --verbose nothing is touched.
    os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;*.info=false;*.warning=false;js=false")
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} --log-level=3".strip()


def run_window(url: str) -> None:
    """Blocks until the window closes. pywebview requires the main thread."""
    import webview

    window = webview.create_window("Scrinium", url, width=1280, height=840, min_size=(800, 600))
    if window is None:
        raise RuntimeError("pywebview did not create the window")
    global _open_window
    _open_window = window
    # Ctrl+C or a stop signal closes the window, and Scrinium with it, in order.
    for stop in (signal.SIGINT, signal.SIGTERM):
        signal.signal(stop, lambda *_: window.destroy())
    # No persisted cookies or cache: the session lives as long as the window.
    webview.start(gui="qt" if sys.platform.startswith("linux") else None, private_mode=True)
