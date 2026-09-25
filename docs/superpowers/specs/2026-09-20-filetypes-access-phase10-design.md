# Design: more file types, file-access permission, Phase 10 power management

Date: 2026-09-20. Approved in chat by the user; build order is Part 1 → 2 → 3.
Standing rules for this work: no git commits; nothing outside the project
folder is modified (the app's own data dir under `~/Library/Application
Support/IntelliFile/` is the agreed exception; the app only ever *reads*
user files).

## Part 1 — More file types (bounded)

**Goal:** CSV, Excel, PowerPoint and code/config files are indexed and
searchable like documents, with a matching type badge in the UI.

- `discovery.py`: `TEXT_EXTENSIONS` grows by three named sets —
  `SPREADSHEET_EXTENSIONS` (`.csv .tsv .xlsx .xlsm`), `PRESENTATION_EXTENSIONS`
  (`.pptx`), `CODE_EXTENSIONS` (`.py .js .ts .tsx .jsx .java .c .cpp .h .hpp
  .cs .go .rs .rb .php .swift .kt .html .htm .css .json .xml .yaml .yml .toml
  .ini .cfg .sh .bat .ps1 .sql .rtf`). `.xls` (binary Excel 97) and `.doc`
  are NOT added — no pure-Python offline parser worth bundling.
- New extractors in `backend/app/extraction/`:
  - `spreadsheet_extractor.py` — CSV/TSV via stdlib `csv` (sniffed
    delimiter, header row kept); `.xlsx/.xlsm` via `openpyxl` read-only mode,
    one block per sheet, `section` = sheet name, cell values joined by
    spaces, rows by newlines. Formulas are never evaluated — `data_only=True`
    reads cached values.
  - `pptx_extractor.py` — `python-pptx`, one block per slide, `page_number`
    = slide number, `heading` = slide title, includes speaker notes.
  - `html_extractor.py` — stdlib `html.parser` strips tags/scripts/styles to
    visible text (Phase 12: never render HTML).
  - Code/config/rtf → existing `extract_text_file` (RTF control words are
    stripped with a small regex pass so search sees prose, not `\par`).
- Guardrails (bug class #1 at scale): `MAX_TEXT_FILE_BYTES = 5 MB` for
  spreadsheet/code/html files (logs, data dumps, bundles are skipped with a
  logged reason); `EXCLUDED_FILE_NAMES` gains lockfiles (`package-lock.json`,
  `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`, `Cargo.lock`, `Pipfile.lock`,
  `composer.lock`) and `*.min.js` / `*.min.css` via a new
  `EXCLUDED_FILE_SUFFIXES`. Per-cell/row caps keep a 5 MB CSV from producing
  a 5 MB chunk stream: rows beyond `MAX_SPREADSHEET_ROWS = 2000` are dropped.
- New deps: `openpyxl`, `python-pptx` (pure Python, offline, small).
- UI: `fileKind()` in `desktop/src/backend.ts` gets `sheet` (CSV/TSV/XLSX),
  `slides` (PPTX) and `code` groups with badge colours in `App.css`; the
  `type:` filter already works on any extension.
- Tests: `prototype_extraction.py` gains one fixture per new type (generated
  in a temp dir by the script itself); `live_test_all_phases.py` gains a CSV
  + code-file search check. Full `run_all_phases.py` must stay green.

## Part 2 — File-access permission on first run (architectural)

First launch shows a permission screen before the Folders page:

> IntelliFile needs access to your files to search them. Everything stays
> on this computer.
> ○ Allow all — search every file on this laptop
> ○ Allow limited — I'll choose which folders and files
> ○ Deny — don't index anything

- **Allow all** → backend indexes every readable drive root (`/` on macOS
  plus `/Volumes/*`; every fixed/removable drive letter on Windows), pruning
  `SYSTEM_EXCLUDED_ROOTS` (`/System /Library /usr /bin /sbin /private /opt
  /cores /dev /proc`, `~/Library`, `C:\Windows`, `Program Files*`,
  `ProgramData`, `AppData`, `$Recycle.Bin`, hidden dirs, dev dirs). Folders
  page shows one "Whole computer" entry with live progress.
- **Allow limited** → today's folder picker plus a file picker
  (`multiple: true`); backend gets `watched_files.json` next to
  `watched_folders.json`; single files are indexed and watched for change.
- **Deny** → search disabled, persistent banner "IntelliFile has no file
  access — change in Settings". Nothing is scanned.
- Stored as `config/access.json` (`{"mode": "all"|"limited"|"denied"}`),
  editable from Settings → File access. Downgrading (all → limited/denied)
  asks "Remove the existing index?" and honours the answer.
- Backend: `GET/POST /access`; `LiveIndexing.enqueue()` is a no-op while
  mode is `denied`.
- macOS still shows its own Desktop/Documents/Downloads/removable-drive
  prompts on first read; that is the OS's dialog, not ours.

## Part 3 — Phase 10: Resource & Power Management (architectural)

Settings gets real controls:

```
Indexing        ☑ Pause on battery   ☑ Pause on Battery Saver
Resource mode   ○ Balanced (default)  ○ Performance  ○ Battery Saver
```

Status page shows why indexing is paused and the backend's CPU %.

- `backend/app/power/power_state.py` — `psutil` for battery/AC and CPU %;
  Battery Saver via `pmset -g` (macOS Low Power Mode) / Windows
  `GetSystemPowerStatus` through `ctypes`. Polled every 10 s in a daemon
  thread; emits on change.
- `backend/app/power/resource_modes.py` — per-mode table: workers
  (Balanced 1 / Performance min(4, cores−1) / Battery Saver 1), inter-job
  sleep (0 / 0 / 200 ms), defer live re-index (no / no / yes).
- `JobQueue` gains pause/resume and a worker-count switch that drains and
  re-creates workers. `LiveIndexing` pauses on battery (if toggle on) and
  resumes on AC from the same job — no rescan.
- `config/settings.json` holds toggles + mode; `GET/POST /settings`;
  `/status` gains `power: {on_battery, battery_saver, paused_reason,
  cpu_percent}`.
- Defaults per the plan: both pause toggles on, mode Balanced.
- Tests: `prototype_power.py` with a fake power source; then a human unplugs
  the Mac mid-index and watches pause/resume (bug class #2).
- New dep: `psutil`.

## Suggested later features (not in scope)

Recent searches + pinned files in the overlay; "Ask my files" local-LLM
answer mode; HEIC photos (`pillow-heif`); date/size omnibox filters;
duplicate-file finder (hashes already exist); acoustic audio search (CLAP).
