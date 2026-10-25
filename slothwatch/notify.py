"""Notifications: terminal, best-effort desktop popup, local outbox file. No cloud services."""
from __future__ import annotations
import platform, shutil, subprocess
from datetime import datetime
from .config import Config


def deliver(message: str, cfg: Config, pings: int = 0, quiet: bool = True) -> None:
    print(message)
    with open(cfg.path("outbox", "notifications.log"), "a") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{message}\n")
    if pings and cfg.desktop_notify:
        first = message.splitlines()[0][:200]
        try:
            if shutil.which("notify-send"):
                subprocess.run(["notify-send", "SlothWatch", first], timeout=3, check=False)
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", f'display notification "{first}" with title "SlothWatch"'],
                               timeout=3, check=False)
        except Exception:
            pass
