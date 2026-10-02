"""Alerts: macOS notification (shows on the Mac, and on your iPhone if iPhone Mirroring/Notification forwarding is on).
Used only for meaningful triggers: a new MARKET_ONLY ENTRY, a LEVEL A slip, a collector going STALE/FAILED."""
import subprocess


def notify(title, message):
    try:
        subprocess.run(["osascript", "-e", f'display notification {message!r} with title {title!r} sound name "Glass"'.replace("'", '"')],
                       timeout=10, capture_output=True)
    except Exception:
        pass  # an alert failure must never break collection
