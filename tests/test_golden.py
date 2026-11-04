"""The web app's TypeScript engine is checked against web/tests/golden/expected.json.

This test makes sure that file still describes what the Python engine does. If it fails,
the engine changed: regenerate with `python scripts/make_golden.py`, then update
web/src/engine so `npm test` passes again.
"""

import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _close(a, b, path="$"):
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), path
    elif isinstance(a, dict):
        assert set(a) == set(b), path
        for k in a:
            _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    else:
        assert a == b, path


def test_golden_fixtures_match_python_engine():
    expected = json.loads((ROOT / "web" / "tests" / "golden" / "expected.json").read_text())
    actual = json.loads(json.dumps(_load("make_golden").build(), default=str))
    _close(actual, expected)


def test_exported_web_content_is_up_to_date():
    mod = _load("export_web_content")
    for name, obj in mod.content().items():
        path = ROOT / "web" / "src" / "content" / name
        assert path.read_text(encoding="utf-8") == mod.render(obj), f"{name} is stale: run scripts/export_web_content.py"
