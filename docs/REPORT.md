# IntelliFile: project report

Version 1.0.0, updated 6 October 2026. Covers the course brief "Agentic AI-based Intelligent File Recommendation". Every number names its source file or log entry.

## 1. Problem and result
Windows Explorer finds files by name; people remember what a file said. IntelliFile is an offline Windows app that finds files by content (documents, audio, photos, video, scanned pages), answers questions from the user's files with citations, and learns which files the user works with. No account, no network.

| Objective | Part | Main evidence |
|---|---|---|
| 1. Agent picks a retrieval strategy | Ask agent (Qwen2.5-1.5B, bounded tool loop) | 25/25 grounded, right file cited 24/25, correct "not found" 4/5, 1 false answer (backend/data/eval_agent.json) |
| 2. Personalization | activity memory, profile, near-tie ranking | habitual file first for 100% of 18 ambiguous searches vs 39% off; 0 of 42 other searches worse (eval_personalization.json) |
| 3. Efficient routing | adaptive router, one-step escalation, reranker | same top-5 accuracy as always-hybrid at about 76% of its time; embedding model used for 43% of queries vs 97% (eval_routing.json) |

## 2. Architecture summary
A Tauri 2 desktop app starts a Python 3.11 FastAPI engine and talks to it over 127.0.0.1 with a per-launch token. Details and diagrams: ARCHITECTURE.md.
- Indexing: watcher plus full scan at start; change detection by size, modified time, SHA-256; text extraction (Windows OCR, Whisper); 224-token passages stored in SQLite FTS5 (words) and LanceDB (bge-small vectors); CLIP vectors for photos and video keyframes in a separate table.
- Search: search/router.py picks one of five tiers (file name, metadata, keyword, hybrid, hybrid+reranker); BM25 and vectors merged by RRF; one-step escalation when nothing is confident.
- Personalization: local history (optionally Windows Recent items) becomes a profile (frequency, recency, type, topic, time of day) that reorders near-ties and feeds Ask.
- Ask: first-look search, then a local model plans up to 4 tool calls within 50 s; the code, not the model, checks that citations support the answer.

Code map: Objective 1 in agent/loop.py, agent/tools.py, agent/llm.py. Objective 2 in context/ (usage_store, profile, recommendations, windows_recent) and search/service.py (_personalize). Objective 3 in search/router.py, learned_router.py, service.py (search_routed), reranker.py.

## 3. Evaluation method
Offline, one laptop (12th-gen Core i5, 16 GB, Windows 11), INTELLIFILE_OFFLINE_GUARD=1. Scripts write backend/data/eval_*.json.
- Corpus: scripts/eval_corpus.py generates 39 documents in six life areas and 60 labelled queries (12 file name, 8 with filters, 20 keyword, 16 natural language, 10 multi-part). Metrics: hit@1, hit@5, MRR, both@5, latency, share of queries that ran the embedding model.
- Routing: evaluate_routing.py runs each query through the router and through always-hybrid on the same index.
- Personalization: evaluate_personalization.py adds "old copy" twins of 8 documents (18 ambiguous queries) and a synthetic four-week log that opens only the originals; 42 other queries measure harm.
- Ask: evaluate_agent.py, 30 questions (25 answerable, 8 need two files; 5 unanswerable where "not found" is correct).
- Photos and video: evaluate_visual_real.py (26 Windows photos, 6 clips). End to end: check_features.py (28 checks) plus a clicked-through smoke test. Suite: run_all_phases.py (28 scripts, including check_ask_cancel.py and prototype_more_formats.py added on 5 October).

## 4. Results
### 4.1 Router against a non-agentic baseline
Baseline = the same fixed pipeline for every query (correction, BM25, embedding, vector search, fusion). Source: eval_routing.json, 28 Sep.

| Pipeline | hit@1 | hit@5 | MRR | both@5 | mean ms | median ms | ran embedding |
|---|---|---|---|---|---|---|---|
| Always hybrid | 93.3% | 100% | 0.964 | 100% | 20.1 | 18.2 | 96.7% |
| Router | 93.3% | 100% | 0.964 | 100% | 15.2 | 9.9 | 43.3% |

