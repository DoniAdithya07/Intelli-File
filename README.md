# IntelliFile

Find a file on your Windows PC by describing what is in it. IntelliFile searches the contents of your documents, spoken audio, photos and scanned pages, answers questions from your own files with citations, and learns which files you use. It runs entirely on your computer: no account, no internet, nothing uploaded.

![IntelliFile search results, day theme](docs/screenshots/ui/day-search-results.png)

![The same search in the night theme](docs/screenshots/ui/night-search-results.png)

## What it does

- **Search by meaning or by words.** "notes about handling traffic spikes" finds `scaling_notes.md` even though the file never uses those words. Misspellings are corrected against the words in your own files.
- **Ask a question.** Start a search with `?` and a local language model reads the best passages and answers in a sentence or two, citing the files it used. The closest passage appears immediately while the answer is written.
- **Choose the cheapest search that works.** Each query is routed to file-name, metadata, keyword or hybrid search, with re-ranking only when needed. On the labelled test set this keeps full hybrid quality at 72% of the time.
- **Learn how you work.** Files you open often, at a given time of day, of your usual types and topics rank a little higher, and each result says why. Insights shows what has been learned. Optionally, it can start from Windows' own Recent items list.
- **Read more than text.** PDF, Word, Excel, PowerPoint, text, Markdown, CSV, HTML, code, audio (transcribed), photos and video (searched by description), and scanned PDFs and screenshots (read with Windows' built-in text recognition).
- **Looks like Windows.** Designed after Windows 11 File Explorer, in Day and Night; follow Windows or pick one in Settings or with the sidebar button.
- **Stay out of the way.** Folders are watched for changes, indexing pauses on battery, broken files are skipped and listed, and the quick-search window opens anywhere with Ctrl+Space.

## Download and run

IntelliFile comes as one zip file, `IntelliFile-windows.zip` (1.87 GB), with everything in it, including the Ask model.

1. Download `IntelliFile-windows.zip` from the releases page.
2. Extract both to the same place, for example your Desktop. Each holds an `IntelliFile` folder and they merge.
3. Open the `IntelliFile` folder and run `IntelliFile.exe`.

Requirements: Windows 10 or 11 (64-bit), about 3 GB of disk space, 8 GB of memory (16 GB recommended for Ask mode). No administrator rights are needed. Step-by-step instructions, including what to try first, are in [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md).

## How it works

A Tauri desktop app starts a Python search engine on the same computer and talks to it over `127.0.0.1` with a key that changes at every start. The engine indexes each passage twice, by its words (SQLite FTS5) and by its meaning (bge-small vectors in LanceDB), and merges both rankings. A router picks the cheapest search for each query; a local Qwen2.5 model answers questions from the retrieved passages; the code, not the model, checks that every answer is supported by the files it cites.

The full design, with diagrams, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Build from source

Windows, Python 3.11, Node.js and Rust are needed. The complete steps are in [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md). In short:

```powershell
cd backend
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
.\venv\Scripts\python.exe scripts\download_embedding_model.py   # and the other download_*.py scripts
.\venv\Scripts\python.exe scripts\run_all_phases.py             # the full test suite, offline
cd ..\desktop
npm install
npm run tauri dev
```

The model download scripts are the only part of the project that uses the internet, and only on a developer's machine. The built app never connects to anything.

## Testing and measurements

Every feature has a script under `backend/scripts/` that exercises it end to end on real files. `run_all_phases.py` runs them all under a guard that fails on any network connection. Retrieval, routing, personalization, the agent and each improvement are measured on a generated, labelled corpus; the numbers and the history of every bug fix are in [docs/DEVELOPMENT_LOG.md](docs/DEVELOPMENT_LOG.md).

## Privacy

IntelliFile reads only the folders you allow, never changes your files, and keeps its index and history in `%LOCALAPPDATA%\IntelliFile`. See [PRIVACY.md](PRIVACY.md) and [TERMS.md](TERMS.md); both are also in the app under Settings.

## Licence

MIT, see [LICENSE](LICENSE). The models, fonts, word list and libraries keep their own licences, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).
