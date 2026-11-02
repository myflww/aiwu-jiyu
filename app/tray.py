"""System tray icon and menu for the desktop app."""

from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image

from backend import processes
from backend.paths import ASSETS


class Tray:
    def __init__(self, window, on_quit) -> None:
        self.window = window
        self.on_quit = on_quit
        self.icon = None

    def _image(self) -> Image.Image:
        path = ASSETS / "avatars" / "dafeiyu.png"
        image = Image.open(path).convert("RGBA")
        return image.resize((64, 64), Image.LANCZOS)

    def start(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", self.show, default=True),
            pystray.MenuItem("启动大肥鱼", lambda icon=None, item=None: self._background(processes.start_all)),
            pystray.MenuItem("停止大肥鱼", lambda icon=None, item=None: self._background(processes.stop_all)),
            pystray.MenuItem("重新扫码", self.qr),
            pystray.MenuItem("查看日志", self.logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出应用（服务继续）", self.quit),
            pystray.MenuItem("退出并停止服务", self.quit_and_stop),
        )
        self.icon = pystray.Icon("dafeiyu", self._image(), "爱屋及鱼", menu)
        self.icon.run_detached()

    def _background(self, func) -> None:
        threading.Thread(target=func, daemon=True).start()

    def show(self, icon=None, item=None) -> None:
        try:
            self.window.show()
        except Exception:
            pass

    def qr(self, icon=None, item=None) -> None:
        self.show()
        try:
            self.window.evaluate_js("openQrModal()")
        except Exception:
            pass

    def logs(self, icon=None, item=None) -> None:
        self.show()
        try:
            self.window.evaluate_js("showView('logs')")
        except Exception:
            pass

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None

    def quit(self, icon=None, item=None) -> None:
        self.stop()
        self.on_quit()

    def quit_and_stop(self, icon=None, item=None) -> None:
        try:
            processes.stop_all()
        except Exception:
            pass
        self.stop()
        self.on_quit()
