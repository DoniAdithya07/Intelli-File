# Release check config: IntelliFile

Windows desktop app: Tauri 2 (Rust) + React/TypeScript/Vite front end, Python 3.11 FastAPI backend frozen with PyInstaller as a sidecar. Shipped as one portable zip.

## Project rules (breaking any is a hard FAIL)
- Offline only: every backend run and test sets `INTELLIFILE_OFFLINE_GUARD=1`; any network attempt is a hard FAIL (`/offline-guard` must report 0 attempts, and no test may log a blocked connection).
- Read-only on the user's files: no check may modify, move or delete a user file.
- Windows only: checks run on Windows 10/11.
- Never commit, push, publish or upload anything as part of a check.
- No UI text may contain emoji or em dashes (docs/UI_DESIGN.md section 26).
- Every feature of the previous release must still be present (search, voice, photos and videos, Ask, For You, Activity, Index, Settings, OCR, Windows Recent import, Ctrl+Space quick search, privacy and terms pages).

## Environment
- Virtual env / toolchain: `backend\venv` (Python 3.11); Node LTS for `desktop`; Rust stable, with `cargo` on PATH for the Tauri build (on this PC Rust lives on drive D: `CARGO_HOME=D:\DevTools\cargo`, `RUSTUP_HOME=D:\DevTools\rustup`, so put `D:\DevTools\cargo\bin` on PATH)
- Required env vars: `INTELLIFILE_OFFLINE_GUARD=1`, `PYTHONIOENCODING=utf-8`
- Working files: everything the check creates (temporary test folders, logs, reports, screenshots, test extractions of the zip) goes in `.local\` inside the project, on drive D. Nothing goes to drive C or outside the project folder.
- Before builds and full runs: close Edge and Chrome (16 GB laptop), stop any running IntelliFile (it locks the release folder and port 8756). Run long suites in foreground chunks.

## Commands
- Install: `cd backend; .\venv\Scripts\python.exe -m pip install -r requirements-dev.txt pyinstaller` and `cd desktop; npm ci`
- Lint / format / type check: `cd backend; .\venv\Scripts\python.exe -m pyflakes app scripts` and `cd desktop; npx tsc --noEmit -p .`
- Tests (quick): `cd backend; .\venv\Scripts\python.exe scripts\run_all_phases.py` (all phase regression scripts)
- Feature check: `scripts\check_features.py` (source) and `scripts\check_features.py --packaged <unzipped IntelliFile>` (release), or the `feature-checker` agent
- Tests (full): quick, plus `scripts\live_test_all_phases.py` against a dev server started with `python -m uvicorn app.main:app --port 8756` (offline guard on)
- Build / package:
  1. `cd backend; .\venv\Scripts\python.exe -m PyInstaller --noconfirm intellifile-backend.spec`
  2. `.\venv\Scripts\python.exe scripts\assemble_resources.py`
  3. `cd ..\desktop; npm run tauri build -- --no-bundle`
  4. `cd ..\backend; .\venv\Scripts\python.exe scripts\package_windows.py`
- Output file: `desktop\src-tauri\target\release\IntelliFile-windows.zip`, one zip with everything (the Ask model included), under GitHub's 2 GB release-file limit (2,147,483,648 bytes).

## Baselines
| Measure | Command | Baseline | Fail if |
|---|---|---|---|
| Recall@5 | `scripts\evaluate_retrieval.py` | 100% | below 100% |
| Hybrid MRR | `scripts\evaluate_retrieval.py` | 0.983 | below 0.983 |
| Search latency P95 | `scripts\evaluate_retrieval.py` | under 1 s | 1 s or more |
| Router vs always-hybrid | `scripts\evaluate_routing.py` | 104% of hybrid MRR at 72% of its latency | MRR share below 100% or latency share above 100% |
| Agent citations and grounding | `scripts\evaluate_agent.py` | 10/10 cited and grounded | fewer than 10/10 |
| Ask worst-case time | `scripts\evaluate_agent.py` | max under 50 s | 50 s or more |
| Network attempts | `/offline-guard` and suite logs | 0 | any |
| Download size | size of `IntelliFile-windows.zip` | previous release 2,519,919,466 bytes (two zips, 2026-09-26) | not smaller than the previous release, or 2,147,483,648 bytes (GitHub's limit) or more |

## Smoke test (packaged app, launched from a fresh extraction of IntelliFile-windows.zip)
1. Launch `IntelliFile.exe` → window within 2 s; sidebar shows "Works offline" and a file count within 15 s.
2. Index → Add folder (demo folder) → counts rise, status returns to "Up to date".
3. Search "how do we add capacity when lots of visitors arrive" → ranked list, route line, preview passage with "Why this file?".
4. Tabs and Filters → the filter words appear and results change; Up/Down, Enter, Ctrl+Enter, Esc work.
5. Photos → newest-first grid; "a red circle" finds the red circle.
6. Ask "when is the march invoice due, and how much is it" → closest passage first, answer with a source, Checks shown, under 50 s.
7. For You, Activity, Index, Settings, Help open without errors; Day and Night both render.
8. Ctrl+Space opens quick search.
9. Close the window → no IntelliFile or backend process left running.

## Human-only checks
- Real microphone voice search.
- Unplug the laptop mid-index (indexing pauses, then resumes from the same file).
- Clean-PC test: extract IntelliFile-windows.zip on a second Windows PC (or Windows Sandbox with networking off) and run the smoke test.
