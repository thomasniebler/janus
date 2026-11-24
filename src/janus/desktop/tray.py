"""Desktop tray integration for Janus."""

import logging
import os

from janus.config import settings

logger = logging.getLogger("janus")

_tray_dashboard_url = settings.base_url


def set_tray_dashboard_url(url: str) -> None:
    """Set the dashboard URL opened by the tray menu."""
    global _tray_dashboard_url
    _tray_dashboard_url = url


def create_tray_icon():
    """Create and run a system tray icon for Janus."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        logger.info("Skipping Janus tray icon because no graphical session is available.")
        return

    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ["PYSTRAY_BACKEND"] = "appindicator"

    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as exc:
        logger.warning("Skipping Janus tray icon because desktop dependencies are unavailable: %s", exc)
        return

    width, height = 64, 64
    image = Image.new("RGB", (width, height), "blue")
    dc = ImageDraw.Draw(image)
    dc.ellipse((10, 10, 54, 54), fill="white")

    def on_open_dashboard(icon, item):
        import webbrowser

        webbrowser.open(_tray_dashboard_url)

    def on_quit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(pystray.MenuItem("Open Dashboard", on_open_dashboard), pystray.MenuItem("Quit", on_quit))
    icon = pystray.Icon("Janus", image, "Janus AI Assistant", menu)
    icon.run()
