"""Desktop entry point for 爱屋及鱼."""

from __future__ import annotations

import argparse
import ctypes
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

from backend.paths import LOGS, ensure_dirs  # noqa: E402
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
    args = parser.parse_args()

    _enable_dpi()
    if getattr(sys, "frozen", False):
        _setup_log()

    server = start_server()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={int(time.time())}&view=overview"
    print(f"DAFEIYU_APP_URL={url}", flush=True)

    if args.headless:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.shutdown()
        return

    try:
        import webview

        try:
            from backend.webview2 import ensure as ensure_webview2

            if not ensure_webview2():
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "爱屋及鱼需要 Microsoft Edge WebView2 Runtime 才能显示窗口。\n"
                    "安装包内已附带安装器，但这次没有安装成功；请联网后重试，"
                    "或从 Microsoft 官网安装 WebView2 Runtime。",
                    "爱屋及鱼",
                    0x10,
                )
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
            background_color="#f4f7fb",
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

        def on_start():
            if args.tray:
                try:
                    window.hide()
                except Exception:
                    pass
            try:
                from tray import Tray

                box["tray"] = Tray(window, quit_app)
                box["tray"].start()
            except Exception:
                traceback.print_exc()
                if args.tray:
                    try:
                        window.show()
                    except Exception:
                        pass

        webview.start(func=on_start, debug=False)
    except Exception as exc:  # noqa: BLE001
        print(f"webview unavailable: {exc}", flush=True)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.shutdown()


if __name__ == "__main__":
    main()
