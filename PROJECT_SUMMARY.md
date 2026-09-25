# IntelliFile — Project Summary & Tech Stack

*Written 2026-09-11. A plain-language explanation of what has been built so far, how it works, and what technology it uses. The phase-by-phase engineering log lives in [`README.md`](./README.md); the original specification is [`Implimentation_plan.md`](./Implimentation_plan.md).*

---

## 1. What the project is

**IntelliFile is a next-generation File Explorer.**

Today's File Explorer / Finder only lets you browse folders and match exact file names. If you can't remember what you called a file, you can't find it.

IntelliFile lets you **describe what you're looking for in plain words** — typed or spoken, typos and all — and finds the right file by **what's inside it**:

| You type / say | IntelliFile finds |
|---|---|
| "notes about handling sudden traffic spikes" | `scaling_notes.md`, even though it never contains the words "traffic spikes" |
| "sourdogh recipie" *(misspelled)* | `bread_recipe.txt` |
| 🎤 *"find my notes about horizontal scaling"* | the same result, from your voice |
| "birthday cake with candles" | the actual photo of the cake — no captions, no tags |

**Everything runs on the user's own laptop.** No cloud, no internet, no account. Files never leave the machine. This is a hard requirement of the project, not a nice-to-have.

---

## 2. How it works (the 30-second version)

```
                    ┌──────────────────────────────────────────────┐
                    │           Desktop app (Tauri + React)         │
                    │   search box · 🎤 mic button · photo search    │
                    └───────────────────┬──────────────────────────┘
                                        │ local HTTP (127.0.0.1 only)
                    ┌───────────────────▼──────────────────────────┐
                    │        Python backend (FastAPI)               │
                    │                                               │
   your folders ──► │ 1. Discover files, watch for changes          │
                    │ 2. Extract text (PDF/DOCX/TXT/MD/CSV/XLSX/PPTX…)│
                    │    Transcribe audio (Whisper)                 │
                    │    Embed photos (CLIP)                        │
                    │ 3. Split text into chunks                     │
                    │ 4. Index each chunk TWO ways:                 │
                    │      keyword  → SQLite FTS5 (BM25)            │
                    │      meaning  → bge-small vector → LanceDB    │
                    │ 5. Search: run both, fuse the rankings (RRF), │
                    │    fix typos, explain why each file matched   │
                    └───────────────────────────────────────────────┘
```

**The key idea is hybrid search.** Keyword search is precise but brittle (miss one word and it fails). Semantic search understands meaning but can be fuzzy. IntelliFile runs *both* on every query and merges the two ranked lists with **Reciprocal Rank Fusion** — so a file that ranks well on either signal surfaces, and a file that ranks well on both rises to the top.

---

## 3. Tech stack

### Desktop application (`desktop/`)

| Component | Technology | Why this choice |
|---|---|---|
| App shell | **Tauri 2** (Rust) | Produces a small native app for **both Windows and macOS** from one codebase. Much lighter than Electron (no bundled Chromium — uses the OS webview). |
| UI | **React 19 + TypeScript** | Standard, well-understood UI stack; TypeScript catches errors at build time. |
| Build tool | **Vite** | Fast builds; `tsc && vite build` type-checks then bundles. |
| Native dialogs / open-in-Finder | `@tauri-apps/plugin-dialog`, `@tauri-apps/plugin-opener` | Folder picker and "reveal file in Finder/Explorer". |
| Voice capture | Browser **Web Audio API** (`getUserMedia` → `AudioContext`) with a hand-written WAV encoder | Records 16 kHz mono WAV in the webview and POSTs it to the backend — no native audio code needed. |

### Backend (`backend/`) — Python 3.11

