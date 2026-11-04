"""Learner progress (XP, lessons, missions, session history) stored in a local JSON file."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date
from pathlib import Path

from .lessons import LESSONS
from .missions import MISSION_BY_ID

LEVELS = [(0, "Novice"), (150, "Apprentice"), (400, "Practitioner"), (800, "Analyst"),
          (1400, "Strategist"), (2200, "Mentor")]
LESSON_XP = 50
SESSION_XP = 25
REPEAT_MISSION_SHARE = 0.25


def level_info(xp: int) -> dict:
    idx = max(i for i, (need, _) in enumerate(LEVELS) if xp >= need)
    need, name = LEVELS[idx]
    nxt = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None
    return {"level": idx + 1, "name": name, "xp": xp, "level_start": need, "next_level_at": nxt}


class ProgressStore:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.data = self._load()

    def _empty(self) -> dict:
        return {"xp": 0, "lessons": {}, "missions": {}, "sessions": []}

    def _load(self) -> dict:
        if self.path is None or not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()  # a corrupt file should not block playing
        base = self._empty()
        base.update({k: data.get(k, v) for k, v in base.items()})
        return base

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".progress-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass  # progress is a convenience; never break the game over it

    def snapshot(self) -> dict:
        with self._lock:
            d = json.loads(json.dumps(self.data))
        d["level"] = level_info(d["xp"])
        d["lessons_total"] = len(LESSONS)
        d["lessons_passed"] = sum(1 for v in d["lessons"].values() if v.get("passed"))
        return d

    def record_quiz(self, lesson_id: str, result: dict) -> int:
        with self._lock:
            prev = self.data["lessons"].get(lesson_id, {})
            gained = LESSON_XP if result["passed"] and not prev.get("passed") else 0
            self.data["lessons"][lesson_id] = {
                "passed": prev.get("passed", False) or result["passed"],
                "best": max(prev.get("best", 0), result["score"]),
                "total": result["total"],
                "date": date.today().isoformat(),
            }
            self.data["xp"] += gained
            self._save()
            return gained

    def record_missions(self, mission_ids: list[str]) -> int:
        gained = 0
        with self._lock:
            for mid in mission_ids:
                xp = MISSION_BY_ID[mid]["xp"]
                count = self.data["missions"].get(mid, 0)
                gained += xp if count == 0 else int(xp * REPEAT_MISSION_SHARE)
                self.data["missions"][mid] = count + 1
            self.data["xp"] += gained
            self._save()
        return gained

    def record_session(self, summary: dict) -> int:
        with self._lock:
            self.data["sessions"].append({**summary, "played": date.today().isoformat()})
            self.data["sessions"] = self.data["sessions"][-100:]
            self.data["xp"] += SESSION_XP
            self._save()
        return SESSION_XP
