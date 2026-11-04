# Notes for Claude

## The owner's conventions

- **"Web app" means a static site hosted on GitHub Pages**, built and deployed by GitHub Actions,
  like `Themagicianleo/msfs-buddy`: Vite + TypeScript, Vitest (`npm test`), a `deploy.yml` that
  tests, builds with `BASE_PATH=/<repo>/` and publishes with `actions/upload-pages-artifact` +
  `actions/deploy-pages`. There is no server: everything runs in the browser, and data is fetched
  ahead of time by a scheduled workflow that commits it to `public/data/` and redeploys.
- The owner's GitHub account is `Themagicianleo`; the default branch is `main`.

## This repository

- `trade_sim/` is the Python reference engine (CLI, Flask local server). `web/` is the hosted app.
- The TypeScript engine in `web/src/engine/` is a port of the Python one. After changing engine
  behaviour in Python, mirror it in TypeScript and run `python scripts/make_golden.py`; `npm test`
  (in `web/`) must pass the golden comparison.
- Lessons, glossary, scenarios and missions are defined in Python. Run
  `python scripts/export_web_content.py` after editing them (pytest checks it).
- Everything is educational. Never add text that tells the user to buy or sell (a test checks this).
- Never commit `config.yaml`, `journal.csv`, `progress.json` or `data_cache/`.
