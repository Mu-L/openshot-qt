"""One-time survey policy and URL metadata (no automatic network requests)."""

import base64
import json
import math
from urllib.parse import urlencode

SURVEY_URL = "https://www.openshot.org/feedback/"
FEEDBACK_DELAY_SECONDS = 30 * 60


def survey_url(distribution, install_id):
    """Encode the website's v1 context as unpadded URL-safe base64 UTF-8 JSON.

    Official website packages use ``direct``; our official MSIX uses
    ``microsoft-store``. Unidentified installs stay ``unknown`` until we can
    reliably detect distro or Steam packaging.
    """
    kind = distribution.get("package_type", "unknown")
    source = kind if kind in ("snap", "flatpak") else "unknown"
    if distribution.get("official") and kind in ("exe", "appimage", "appbundle"):
        source = "direct"
    elif distribution.get("official") and kind == "msix":
        source = "microsoft-store"
    system = distribution.get("platform", "unknown").lower()
    system = {"windows": "windows", "darwin": "macos", "macos": "macos",
              "linux": "linux"}.get(system, "unknown")
    # Website context contract (v=1):
    # os: windows, macos, linux, unknown.
    # source: direct, microsoft-store, steam, flatpak, snap, distro, unknown.
    # direct means official website downloads; distro means distribution packages.
    # steam/distro are reserved here until reliable detection is implemented.
    # version is the OpenShot version string; install_uuid is the existing
    # unique_install_id (installation/profile identifier, not a person-wide ID).
    payload = {
        "v": 1,
        "version": distribution["app_version"],
        "os": system,
        "source": source,
        "install_uuid": install_id,
    }
    context = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return SURVEY_URL + "?" + urlencode({"context": context})


class FeedbackPolicy:
    """Persist use across sessions; consume only on dismissal or survey navigation."""

    def __init__(self, settings):
        self.settings = settings
        self.unsaved_seconds = 0

    @property
    def shown(self):
        # Keep the original settings key so previously dismissed prompts stay dismissed.
        return bool(self.settings.get("feedback-shown"))

    def advance(self, seconds):
        if self.shown:
            return False
        try:
            elapsed = float(self.settings.get("feedback-use-seconds") or 0)
        except (TypeError, ValueError, OverflowError):
            elapsed = 0
        if not math.isfinite(elapsed) or elapsed < 0:
            elapsed = 0
        previous = elapsed
        elapsed = min(FEEDBACK_DELAY_SECONDS, elapsed + max(0, seconds))
        self.settings.set("feedback-use-seconds", elapsed)
        self.unsaved_seconds += max(0, seconds)
        if self.unsaved_seconds >= 60 or previous < FEEDBACK_DELAY_SECONDS <= elapsed:
            self.settings.save()
            self.unsaved_seconds = 0
        return elapsed >= FEEDBACK_DELAY_SECONDS

    def consume(self):
        if self.shown:
            return False
        self.settings.set("feedback-shown", True)
        try:
            self.settings.save()
        except Exception:
            self.settings.set("feedback-shown", False)
            raise
        return True
