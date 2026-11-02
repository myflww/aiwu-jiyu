"""Desktop entry point for 爱屋及鱼."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import sys
import time
import traceback
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from bootstrap import apply as apply_bootstrap  # noqa: E402

apply_bootstrap()

from backend.paths import APP_HOME, ASSETS, LOGS, ensure_dirs  # noqa: E402
from backend.server import start_server  # noqa: E402


class JsApi:
    def __init__(self) -> None:
        self._window = None

    def choose_folder(self) -> str:
        import webview

        if not self._window:
            return ""
        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            return ""
        if not result:
            return ""
        return str(result[0] if isinstance(result, (list, tuple)) else result)


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _single_instance():
    """Keep one window per data directory; a second launch restores it."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    name = hashlib.sha256(str(APP_HOME).casefold().encode("utf-8")).hexdigest()[:24]
    handle = kernel32.CreateMutexW(None, False, "Local\\AiwuJiyu-" + name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, "爱屋及鱼")
        if hwnd:
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)
        return False
    # Holding this handle until process exit keeps the mutex alive.
    return handle


def _setup_log() -> None:
    try:
        ensure_dirs()
        stream = open(LOGS / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream

        def _excepthook(exc_type, exc, tb):
            traceback.print_exception(exc_type, exc, tb)
            stream.flush()

        sys.excepthook = _excepthook
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true", help="run only the local server")
    parser.add_argument("--tray", action="store_true", help="start hidden in the system tray")
    parser.add_argument("--preview", action="store_true", help="isolated interface preview, without contacting services")
    parser.add_argument("--no-auto-start", action="store_true", help="do not start bot services on launch")
    args = parser.parse_args()

    _enable_dpi()
    if getattr(sys, "frozen", False):
        _setup_log()

    instance = _single_instance() if not args.headless and not args.preview else None
    if instance is False:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("myflww.AiwuJiyu.Desktop")
    except Exception:
        pass

    server = start_server(preview=args.preview, auto_start=not args.no_auto_start)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={int(time.time())}&view=overview"
    print(f"DAFEIYU_APP_URL={url}", flush=True)

    if args.headless:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.shutdown()
            server.server_close()
        return

    try:
        import webview

        try:
            from backend.webview2 import ensure as ensure_webview2

            if not ensure_webview2():
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "爱屋及鱼需要 Microsoft Edge WebView2 Runtime 才能显示窗口。\n"
                    "自动准备没有完成；请联网后重试，"
                    "或从 Microsoft 官网安装 WebView2 Runtime。",
                    "爱屋及鱼",
                    0x10,
                )
                server.shutdown()
                server.server_close()
                return
        except Exception:
            pass

        api = JsApi()
        window = webview.create_window(
            "爱屋及鱼",
            url,
            width=1180,
            height=840,
            min_size=(980, 700),
            background_color="#F6F4F0",
            text_select=True,
            js_api=api,
        )
        api._window = window
        box: dict = {"tray": None, "quitting": False}

        def on_closing():
            if box["quitting"] or box["tray"] is None:
                return True
            try:
                window.hide()
            except Exception:
                pass
            return False

        def quit_app():
            box["quitting"] = True
            try:
                if box["tray"]:
                    box["tray"].stop()
            finally:
                try:
                    window.destroy()
                except Exception:
                    pass

        window.events.closing += on_closing
        server.quit_callback = quit_app

        def on_start():
            if args.tray:
                try:
                    window.hide()
                except Exception:
                    pass
            try:
                from tray import Tray

                box["tray"] = Tray(window, quit_app, preview=args.preview)
                box["tray"].start()
            except Exception:
                traceback.print_exc()
                if args.tray:
                    try:
                        window.show()
                    except Exception:
                        pass

        icon = ASSETS / "ui" / "app-icon.ico"
        webview.start(func=on_start, debug=False, gui="edgechromium",
                      icon=str(icon) if icon.is_file() else None)
        if box["tray"]:
            box["tray"].stop()
        server.shutdown()
        server.server_close()
    except Exception as exc:  # noqa: BLE001
        print(f"webview unavailable: {exc}", flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