| Component | Technology | Why this choice |
|---|---|---|
| API server | **FastAPI + Uvicorn** | Local-only HTTP server the desktop app talks to. Python was chosen over Rust for the backend because the ML ecosystem (Whisper, embeddings, CLIP) is mature here. |
| File watching | **watchdog** | Cross-platform file-system events (create/modify/delete/rename), with a 30 s debounce so a file being saved repeatedly is indexed once. Every folder the user indexes is watched live and remembered across launches (`config/watched_folders.json`). |
| File identity | SHA-256 content hash (streamed 1 MB at a time) | Lets the app skip re-indexing unchanged files and handle renames without re-embedding. |
| Text extraction | **pypdf** (PDF), **python-docx** (DOCX), **openpyxl** (XLSX), **python-pptx** (PPTX), built-in (TXT/MD/CSV/TSV/HTML/code/RTF) | Pure-Python, no external binaries to ship. |
| Chunking | Token-bounded (224 tokens, 32 overlap, measured in Phase 13) | Long documents are split so each piece fits the embedding model's window and results can point at the matching passage. |
| Keyword index | **SQLite FTS5** with built-in **BM25** ranking | Ships inside Python's standard library — zero extra dependency, a real ranking function, and `fts5vocab` gives us the vocabulary for typo correction. |
| Vector index | **LanceDB** (embedded, file-based) | Embedded vector database — no server process, persists to disk, runs identically on Windows and macOS. |
| Text embeddings | **bge-small-en-v1.5** (384-dim, ONNX, ~130 MB; all-MiniLM-L6-v2 kept as fallback) | Chosen by measurement (Phase 13 / Improvement 2): vector-only Recall@5 93% → 100%, hybrid MRR 0.961 → 0.983 for +1 ms per query. Its relevance thresholds are derived from data (`thresholds.json`), not hand-set; a model change clears and rebuilds the text index automatically. |
| ML runtime | **ONNX Runtime**, **CPU by default** | One inference engine for all three models. Accelerators (DirectML on Windows, CoreML on macOS) are wired in but **opt-in**: measured on the shipping models, CoreML was 5× *slower* for the text embedder, no faster for CLIP, and produced wrong output for Whisper. All three models are interactive on CPU (4 ms/text, 38 ms/image, ~0.2 s/sentence). |
| Speech-to-text | **Whisper base.en** (quantized ONNX, ~73 MB) with our own greedy decoder on ONNX Runtime, **CPU only** | Local, offline transcription. Chosen over tiny.en by testing on the user's own voice (tiny heard "bread" as "Brit"). Runs on CPU deliberately: the CoreML accelerator was found to corrupt Whisper's output, and CPU is ~0.2 s per sentence anyway. The decoder was rewritten in Phase 14 so the packaged app ships without PyTorch (−1 GB). Loaded on first audio use, not at startup. |
| Audio decoding | **PyAV** (FFmpeg bindings) | Decodes `.mp3/.m4a/.flac/.ogg/.wav/...` to 16 kHz mono for Whisper; also used for the live-mic WAV. |
| Visual search | **CLIP ViT-B/16** (`Xenova/clip-vit-base-patch16`, **fp16** ONNX, ~300 MB), **CPU, one image per inference** | Maps photos *and* text into the same 512-dim space, so a typed description can be matched directly against image vectors. Chosen over B/32 and L/14 by measurement on 124 labelled photos (same size as B/32, better ranking; L/14 too big/slow). fp16 rather than int8 because the int8 export was verified against the original PyTorch model to distort embeddings by up to 0.11 — enough to mis-rank photos; fp16 matches exactly at half of fp32's size. Runs on CPU one image at a time for determinism. Torch-free. |
| Image handling | **Pillow** | EXIF orientation fix, CLIP preprocessing (resize → 224×224 crop → normalize), thumbnails, capture date. |
| Ranking fusion | Custom **Reciprocal Rank Fusion** (k = 60) + filename matching | Merges BM25 and vector rankings without needing their scores to be on the same scale. A file whose *name* covers the query ranks above content matches — naming the file is the strongest signal there is. |
| Typo tolerance | Custom Levenshtein (edit distance ≤ 2) against the **user's own indexed vocabulary** | Corrects "recipie" → "recipe" using words that actually appear in the user's files, so technical terms the user has ("Kubernetes") are corrected *toward*, not away from. |

### Packaging & CI (Phase 14 — designed, not yet executed)

| Component | Technology |
|---|---|
| Backend freeze | **PyInstaller** → single binary shipped as a Tauri *sidecar* (auto-started by the app) |
| Installers | Tauri bundler → `.msi` / NSIS `.exe` (Windows), `.app` / `.dmg` (macOS) |
| CI | GitHub Actions — `.github/workflows/windows-build.yml` and `macos-build.yml` |

### Data stored on the user's machine

All under `%LOCALAPPDATA%\IntelliFile` (Windows) or `~/Library/Application Support/IntelliFile` (macOS):

