"""Scrinium's own window (pywebview). Closing it stops Scrinium.

Windows and macOS use the system web engine. Linux needs the `window` extra (Qt):
without it, or without a desktop, Scrinium uses the browser.
"""

import importlib.util
import json
import os
import signal
import sys
import urllib.parse
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


# The page listens for this event: a folder was dropped on the window, here is its path.
DROP_EVENT = "scrinium:folder-dropped"


def first_local_path(urls: list[str]) -> str | None:
    """The first dropped thing that lives on this computer, as a path. A web page is never told
    where a dropped file lives: only the window knows."""
    for url in urls:
        if url.startswith("file://"):
            path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
            if path:
                return path
    return None


def drop_script(path: str) -> str:
    """The script that hands the path to the page. The path goes as JSON, never as code."""
    detail = json.dumps(path)
    return f"window.dispatchEvent(new CustomEvent({json.dumps(DROP_EVENT)}, {{detail: {detail}}}))"


def _hand_over_drops() -> None:
    """Qt only: wraps the web view's drop so the page gets the dropped folder's path.

    pywebview's own way (a DOM drop handler registered from Python) runs through `eval`, which
    Scrinium's content security policy blocks. Qt's `runJavaScript` is not page script and is
    not blocked. Everything else about the drop is left to pywebview and the page.
    """
    from webview.platforms.qt import BrowserView

    view_class = BrowserView.WebView
    if getattr(view_class, "_scrinium_drops", False):
        return
    original = view_class.dropEvent

    def drop_event(view: Any, event: Any) -> Any:
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            path = first_local_path([url.toString() for url in mime.urls()])
            if path is not None:
                view.page().runJavaScript(drop_script(path))
        return original(view, event)

    view_class.dropEvent = drop_event
    view_class._scrinium_drops = True


def _deny_permissions() -> None:
    """Qt only: the page gets no camera, microphone, notifications or any other permission.

    pywebview 6 grants camera and microphone, and answers the rest with a plain int that PyQt6
    refuses: the exception inside Qt closes Scrinium (asking to notify when extraction ends was
    enough). Scrinium needs none of them, so every request is denied, with Qt's own value.
    """
    from qtpy.QtWebEngineCore import QWebEnginePage
    from webview.platforms.qt import BrowserView

    page_class = BrowserView.WebPage
    if getattr(page_class, "_scrinium_permissions", False):
        return
    denied = QWebEnginePage.PermissionPolicy.PermissionDeniedByUser

    def on_permission(page: Any, url: Any, feature: Any) -> None:
        page.setFeaturePermission(url, feature, denied)

    # Connected by name when each page is created, after this runs.
    page_class.onFeaturePermissionRequested = on_permission
    page_class._scrinium_permissions = True


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
    if sys.platform.startswith("linux"):
        _deny_permissions()
        _hand_over_drops()
    # No persisted cookies or cache: the session lives as long as the window.
    webview.start(gui="qt" if sys.platform.startswith("linux") else None, private_mode=True)
