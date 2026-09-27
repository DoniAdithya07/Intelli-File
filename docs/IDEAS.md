# IntelliFile, ideas for the next round

*Brainstormed 2026-09-26, after running the Windows build end to end (search, Ask mode, photo search, live folder watching). The ideas start from what was observed in that run and from the assignment's three objectives, not from a blank page.*

## The opportunity, restated

- **Users:** students, developers and researchers with thousands of files, who remember what a file was *about* but not what it was *called*.
- **Job to be done:** "get me back to that file, or the answer inside it, in seconds, without uploading anything."
- **The grader:** a professor who downloads the app, runs it on their own laptop with no help, and judges three things. Objective 1: the agent picks a strategy. Objective 2: personalization from behaviour. Objective 3: efficient routing.
- **Where it stands:** every objective is built and measured. The gaps are about quality at the edges, speed on a laptop CPU, and getting the app onto the grader's machine.

### What the live run showed

| Observed | Where |
|---|---|
| "Possibly related" listed 10 weak matches, including `camping trip.md`, for a server-capacity question | Search page |
| Ask mode took about 27 s, and cited `april invoice.txt` although only the March invoice supports the answer | Ask mode |
| The agent invented a filter (`type:invoice`); the harness caught it and retried | Ask trace |
| Personalization shows "while IntelliFile learns your habits" on day one, so it needs weeks of use before it can show anything | Search page |
| The 2.52 GB zip is over GitHub's 2 GB release-asset limit, and there is no installer | Packaging |
| Photos dropped into a watched folder were indexed about 25 s later with no rescan Done | Live watcher |

---

## Ideas from three perspectives

### Product manager: fit, value, advantage

1. **Personalization from day one, using Windows' own history.** Windows already records which files you opened, in `%APPDATA%\Microsoft\Windows\Recent` (shortcut files with timestamps) and in each app's jump list. Import that history on first run, with the user's consent, as seed activity. Objective 2 then works the minute the grader installs the app, instead of after "two weeks of real use".
2. **A two-part download: IntelliFile Lite plus an Ask pack.** The core app with search, voice and photos is about 1.4 GB and fits a GitHub Release. The 1.1 GB language model becomes an optional second download that the app detects when it is placed next to it. This removes the distribution blocker and halves the first download.
3. **A "Routing savings" meter in Insights.** A running total of embedding runs skipped, reranker calls avoided and milliseconds saved against always-hybrid, taken from the user's own query log. It turns Objective 3's measured claim (72% of the latency, embedding on 43% of queries) into something the grader can watch while using the app.
4. **A duplicates and versions report.** "You have 4 copies of `resume.docx`; the newest is in Downloads." Exact copies come from the content hashes the app already stores, and near-copies come from document vectors. This is a clear reason to keep the app installed, beyond search.
5. **"Find similar" from File Explorer.** A right-click entry, or an `intellifile://similar?path=…` link, that opens IntelliFile with files related to the selected one. Search becomes part of the tool people already use.

### Product designer: experience, onboarding, engagement

1. **Fewer, better weak results.** Show at most three "possibly related" results behind a *Show more* link, and hide the weak tier when the best match is far ahead. The camping-trip noise disappears, and the page reads as confident.
2. **A one-click sample tour on the first-run screen.** A button next to *Allow all / limited / deny*: **"Try it with sample files first."** It indexes the bundled sample folder, then walks through five queries: filename, meaning, filter, a photo and an Ask question. The grader sees every feature in two minutes without reading `HOW_TO_RUN.md`.
3. **An instant answer while Ask thinks.** Within about a second, show the best matching sentence from the top file (`…due on 14 April 2025; the amount is 1,440 euros`) labelled *"Quick answer from march invoice.txt"*. The model's answer then replaces it. The 27 s wait gets something useful at second 1.
4. **A preview pane with the passage highlighted.** Selecting a result opens a side panel showing the matching passage in context, on the right PDF page or slide, with the query words highlighted. It proves why the file matched without leaving the app.
5. **Show the plan for every search, not only Ask.** A collapsible line under each result list, for example *"Understood as: name match → skipped meaning search (saved 30 ms)"*. It uses the route data every response already carries, and makes the agent's reasoning visible on ordinary searches.

### Software engineer: technical depth, integrations, platform

