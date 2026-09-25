# Building IntelliFile on Windows

The steps that turn this repository into `IntelliFile.exe` on a Windows 10/11 PC. `.github/workflows/windows-build.yml` runs the same steps on GitHub's Windows runner.

Commands are for **PowerShell**, run from the repository root unless a step says otherwise.

## 1. Tools (once per PC)

```powershell
winget install --id Python.Python.3.11 -e --scope user
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
winget install --id OpenJS.NodeJS.LTS -e     # skip if `node --version` already works
rustup default stable
```

Open a new terminal afterwards so `cargo` and `py` are on the PATH. WebView2 ships with Windows 11 (and current Windows 10), so Tauri needs nothing else.

## 2. Backend environment

```powershell
cd backend
py -3.11 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt pyinstaller --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

The extra index gives `llama-cpp-python` as a prebuilt CPU wheel, so no C++ compile is needed. `onnxruntime-directml` is picked automatically on Windows (see `requirements.txt`).

## 3. Models (~1.7 GB, the only step that needs the internet)

Skip any model that is already in `backend/models/`.

```powershell
.\venv\Scripts\python.exe scripts\download_embedding_model.py
.\venv\Scripts\python.exe scripts\tune_semantic_thresholds.py bge-small-en-v1.5
.\venv\Scripts\python.exe scripts\download_whisper_model.py
.\venv\Scripts\python.exe scripts\download_clip_model.py
.\venv\Scripts\python.exe scripts\download_reranker_model.py
.\venv\Scripts\python.exe scripts\download_llm_model.py
.\venv\Scripts\python.exe scripts\download_wordlist.py
```

## 4. Run it in development

Terminal 1:

```powershell
cd backend
$env:INTELLIFILE_OFFLINE_GUARD = "1"
.\venv\Scripts\python.exe -m uvicorn app.main:app --port 8756
```

Terminal 2:

```powershell
cd desktop
npm install
npm run tauri dev
```

## 5. Tests

```powershell
cd backend
$env:INTELLIFILE_OFFLINE_GUARD = "1"; $env:PYTHONIOENCODING = "utf-8"
.\venv\Scripts\python.exe scripts\run_all_phases.py          # all phase regression scripts
.\venv\Scripts\python.exe scripts\live_test_all_phases.py    # against the dev server from step 4
```

## 6. Build the app

```powershell
cd backend
.\venv\Scripts\python.exe -m PyInstaller --noconfirm intellifile-backend.spec
.\venv\Scripts\python.exe scripts\assemble_resources.py
cd ..\desktop
npm run tauri build
```

The output is under `desktop\src-tauri\target\release\`. See `docs/HOW_TO_RUN.md` for what to hand to a user.