- `files.db` — SQLite: one row per file (path, hash, size, modified time, tombstone flag)
- `keyword.db` — SQLite FTS5: every text chunk, for BM25
- `vector_index/` — LanceDB: `chunks` table (384-dim text vectors) and `images` table (512-dim CLIP vectors)

Thumbnails are generated on demand, never stored — the app never duplicates the user's data.

---

## 4. What has been built so far

The project is planned in 15 phases. **Phases 0–8 are complete and verified.** Each has a regression script under `backend/scripts/prototype_*.py` that exercises real behaviour (real PDFs generated on the fly, real embeddings, real speech synthesized by the OS) — not mocks. `run_all_phases.py` runs them all; `live_test_all_phases.py` additionally hits the running HTTP server.

| Phase | What it delivers | Verified by |
|---|---|---|
| 0 | Requirements, architecture decisions (Tauri + Python sidecar) | — |
| 1 | Project scaffolding; LanceDB / SQLite / file-record storage layer; desktop app calls backend `/health` | `prototype_lancedb.py` |
| 2 | Folder discovery, live file watching, debouncing, rename/delete handling, exclusion of `node_modules/`, `venv/`, model dirs, `requirements.txt`… | `prototype_file_watch.py` |
| 3 | PDF / DOCX / TXT / MD — and since 2026-09-20 CSV / XLSX / PPTX / HTML / code / RTF — extraction into blocks; chunking with overlap | `prototype_extraction.py` |
| 4 | Full indexing pipeline: hash → extract → chunk → embed → write to both indexes; tombstone cleanup | `prototype_indexing.py` |
| 5 | Hybrid search: BM25 + vector + RRF fusion, filename matching, exact-phrase mode, file-type filter, snippets with keyword highlighting, "why this matched" explanation, relevance cutoff | `prototype_search.py` |
| 6 | Typo / grammar tolerance via vocabulary-based spelling correction | `prototype_typo_tolerance.py` |
| 7 | Voice search: 🎤 button → WAV → Whisper base.en (CPU, prompted with the user's file names) → editable text → normal search. Also indexes audio *files* by their spoken content. Live mic tested by a human, model chosen on their voice. Since 2026-09-20 a misheard bare file name ("landlord letter" → "Plan lot later") is snapped to the sound-alike file name after Whisper, only when the raw words find nothing. | `prototype_transcription.py` |
| 8 | Visual search for photos: CLIP ViT-B/16 (fp16, exact) embeddings, separate image table, `/search-visual`, on-demand thumbnails, EXIF date, strong/weak confidence tiers (absolute + relative-to-best rule). **Every threshold measured on 127 labelled photos** (`evaluate_visual_cutoff.py`), and the ONNX pipeline verified against the original PyTorch model. Photo queries are spell-checked against a bundled 100k-word English list + the user's own file names, so gibberish is refused instead of matched. | `prototype_visual_search.py` |
| 8b | Video search: PyAV decodes only the codec's keyframes (a 4K 41 s clip indexes in ~1 s), each keyframe a CLIP vector with its timestamp; fade-to-black frames skipped; one result per video at its best moment with a 3-frame filmstrip of the best distinct moments; Photos & Videos page with All / Photos / Videos filter. Tested on 6 real Wikimedia clips — each is the only strong hit for its own subject. | `prototype_visual_search.py` check 9 |
| 9 | Desktop UI (in progress): sidebar shell built from the Obsidian design mockups, all five screens (Search, Photos & Videos, Folders, Status, Settings), Ctrl+Space overlay window, "Did you mean" spelling chips with rule-based grammar tidy, platform-aware labels (Finder/Explorer, ⌘/Ctrl). Settings is info-only until Phases 10/12 give it something real to control. | browser + live app |
| 16 | Activity memory (Objective 2 foundation): every search, open, reveal and click remembered locally with a 30-min session model; `/context` = current working context; "Remember my activity" switch + Clear on Settings. Built 2026-09-21. | `prototype_usage.py` |
| 17 | User profile from the activity memory (frequent files, type mix, topics by clustering opened files, weekday × time-of-day patterns, session co-occurrence); personalized ranking with visible reasons and an on/off switch; recommendations before any query (likely next / usual now / used most); Insights screen. Built 2026-09-21. | `prototype_profile.py` |
| 18 | Query router (Objective 3): five tiers from cheap surface features — filename / metadata / keyword / hybrid / hybrid+rerank — with one-step escalation, `after:` `before:` `size:` `in:` filters, and a cross-encoder reranker (ms-marco-MiniLM-L-6-v2) used as promotion-not-veto after measuring that it mis-ranks concept matches; every response reports the route, skipped stages and per-stage ms; Auto mode + route badge in the UI. Built 2026-09-21. | `prototype_router.py` |
| 19 | Local LLM agent (the assignment's "agent"): Qwen2.5-1.5B via llama.cpp, offline on CPU; plans in JSON, chooses the retrieval strategy (mode, filters, decomposition), calls IntelliFile search as a tool, reads the chunks and streams a cited answer; grounding, citation verification and a figure check are done by the harness, not trusted to the model; every step streams to the UI as a trace (Ask mode). 10/10 on a 10-question answer key. Built 2026-09-21. | `prototype_agent.py` |
| 20 | Evaluation (Objective 3's numbers): a generated 39-document corpus with 60 labelled queries. Router keeps 104% of always-hybrid's MRR at 72% of its latency and runs the embedding model for 43% of queries instead of 97%; personalization lifts the habitual file to first on 78% of ambiguous queries (from 44%) with 0 of 42 ordinary queries harmed; reranker measured and made escalation-only; agent 10/10 on its answer key; the whole live suite passes with 0 network attempts under a socket-level offline guard. Measured 2026-09-21. | `evaluate_routing.py`, `evaluate_personalization.py` |
| 14 | Packaging (in progress): PyTorch removed by writing our own Whisper decoder on onnxruntime (identical transcripts on 30/30 clips); backend frozen with PyInstaller and verified with the full live suite under a network guard; Tauri shell spawns and stops it; all models, a sample folder and HOW_TO_RUN.md bundled; macOS `.app`/`.dmg` built and launched cold. Windows: CI workflow written, never run. 2026-09-21. | `intellifile-backend.spec`, `assemble_resources.py` |
| 10 | Power-aware indexing: battery / OS low-power / CPU polled every 5 s; indexing pauses between files on battery (default) and resumes from the same file when power returns; Balanced / Performance / Battery Saver modes; controls on Settings, reason on Status. Verified with a fake power source; real unplug is the human's check. 2026-09-21. | `prototype_power.py` |
| 11 | Reliability: corrupt, vanishing and unreadable files are counted and listed, never fatal; a crash mid-index is repaired on the next scan through an `indexed` flag (no rescan). 2026-09-21. | `prototype_reliability.py` |
| 12 | Security & privacy: first-run file-access screen (Allow all / limited incl. single files / Deny), per-launch API token so no other local program can read the index, path validation, document contents never executed, socket-level offline guard. 2026-09-21. | `prototype_security.py` |
| 13 | Benchmarks: BM25-only 47% vs shipped hybrid 97% Recall@5 (MRR 0.961) at 8 ms P50 / 11 ms P95 (targets 300 ms / 1 s); chunk configs measured on short and long documents; three embedding models compared (bge-small-en-v1.5 reaches 100% recall — the first improvement to make). 2026-09-21. | `evaluate_retrieval.py`, `evaluate_embeddings.py` |
| 15 | Improvements (in progress): bge-small-en-v1.5 shipped with data-derived thresholds (hybrid MRR 0.983); personalization re-measured with it (habitual file first 100%, 0/42 harmed); first launch 38 → 32 s and every later one ~2 s via lazy model imports; a learned router trained on the query log with gated activation (ties the rules at 93% on the corpus); a 30-question agent key with 5 unanswerable questions and a premise check that cut confident false answers 3 → 1 without losing a correct one. 2026-09-21. | `evaluate_learned_router.py`, `evaluate_agent.py`, `tune_semantic_thresholds.py` |

**Current test status (2026-09-21, late):** `run_all_phases.py` → 20/20 scripts pass under the offline guard. `live_test_all_phases.py` → 20 passed, 0 failed, 2 skipped (human-only: the real mic button, judging real photos — both done by hand), 0 network attempts. Desktop `tsc`, `vite build` and `cargo build` succeed; the macOS bundle is ad-hoc signed as a whole and verifies with `codesign --verify --deep --strict`. Two full code reviews and a macOS stability pass the same day found and fixed 39 issues, every one recorded in `README.md` with cause, fix and check. The UI's days of live testing by the user found and fixed 12 real bugs, including three that only a human at the real app could find: every search result rendering invisible, voice file-name prompting silently disabled for anyone with a normal number of files, and Re-index wiping a folder during the startup scan.

**Still to do:** run the Windows CI build and install it on a Windows machine (nothing has run on Windows yet — the biggest remaining risk), a clean-machine test on another Mac, a human click-through of the rebuilt app (Ask mode, Insights, Settings switches, the first-run access screen), the written report (`docs/REPORT.md`), and two weeks of real use before the demo so the personalization has real habits to show.

---

## 5. Engineering decisions worth explaining

These are the choices a reviewer is most likely to ask about.

1. **Local-first, no cloud.** The whole point is privacy and offline operation. Every model (bge-small, Whisper, CLIP, the reranker, the Qwen agent) runs on-device through ONNX Runtime / llama.cpp. There is no network code in the backend at all except the local HTTP server, and the test suites run under a socket-level offline guard that fails on any outbound attempt.

2. **Hybrid search instead of "just use embeddings".** Pure semantic search returns a confident-looking top result even when nothing is relevant, and it can't do exact phrase matching. Pure keyword search can't handle "traffic spikes" vs "demand increases". Running both and fusing with RRF gets the strengths of each. A relevance cutoff on the vector side stops unrelated queries from producing junk.

3. **Two vector tables, one abstraction.** Text (384-dim) and images (512-dim) live in separate LanceDB tables. Because the Phase 1 storage layer took the table name and dimension as parameters from the start, adding visual search in Phase 8 needed **zero** storage-layer changes.

4. **One place for platform differences.** Hardware acceleration (DirectML on Windows, CoreML on macOS, CPU fallback) is selected by a dependency marker in `requirements.txt` plus one function in `embeddings/hardware.py`. No `if windows:` branches anywhere else.

5. **Typo correction against the user's own words, not a dictionary.** A generic English dictionary would "correct" real technical terms. Using the FTS5 vocabulary of the user's actual files means corrections always point at something that exists.

6. **Verify, don't assume.** Every phase has a regression script *and* was tested against the live server; several real bugs only appeared in live testing (see §6). The README records what could and could not be tested by automation, and what needed a human.

7. **Graceful degradation.** If the Whisper or CLIP model files are missing, the app still runs text-only and the relevant endpoint reports what's missing instead of crashing.

---

## 6. Real bugs found and fixed (and what they taught us)

| Found in | Bug | Fix | Lesson |
|---|---|---|---|
| Phase 5 live test | FTS5 crashed on punctuation in queries | Sanitise/quote query terms | Live server testing catches what unit scripts don't |
| Phase 6 live test | `venv/`, `node_modules/` package `.txt` files polluted results | `EXCLUDED_DIR_NAMES` in discovery | Index hygiene is a recurring bug class |
| Full re-test 2026-09-10 | Whisper's own `merges.txt` (50k-line tokenizer file) became the #1 "semantic match" for "sourdough" | Exclude `models/` | Same class — added a permanent regression assertion |
| User's live test | `requirements.txt` indexed as a document | New `EXCLUDED_FILE_NAMES` (a directory rule can't catch a bad *file* in a good directory) | Caught by the user, not by any script |
| Phase 7 live mic | First spoken word always garbled | (a) mic audio was being played back through the speakers while recording — acoustic feedback; (b) the audio pipeline delivered 0.5–0.8 s of pure zeros before real samples — added a 700 ms warm-up | Hardware bugs are invisible to synthesized-audio tests; instrument and inspect the raw data instead of guessing twice |
| Phase 8 build | LanceDB returns *squared* L2 distance, not L2 | Re-derived the cutoff | Verify library semantics empirically |
| Phase 8 build | CLIP text tower takes no attention mask — batching with padding would silently corrupt embeddings | Encode one string at a time | Check the actual ONNX signature |
| Phase 8 real-photo test (2026-09-11) | Relevance cutoff tuned on synthetic shapes hid correct real photos ("a group of friends" returned nothing even though the top hit was the group photo) | Re-derived cutoff from measured real-photo distances (1.5 → 1.55), all numbers recorded in the code | Synthetic fixtures prove wiring, not real-world quality |
| User's live app test (2026-09-11) | **File watcher was never wired into the running app** — renamed files kept their old paths in search results ("No such file or directory" on click) | New `watch_setup.py` starts the Phase 2 watcher for every indexed folder (resumed on next launch); folder rescans now reconcile renames/deletions; search never returns a file that's gone from disk | Every module existed and passed its own test — the *integration* was missing. Only a human using the real app finds that |
| User's live app test (2026-09-11) | `.avif` images silently never indexed | Added to supported formats (Pillow decodes natively) | Test with the user's real files, not just your own fixtures |
| User's live app test (2026-09-11) | App's own 30–310 px icon PNGs indexed as "photos", polluting vague queries | Skip images under 320 px on the longest side | Index-pollution bug class again, in image form |
| User's live mic test (2026-09-11) | Voice heard as "no tablet, horizontal scaling"; and the **CoreML accelerator silently corrupted** larger Whisper models (single word / `I!!!!…`) | Whisper pinned to CPU (faster *and* correct); upgraded tiny.en → base.en after comparing all three on the user's own clips | Accelerators can be wrong, not just slow — verify output, not just speed; and pick models on the real user's voice |
| User's live mic test (2026-09-11) | "open abhisek plan" → "happy shake clan" — Whisper can't spell names it has never seen | The user's own file names are passed to Whisper as a decoding prompt ("gurtucheyatam" now transcribes correctly) | Domain vocabulary must be injected; a generic model can't know your files |
| User's re-check (2026-09-11) | Clear airliner photos ranked *below* a plane wreck for "Airplane" | Compared our ONNX pipeline to the original PyTorch CLIP: int8 quantization was distorting embeddings by up to 0.11; switched to fp16 weights (exact, +148 MB) | When rankings look wrong, test the *pipeline* against ground truth before blaming the model |
| User's live photo test (2026-09-11) | "airplane" ranked an aerial airport photo above real planes; investigating exposed that **CLIP embeddings changed with batch size and execution provider** (dynamic int8 quantization) — the tuned cutoff would have been wrong on the grading laptop | Upgraded to ViT-B/16 (measured vs B/32 and L/14); pinned CLIP to CPU with one image per run; model-change detection re-embeds automatically; cutoff re-measured on stable embeddings | Measure *determinism*, not just accuracy — a threshold is only as portable as the numbers under it |
| User's live photo test (2026-09-11) | "a person in red shirt" also returned a red pizza | Cutoff re-measured on 124 labelled photos (precision 0.54 → 0.69 at ~7 pts recall); borderline hits shown dimmed as "Possibly related" instead of mixed in | With a labelled set you can *measure* a threshold instead of guessing; when a cut costs recall, show the tier rather than hide it |
| User's live search test (2026-09-11) | Search matched *content only* — "rasmalai" found nothing though `rasmalai.txt` existed; and the spelling corrector turned "naruto" into "auto" | Filename matching ranked above content; filename words added to the correction vocabulary | The basics of the old tool (find by name) must survive in the new one |

---

## 7. Size of the codebase

(Figures from 2026-09-11; Phase 8b, the desktop UI and the word list have been added since.) ~2,850 lines of application code (Python backend + TypeScript frontend), plus ~1,800 lines of regression/test and model-download scripts. Bundled ML models as of 2026-09-21: bge-small 130 MB (+ MiniLM 87 MB fallback), CLIP B/16 fp16 300 MB, Whisper base.en 73 MB, reranker 87 MB, Qwen2.5-1.5B Q4_K_M 1.0 GB.

---

## 8. How to run it (development)

```bash
# Backend
cd backend
python3.11 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python scripts/download_embedding_model.py # bge-small-en-v1.5 (download_model.py fetches the MiniLM fallback)
venv/bin/python scripts/download_whisper_model.py  # Whisper base.en (optional — voice)
venv/bin/python scripts/download_clip_model.py     # CLIP B/16 (optional — photos)
venv/bin/python scripts/download_demo_photos.py    # 124 sample photos into ~/Desktop/IntelliFile-Search-Demo (demo only)
venv/bin/uvicorn app.main:app --port 8756

# Desktop app (separate terminal)
cd desktop
npm install
npm run tauri dev

# Tests
cd backend
venv/bin/python scripts/run_all_phases.py          # all phase regression scripts
venv/bin/python scripts/live_test_all_phases.py    # against the running server
```

The packaged version (Phase 14) will start the backend automatically as a sidecar — the user just double-clicks the app.
