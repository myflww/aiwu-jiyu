"""System tray icon and menu for the desktop app."""

from __future__ import annotations

import threading
from PIL import Image

from backend import processes
from backend.paths import ASSETS


class Tray:
    def __init__(self, window, on_quit, *, preview: bool = False) -> None:
        self.window = window
        self.on_quit = on_quit
        self.icon = None
        self.preview = preview

    def _image(self) -> Image.Image:
        path = ASSETS / "ui" / "app-icon.ico"
        if not path.is_file():
            path = ASSETS / "avatars" / "dafeiyu.png"
        with Image.open(path) as source:
            image = source.convert("RGBA")
        return image.resize((64, 64), Image.LANCZOS)

    def start(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", self.show, default=True),
            pystray.MenuItem("启动大肥鱼", lambda icon=None, item=None: self._background(processes.start_all), enabled=not self.preview),
            pystray.MenuItem("停止大肥鱼", lambda icon=None, item=None: self._background(processes.stop_all), enabled=not self.preview),
            pystray.MenuItem("重新扫码", self.qr, enabled=not self.preview),
            pystray.MenuItem("查看日志", self.logs),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出应用（服务继续）", self.quit),
            pystray.MenuItem("退出并停止服务", self.quit_and_stop, enabled=not self.preview),
        )
        self.icon = pystray.Icon("aiwu-jiyu", self._image(), "爱屋及鱼", menu)
        self.icon.run_detached()

    def _background(self, func) -> None:
        threading.Thread(target=func, daemon=True).start()

    def show(self, icon=None, item=None) -> None:
        try:
            self.window.show()
            self.window.restore()
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
