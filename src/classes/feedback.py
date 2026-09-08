"""One-time survey policy and URL metadata (no automatic network requests)."""

import math
from urllib.parse import urlencode

# Provisional website endpoint; keep routing separate from the UI.
SURVEY_URL = "https://www.openshot.org/survey/"
FEEDBACK_DELAY_SECONDS = 30 * 60


def survey_url(distribution, install_id):
    params = dict(distribution)
    params["official"] = "1" if params["official"] else "0"
    params["guid"] = install_id
    return SURVEY_URL + "?" + urlencode(params)


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