- Identical quality; latency 24.3% lower (latency_saved), so about 76% of baseline time. Tier chosen correctly for 60/60, 0 escalations.
- Savings come from cheap tiers: file name (12 queries) 19.9 to 9.1 ms, keyword (20) 16.5 to 6.9 ms. Hybrid (26) 23.4 to 24.6 ms: no gain.
- The router does not beat the baseline on accuracy in the 28 Sep file. The 21 Sep run (dev log, Phase 20) showed a higher MRR only because two filters-only queries scored 0 for the baseline. The 72% in the old README and 77% in TEST_REPORT (27 Sep) are earlier runs of the same code; differences are timing noise.

Retrieval methods on the same 60 queries (eval_retrieval.json):

| Method | recall@5 | MRR | median ms | p95 ms |
|---|---|---|---|---|
| BM25 only | 57.5% | 0.567 | 5.7 | 7.5 |
| Vector only | 100% | 0.938 | 16.2 | 19.1 |
| BM25+vector+RRF | 100% | 0.926 | 15.9 | 18.4 |
| + file-name matching (shipped) | 100% | 0.964 | 17.1 | 21.0 |
| + reranker on every query | 100% | 0.964 | 129.6 | 213.9 |

The reranker on every query costs about 7 times the median latency and changes no result, so it is reached only by escalation. On the 10 multi-part questions: hybrid 20.0 ms, with reranker 175.6 ms, both MRR 1.0. A learned router only ties the rules (eval_learned_router.json: 77.6% right tier for both), so rules ship.

### 4.2 Personalization (eval_personalization.json, 28 Sep)
| | habitual original first | above twin | MRR of original | other 42 queries |
|---|---|---|---|---|
| Off | 38.9% (7/18) | 38.9% | 0.676 | 0 worse |
| On | 100% (18/18) | 100% | 1.000 | 0 worse |

- Each other query's rank was compared with and without personalization: none moved down.
- It reorders only results within a small score window whose passages say nearly the same thing (PERSONAL_NEAR_TIE 0.0006, PERSONAL_DUPLICATE_JACCARD 0.5 in search/service.py). Reordering every near-tie by habit pushed the right answer down on 6 of 42 ordinary queries (code comment; dev log Phase 20). Each reordered result shows its reason.
- Day one: with consent, Windows Recent shortcuts are read; in prototype_windows_recent.py a fresh profile leaves cold start at once (24 events vs threshold 20) and ranks the file in use first (dev log, improvement 1).

### 4.3 Ask agent (eval_agent.json, 5 Oct; dev log)
| Measure (30 questions) | Result |
|---|---|
| Grounded | 25/25 |
| Right file cited | 24/25 (96%) |
| Correct "not found" | 4/5 |
| False answers / extra citations | 1 / 0 |
| Figure warnings | 1 |
| Mean / slowest | 15.1 s / 33.3 s measured; earlier run showed 24.2 s / 42.6 s on a busy laptop |

- The model chooses to search again with new words, mode (keyword or smart) or filters, read more, or answer, and can split a two-part question. 10/10 traces on the 10-question key showed a strategy choice (dev log, Phase 19).
- The code decides which sources support the answer, which citations stay, whether every number is in the sources and whether the question's premise is in the files. This took a 1.5B model from 1/10 to 10/10 on the first key.
- Retrieval-only baseline: the first-look search's closest sentence came from the right file for 21/25 questions, never a wrong file (quick_answer_right_file). The agent raises correct citation to 23/25 and adds a written answer, costing seconds instead of milliseconds.
- Speed: JSON-grammar sampling was the cost (9.1 s vs 2.0 s per planning call). Dropping it, adding the first look and stopping planning at 1.3 x budget cut mean 20.5 s to 12.9 s, slowest 36.8 s to 25.6 s (dev log, improvement 3, 26 Sep). Later, Ask took 56.3 s and 51.7 s in the user's own logs (past the 50 s cap); it now reads at most 6 sources.

### 4.4 Supporting results
| Feature | Result | Source |
|---|---|---|
| Search speed | p95 21 ms (limit 1 s) | eval_retrieval.json |
| Weak results | noise per query 2.25 to 0.18, 0 correct files lost | dev log, improvement 2 |
| Offline OCR | 4/4 pixel-only phrases found | dev log, improvement 4 |
| Real photos (26) | first 50%, top 3 78% | TEST_REPORT |
| Video (6) | video 6/6, video and moment 5/6 | TEST_REPORT |
| Feature check, packaged app | 28/28, 0 network attempts | dev log, 6 Oct |
| Smoke test, packaged app | 17/17, 0 console errors | dev log, 28 Sep |
| Full suite | 28 of 28 pass (6 Oct); 27 of 27 on 5 Oct, 26 of 26 on 30 Sep | TEST_REPORT |