1. **Trim citations with the reranker the app already has.** After the model answers, keep only sources whose best sentence the cross-encoder scores as supporting the answer, then renumber. This fixes the April over-cite, needs no new model, and can be scored against the 30-question answer key.
2. **A faster, stoppable agent.** Reuse llama.cpp's prompt cache for the fixed system prompt and tool list across planning turns, which should cut the prompt-processing time that dominates on this CPU. Also stream planning output so the time budget can stop a call mid-way. This fixes the 50 s-cap overrun seen under load.
3. **Offline text recognition with Windows' built-in engine.** Windows 10 and 11 include an offline text-recognition (OCR) API (`Windows.Media.Ocr`) that Python can call. It would index scanned PDFs, screenshots and photos of whiteboards with no new model and no extra download size, a Windows-only advantage.
4. **Measure DirectML on Windows.** `hardware.py` says DirectML is "untested for these models." Measure bge-small and CLIP on this laptop's Intel GPU against the CPU for speed *and* identical output, as was done for CoreML on the Mac. If it wins, first-time indexing of a large folder gets faster.
5. **Local evaluation with the user's own files.** Log which result the user opened for each query (the activity memory already records this). Use those pairs to re-tune the thresholds and the learned router on the user's own data, fully offline, and show *"ranking tuned on 214 of your searches"* in Insights.

---

## Top 5, prioritized

Weighted toward three things: helping the grade (the three objectives and a working download), being fast to validate with the existing evaluation scripts, and standing out.

### 1. Split download: Lite + Ask pack *(PM 2)*
**Why first:** today the grader has no clean way to get the app. It blocks everything else, and it's about an hour of work: skip `models/llm` in `package_windows.py`, add a second zip, and make the UI show "Ask needs the Ask pack" instead of failing (the backend already reports `ask/status.available`).
**Assumptions to test:** the Lite zip stays under 2 GB (expected about 1.4 GB); the app starts and searches without the model folder; Ask turns on when the pack is unzipped into `models\llm` with no restart, or with a documented one.

### 2. Personalization from day one via Windows Recent *(PM 1)*
**Why:** Objective 2 is half the brief, and today it can only be demonstrated after weeks of use. Seeding from Windows' own history makes it visible on first launch, on *the grader's* files, and no other student project will do it.
**Assumptions to test:** the Recent folder's shortcuts can be resolved to real paths from Python on Windows; their timestamps are close enough to real open times for the weekday/time-of-day patterns; importing is opt-in and shown clearly (it is personal data, even if it stays local). **Measure** with `evaluate_personalization.py` using seeded events in place of the synthetic log.

### 3. Cleaner weak results *(Designer 1)*
**Why:** it's the most visible quality problem in a demo, and it's cheap: a cap plus a gap rule in `search/service.py`.
**Assumptions to test:** a rule like "hide weak when strong exists and the gap exceeds X" can be tuned on the 60 labelled queries without losing recall@5. Report precision of what's *shown* before and after, the same method used for the photo cutoff in Phase 8.

### 4. Faster, tighter Ask: instant answer, prompt cache, trimmed citations *(Designer 3 + Engineer 1 + 2)*
**Why:** Ask is the headline "agentic" feature, and it's the slowest and least precise thing observed today. The three parts are independent and each is measurable.
**Assumptions to test:** prompt caching cuts the mean below about 15 s on this CPU (`prototype_agent.py` part B already reports the mean and max); citation trimming keeps 30/30 grounded on `evaluate_agent.py` while dropping unsupported sources; the instant answer is right often enough to show (score the top sentence against the same answer key).

### 5. Offline OCR with Windows' built-in engine *(Engineer 3)*
**Why:** the biggest content gap on a real student laptop is scanned lecture PDFs and screenshots. This covers it at zero download cost, and it's a real use of the "Windows version" decision.
**Assumptions to test:** `Windows.Media.Ocr` is reachable from the frozen Python backend (through the `winrt` packages) and works offline on a clean Windows 11; accuracy on scanned slides is good enough to find them by a phrase; the time per page fits power-aware background indexing.

### Worth doing if time allows
- **Routing savings meter** (PM 3): small UI work on data that already exists, and a strong Objective 3 demo.
- **One-click sample tour** (Designer 2): the grader's first two minutes decide a lot.

## Suggested order

1 → 3 → 4 (citations first, then the cache) → 2 → 5. The first three fix what a grader would notice in the first ten minutes. Items 2 and 5 are the new capabilities that set the project apart.
