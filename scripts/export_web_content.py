"""Export lessons, glossary, scenarios, missions and default config to JSON for the web app.

The Python package is the single source of truth for this content. Run after editing it:

    python scripts/export_web_content.py

tests/test_web_content.py fails if the exported files are out of date.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trade_sim.config import DEFAULTS, validate  # noqa: E402
from trade_sim.education.glossary import GLOSSARY  # noqa: E402
from trade_sim.education.lessons import LESSONS  # noqa: E402
from trade_sim.education.missions import MISSIONS  # noqa: E402
from trade_sim.education.practice import PRACTICE_BENCHMARK, PRACTICE_TICKERS  # noqa: E402
from trade_sim.education.scenarios import SCENARIOS  # noqa: E402
from trade_sim.education.progress import LEVELS, LESSON_XP, REPEAT_MISSION_SHARE, SESSION_XP  # noqa: E402

OUT = ROOT / "web" / "src" / "content"


def content() -> dict[str, object]:
    import copy

    cfg = validate(copy.deepcopy(DEFAULTS))
    return {
        "lessons.json": LESSONS,
        "glossary.json": GLOSSARY,
        "scenarios.json": SCENARIOS,
        "missions.json": MISSIONS,
        "config.json": cfg,
        "practice.json": {"tickers": {t: list(v) for t, v in PRACTICE_TICKERS.items()}, "benchmark": PRACTICE_BENCHMARK},
        "progress.json": {"levels": LEVELS, "lesson_xp": LESSON_XP, "session_xp": SESSION_XP,
                          "repeat_mission_share": REPEAT_MISSION_SHARE},
    }


def render(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, obj in content().items():
        (OUT / name).write_text(render(obj), encoding="utf-8")
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