### 4.5 Scale test

On 5 October, a scale test on 1,039 files (39 labelled documents and 60 queries among 1,000 filler files) was run over HTTP. Indexing reached 6.8 files/second (1,000 files in 2.5 minutes). Search on the 39 labelled files: median latency 44 ms (P95 104 ms), MRR 0.939 (vs. 0.964 on just 39 files), top-5 accuracy 60/60 (100%). Backend memory: 1.3 to 1.9 GB. A 3,000-file stage was not run. The "search gets slower" limit in TEST_REPORT is now replaced by these numbers.

## 5. Limitations
- Synthetic data: 39 generated short documents and 60 developer-written queries prove the mechanisms and are reproducible, but do not prove the same accuracy on a real messy collection. Small corpora are easy (hybrid reaches 100% recall@5).
- Synthetic activity log: personalization was tested on a generated four-week history, no real users. Real-world benefit is unmeasured.
- Router gain is on cheap tiers only (32 of 60 queries). A workload of only natural-language questions would save nothing; hybrid queries cost slightly more with the router.
- The router's rules were tuned on the same kind of labelled queries they are scored on, so 60/60 tier match is optimistic.
- Small model: Qwen2.5-1.5B can state a non-numeric fact wrongly while words overlap a source (a trip "on Wednesday" for 12 May); the figure check cannot catch that. 2 of 25 answerable questions cited the wrong file.
- Ask takes 20 to 45 s on a busy laptop (about 14 s free); hard cap 50 s, exceeded once before the fix.
- Photo search on near-identical pictures is weak (first 50% on 26 real photos); the limit is CLIP, not the code.
- All timings are from one laptop.
- Flaky: the power test failed once under memory load (60 s wait, not lengthened); the feature check failed once at backend start right after fresh extraction and passed on rerun.
- Open manual checks: voice with a real microphone; unplugging the laptop while indexing (only a fake power source tested); Ctrl+Space from another program; the zip on a second, clean Windows PC (never tried; only a fresh extraction on the development PC).
- Not built: no installer (over the 2 GB limit of NSIS and MSI), so a portable zip; GitHub build workflow never run; macOS code present but unbuilt.
- UI: photo page has no side preview with the picture's text; screens have no own Help links.
- Security: see section 6 for the review and its fixes. The offline guard is a test tool; the shipped app is offline because it has no network code.

## 6. Security review
A read-only review on 29 September 2026 found no critical or high issues: the engine listens on 127.0.0.1 only, no endpoint takes a raw path, the Ask agent can only read the index, no user file is ever changed and no network client exists. It found two medium and three low issues (open development server, wide open-path permission, no content security policy, token in thumbnail URLs, weak token source). These were then fixed; the changes are listed in DEVELOPMENT_LOG.md.

## 7. How to reproduce
Source build needs Windows, Python 3.11, Node.js, Rust (docs/BUILD_WINDOWS.md). To only run: extract IntelliFile-windows.zip, start IntelliFile\IntelliFile.exe.
```powershell
cd backend
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
.\venv\Scripts\python.exe scripts\download_embedding_model.py   # and the other download_*.py (internet, developer machine only)
$env:INTELLIFILE_OFFLINE_GUARD = "1"
.\venv\Scripts\python.exe scripts\evaluate_retrieval.py
.\venv\Scripts\python.exe scripts\evaluate_routing.py
.\venv\Scripts\python.exe scripts\evaluate_personalization.py
.\venv\Scripts\python.exe scripts\evaluate_agent.py             # needs the Qwen model
.\venv\Scripts\python.exe scripts\evaluate_visual_real.py
.\venv\Scripts\python.exe scripts\check_features.py             # add --packaged <unzipped folder> for the release
.\venv\Scripts\python.exe scripts\run_all_phases.py             # all 26 scripts
```
Latencies vary with hardware and run; accuracy on the generated corpus is deterministic.

## 8. Conclusion
All three objectives are built, measured and demonstrable offline. Strongest evidence: routing (same accuracy, lower cost on cheap queries) and grounding (correct "not found" on 4 of 5 unanswerable questions, 1 false answer on "When does my gym membership expire?" answered from the monthly expenses sheet). Weakest: personalization, shown on a synthetic history only. Main open risk: the zip has not run on a clean second PC.
