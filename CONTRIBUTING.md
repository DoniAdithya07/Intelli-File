# Contributing to IntelliFile

Thank you for helping. A few rules keep the project what it is.

## Ground rules

1. **Offline, always.** The app must never make a network connection: no telemetry, no update checks, no cloud APIs, no model downloads at runtime. Download scripts are for developers only.
2. **Users' real files.** IntelliFile runs on people's own documents. It may read files it is allowed to read; it must never change, move or delete them.
3. **Measure, don't assume.** A change to ranking, routing, personalization or the agent comes with numbers from the evaluation scripts, before and after.
4. **Windows is the target.** Test on Windows 10 or 11.

## Before you open a pull request

```powershell
cd backend
$env:INTELLIFILE_OFFLINE_GUARD = "1"
.\venv\Scripts\python.exe scripts\run_all_phases.py
cd ..\desktop
npx tsc --noEmit
```

All scripts must pass with zero network attempts. If you add a feature, add a script under `backend/scripts/` that exercises it on real files and add it to `run_all_phases.py`.

To check the finished features end to end, the way the app uses them, run the feature check. It builds its own test folder (documents in six formats, photos, a screenshot with text, a video and a spoken recording), starts a backend on a throwaway data folder, and checks file, photo, video and audio identification, the preview, filters, the offline guard and that no file was changed:

```powershell
cd backend
.\venv\Scripts\python.exe scripts\check_features.py                          # the source backend
.\venv\Scripts\python.exe scripts\check_features.py --packaged <IntelliFile>  # an unzipped release
```

When a check fails, the evidence for its cause is printed under it: whether the file is in the index, the text IntelliFile extracted from it, the results with their scores, the search route, the models and the backend's errors. `--fault corrupt-pdf`, `--fault blank-screenshot` or `--fault silent-recording` breaks one test file on purpose, to show that the check fails and names the cause.

In Claude Code (started inside this folder), the `feature-checker` agent (`.claude/agents/feature-checker.md`) runs the check on the source and on a release, uses the diagnosis and a playbook of likely causes to find why anything failed, confirms it in the code, and reports in plain words. It never changes code or tests.

## Writing and design

Interface text is plain and specific: sentence case, active voice, no emoji, no em dashes, no marketing words. Errors say what happened and what to do. The visual design uses the tokens in `desktop/tailwind.config.js` and `desktop/src/App.css`: one action colour, 1px rules, no gradients, no glow, no decorative animation.

## Reporting a problem

Open an issue with what you did, what you expected, what happened, and the relevant lines from `%LOCALAPPDATA%\IntelliFile\logs\backend.log`. Remove any file names or text you do not want to share.
