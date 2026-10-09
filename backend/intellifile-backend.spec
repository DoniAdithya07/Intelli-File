# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the IntelliFile backend (Phase 14).

Builds a ONEDIR bundle: dist/intellifile-backend/ with the executable and
its _internal/ libraries. Models and data are NOT embedded — they are
copied next to it by scripts/assemble_resources.py and found through
INTELLIFILE_MODELS_DIR / INTELLIFILE_DATA_DIR (see app/paths.py) — so a
model upgrade never means rebuilding the Python bundle.

Run (from backend/, in the clean build venv):
    venv-build/bin/pyinstaller --noconfirm intellifile-backend.spec
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

hiddenimports = []
datas = []
binaries = []

# Packages whose data files / native libraries / lazy imports PyInstaller's
# static analysis misses. Each one was found by running the frozen bundle
# and reading the ImportError, or is a known-lazy loader.
# pi_heif (HEIC photos, 2026-10-05): a native module plus libheif/libde265 DLLs and their licence file.
# Licensing: pi_heif's DLLs are LGPL and carry no x265, but "av" brings FFmpeg 8.1.2 with the GPL
# x264/x265 encoders (av.libs/). The bundle is therefore NOT free of GPL code; the GPL/LGPL texts
# ship in licenses/ next to IntelliFile.exe (scripts/package_windows.py). pillow_heif (dev-only,
# links its own x265) is excluded below.
for pkg in ("onnxruntime", "lancedb", "pyarrow", "av", "tokenizers", "llama_cpp", "watchdog", "pypdf", "docx", "pptx", "openpyxl", "PIL", "pi_heif"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Windows' own OCR + PDF rendering (next-round improvement 4): the pywinrt
# projections are a namespace package of native modules imported lazily.
try:
    d, b, h = collect_all("winrt")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:
    pass  # not on Windows: OCR is simply off

# transformers is only used for the Whisper feature extractor (numpy path);
# collect the package but keep torch out.
hiddenimports += collect_submodules("transformers.models.whisper")
hiddenimports += ["transformers.models.whisper.feature_extraction_whisper", "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "multipart"]
datas += collect_data_files("transformers", includes=["**/*.json"])

a = Analysis(
    ["run_backend.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "torchvision", "torchaudio", "optimum", "tensorflow", "jax", "flax", "tkinter", "matplotlib", "IPython", "notebook", "pytest", "hf_xet", "pillow_heif"],  # hf_xet only speeds up model downloads, never used offline
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="intellifile-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # the shell reads its stdout/stderr; no window is shown on Windows (CREATE_NO_WINDOW)
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="intellifile-backend")
