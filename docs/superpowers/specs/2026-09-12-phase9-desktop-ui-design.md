# Phase 9 — Desktop UI (IntelliFile Obsidian) — design spec

Source of truth for the look: `Frontend Design/intellifile_obsidian/DESIGN.md` and the four
Stitch screens (Search, Photos, Folders, Status) + logo. Settings, first-run and the
Ctrl+Space overlay are not designed; they are built in the same system.

## Decisions (from the user)
- Two tabs, not one box: Files (Search) and Photos keep separate searches.
- Ctrl+Space overlay is in scope, fully built on macOS and Windows.
- Night theme only in this pass; tokens are CSS variables so Day is a second set later.
- Every number shown comes from the backend. Nothing decorative is faked.

## Backend additions (all local HTTP, FastAPI)
- `GET /status` — engine state, watched folders with per-folder counts (documents / photos /
  audio), totals, failed count, index size on disk, models loaded, and the current indexing
  job (state, done/total, current file, started_at).
- `POST /index-folder` becomes asynchronous: validates, starts a background job, returns
  `{job: started}`; progress is read from `/status`. Startup rescan reports through the same
  job state.
- `POST /forget-folder` — stops watching, tombstones + purges every record under the folder.
- `POST /reindex-folder` — drops records under the folder and re-runs the scan (force).
- `GET /search?mode=smart|exact|keyword` — exact wraps the query as a phrase; keyword skips the
  semantic path. Default smart.
- `/search` results gain `size` and `modified_time`; `/status` gains `failed` via the job.

## Frontend (desktop/, React + TS + Vite + Tauri 2)
- Tailwind via PostCSS (offline), fonts bundled (@fontsource inter/jetbrains-mono,
  material-symbols). No runtime Google Fonts / CDN.
- Shell: title bar (logo, "Local AI", Quick Open), sidebar, engine footer, hint bar.
- Pages: Search, Photos, Folders (with first-run state), Status, Settings.
- Keyboard: ↑↓ move selection, ↵ open (opener plugin openPath), ⌘/Ctrl+↵ reveal, Esc clears.
- Mic states: idle → warming → recording → transcribing → text lands (existing Recorder).
- Overlay: second Tauri window (`overlay`), always-on-top, borderless, centred, opened by
  global shortcut Ctrl+Space (tauri-plugin-global-shortcut); Esc hides. Shares the search
  components.
- Settings persisted in localStorage now; Phase 10 wires battery toggles to the backend.

## Testing
- Backend: extend `live_test_all_phases.py` with /status, /forget-folder, async index job.
- Frontend: `tsc`, `vite build`, `cargo build`; manual walkthrough by the user on the real app.
