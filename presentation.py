"""Local display selection and edge-triggered alerts; no account or network calls."""
from usage_core import headline

MODES = ("auto", "five_hour", "weekly", "both")


def character_motion(state, enabled, frame, recovering=False):
    """Original Sena pose priority: fail, brief success, dance, idle. No bobbing."""
    if state in ('low', 'empty'):
        return state, 0
    if state == 'unknown':
        return 'idle_alt', 0
    if recovering:
        return 'full', 0
    if enabled:
        return f'frame_{frame % 3 + 1}', 0
    return 'idle_alt', 0


def display_windows(snapshot, mode="auto"):
    if not snapshot:
        return []
    windows = snapshot["windows"]
    if mode == "both":
        return sorted(windows, key=lambda w: w.get("minutes") or 0)
    if mode in ("five_hour", "weekly"):
        minutes = 300 if mode == "five_hour" else 10080
        return [w for w in windows if w.get("minutes") == minutes]
    return [headline(snapshot)]


def quota_state(windows, fresh):
    if not fresh or not windows:
        return "unknown"
    remaining = min(w["remaining"] for w in windows)
    return "empty" if remaining == 0 else "low" if remaining <= 20 else "full" if remaining == 100 else "normal"


class TransitionTracker:
    """No startup/cached/account-switch sounds, no repeated polling sounds."""
    def __init__(self):
        self.previous = None

    def update(self, current):
        previous, self.previous = self.previous, current
        if not current or not previous:
            return None
        if (current["account"], current.get("plan")) != (previous["account"], previous.get("plan")):
            return None
        if not 0 < current["at"] - previous["at"] <= 900:
            return None
        old = {(w["slot"], w.get("minutes")): w for w in previous["windows"]}
        new = {(w["slot"], w.get("minutes")): w for w in current["windows"]}
        if old.keys() != new.keys():
            return None
        if min(w["remaining"] for w in old.values()) < 100 and min(w["remaining"] for w in new.values()) == 100:
            return "full"
        crossings = []
        for key, window in new.items():
            before = old[key]
            # Do not interpret a different quota cycle as consumption.
            if before.get("reset") != window.get("reset"):
                continue
            if before["remaining"] > 0 and window["remaining"] == 0:
                crossings.append("empty")
            elif before["remaining"] > 20 and 0 < window["remaining"] <= 20:
                crossings.append("low")
        return "empty" if "empty" in crossings else "low" if crossings else None
