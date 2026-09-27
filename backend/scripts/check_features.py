"""End-to-end feature check: does IntelliFile identify files, photos and
videos correctly? Run it before every release or push.

It builds a small test folder of known content, starts a real backend on
it (the source backend, or the packaged one with --packaged), and asks the
same HTTP API the desktop app uses. Every check prints what it expected,
what came back, and PASS or FAIL.

    file identification   meaning, exact words, misspelled file names, every
                          document format, type filters, the preview passage
    photo identification  a described picture finds it; nonsense finds nothing;
                          words inside a screenshot are found (Windows OCR)
    video identification  a described scene finds the video and the right
                          moment; the frame at that moment can be shown
    audio identification  words spoken in a recording find it (when Windows'
                          speech engine is available to make the recording)
    safety                0 network attempts (offline guard on) and the test
                          files are byte-identical afterwards (read-only)

Your own index is never touched: the backend runs on a throwaway data
folder with its own API token, and everything is deleted afterwards.

Run (from backend/):
    venv\\Scripts\\python.exe scripts\\check_features.py
    venv\\Scripts\\python.exe scripts\\check_features.py --packaged ..\\desktop\\src-tauri\\target\\release\\IntelliFile
Exit code 0 means every check passed. A JSON report is written with --report.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

RESULTS: list[dict] = []


def check(group: str, name: str, passed: bool, evidence: str) -> None:
    RESULTS.append({"group": group, "check": name, "result": "PASS" if passed else "FAIL", "evidence": evidence})
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {evidence}", flush=True)


def skip(group: str, name: str, reason: str) -> None:
    RESULTS.append({"group": group, "check": name, "result": "SKIP", "evidence": reason})
    print(f"  [SKIP] {name}: {reason}", flush=True)


# ---------------------------------------------------------------- test folder

DOCUMENTS = {
    "scaling_notes.md": "# System design notes\n\nHorizontal scaling allows additional server instances to be provisioned when traffic demand increases, distributed by a load balancer.\n",
    "march invoice.txt": "Invoice 2025-03: consulting services, 12 hours. Payment terms net 30, so it is due on 14 April 2025; the amount is 1,440 euros.\n",
    "sourdough.md": "Sourdough bread: mix flour, water, salt and starter, let it rise overnight, bake at high heat.\n",
}
# One unusual phrase per format, so a hit can only come from reading that file.
FORMAT_PHRASES = {
    "turbine manual.pdf": "zephyr turbine maintenance schedule",
    "cake recipe.docx": "lemon drizzle cake with poppy seeds",
    "roadmap.pptx": "quarterly roadmap milestones for the aquarium project",
    "garden log.csv": "hydroponic basil yield",
}
SHAPES = {"red circle.png": ("red", "circle"), "blue square.png": ("blue", "square"), "green triangle.png": ("green", "triangle")}
# At least 5 words: IntelliFile indexes an image's text only from OCR_MIN_WORDS
# (app/indexing/indexer.py) words, so a photo with one stray sign stays a photo.
SCREENSHOT_TEXT = "Payment received for invoice 4471 from Northwind Traders"
SPOKEN_TEXT = "The quarterly planning meeting has moved to Thursday afternoon."


def draw_shape(path: Path, colour: str, shape: str) -> None:
    img = Image.new("RGB", (640, 480), (245, 245, 240))
    d = ImageDraw.Draw(img)
    if shape == "circle":
        d.ellipse((170, 90, 470, 390), fill=colour)
    elif shape == "square":
        d.rectangle((170, 90, 470, 390), fill=colour)
    else:
        d.polygon([(320, 80), (120, 400), (520, 400)], fill=colour)
    img.save(path)


def make_folder(root: Path) -> dict:
    """Writes the test folder; returns what was made, for the checks."""
    docs, photos, videos, audio = root / "documents", root / "photos", root / "videos", root / "audio"
    for d in (docs, photos, videos):
        d.mkdir(parents=True)
    for name, text in DOCUMENTS.items():
        (docs / name).write_text(text, encoding="utf-8")

    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=14)
    pdf.multi_cell(0, 10, f"Plant operations. The {FORMAT_PHRASES['turbine manual.pdf']} is reviewed every spring.")
    pdf.output(str(docs / "turbine manual.pdf"))
    import docx
    document = docx.Document()
    document.add_paragraph(f"Weekend baking: {FORMAT_PHRASES['cake recipe.docx']}, served cold.")
    document.save(str(docs / "cake recipe.docx"))
    import pptx
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Planning"
    slide.placeholders[1].text = FORMAT_PHRASES["roadmap.pptx"]
    deck.save(str(docs / "roadmap.pptx"))
    (docs / "garden log.csv").write_text(f"week,crop,note\n1,basil,{FORMAT_PHRASES['garden log.csv']} doubled\n", encoding="utf-8")

    for name, (colour, shape) in SHAPES.items():
        draw_shape(photos / name, colour, shape)
    shot = Image.new("RGB", (1900, 300), "white")
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(shot).text((40, 110), SCREENSHOT_TEXT, fill="black", font=font)
    shot.save(photos / "receipt screenshot.png")

    import av
    frames = []
    for colour, shape in (("red", "circle"), ("blue", "square")):
        img = Image.new("RGB", (640, 360), "white")
        d = ImageDraw.Draw(img)
        (d.ellipse if shape == "circle" else d.rectangle)([200, 40, 440, 320], fill=colour)
        frames.append(img)
    with av.open(str(videos / "shapes clip.mp4"), mode="w") as container:  # red circle for 3 s, then a blue square for 3 s
        stream = container.add_stream("libx264", rate=2)
        stream.width, stream.height, stream.pix_fmt = 640, 360, "yuv420p"
        stream.options = {"g": "1"}
        for img in frames:
            for _ in range(6):
                for packet in stream.encode(av.VideoFrame.from_image(img)):
                    container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    made_audio = False
    try:
        import speech_synth
        if speech_synth.available():
            audio.mkdir()
            speech_synth.synthesize_wav(SPOKEN_TEXT, audio / "meeting note.wav")
            made_audio = (audio / "meeting note.wav").stat().st_size > 10_000
    except Exception as e:  # no speech engine: the audio check is skipped, not failed
        print(f"  (no test recording: {type(e).__name__}: {e})")
    n_docs = len(DOCUMENTS) + len(FORMAT_PHRASES)
    return {"documents": n_docs, "photos": len(SHAPES) + 1, "videos": 1, "audio": 1 if made_audio else 0}


def fingerprint(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------------------------------------------------------- backend

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    def __init__(self, packaged: Path | None, data_dir: Path):
        self.port, self.token = free_port(), secrets.token_hex(16)
        env = {**os.environ, "INTELLIFILE_OFFLINE_GUARD": "1", "INTELLIFILE_APP_DATA_DIR": str(data_dir),
               "INTELLIFILE_API_TOKEN": self.token, "PYTHONIOENCODING": "utf-8"}
        env.pop("INTELLIFILE_PARENT_PID", None)
        if packaged:
            exe = packaged / "backend" / "intellifile-backend.exe"
            cmd, cwd = [str(exe), "--port", str(self.port)], exe.parent
        else:
            cmd, cwd = [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port)], BACKEND
        self.log = open(data_dir.parent / "backend.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        self.url = f"http://127.0.0.1:{self.port}"

    def get(self, endpoint: str, **params):
        return requests.get(self.url + endpoint, params=params, headers={"X-IntelliFile-Token": self.token}, timeout=120)

    def post(self, endpoint: str, body: dict):
        return requests.post(self.url + endpoint, json=body, headers={"X-IntelliFile-Token": self.token}, timeout=120)

    def wait_healthy(self, timeout: float = 180) -> float:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited with code {self.proc.returncode}")
            try:
                if requests.get(self.url + "/health", timeout=2).ok:
                    return time.time() - t0
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"backend not healthy after {timeout:.0f} s")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def wait_indexed(b: Backend, expected_files: int, timeout: float = 600) -> dict:
    t0, status = time.time(), {}
    while time.time() - t0 < timeout:
        status = b.get("/status").json()
        job = status["job"]
        if job["state"] != "running" and not job["queued"] and status["totals"]["files"] >= expected_files:
            return status
        time.sleep(1)
    return status


# ---------------------------------------------------------------- checks

def top_names(results: list[dict], n: int = 3) -> list[str]:
    return [r["filename"] for r in results[:n]]


def run_checks(b: Backend, made: dict, status: dict) -> None:
    totals = status.get("totals", {})
    print("\nIndexing")
    for kind in ("documents", "photos", "videos", "audio"):
        if kind == "audio" and not made["audio"]:
            continue
        check("indexing", f"{kind} indexed", totals.get(kind) == made[kind], f"expected {made[kind]}, index reports {totals.get(kind)}")
    failures = status.get("job", {}).get("recent_failures") or []
    check("indexing", "no file failed to index", not failures, f"{len(failures)} failures" + (f": {failures[0]['path']} ({failures[0]['error'][:80]})" if failures else ""))

    def search(q: str, **extra) -> list[dict]:
        return b.get("/search", q=q, top_k=10, remember="false", **extra).json().get("results", [])

    print("\nFile identification")
    r = search("how do we add capacity when lots of visitors arrive")
    check("files", "found by meaning (no shared words)", bool(r) and r[0]["filename"] == "scaling_notes.md", f"top results {top_names(r)}")
    if r:
        p = b.get("/passages", file_id=r[0]["file_id"], chunk_id=r[0].get("chunk_id") or "").json()
        text = (p.get("match") or {}).get("text", "")
        check("files", "preview shows the matching passage", "load balancer" in text, f"passage: {text[:70]!r}")
    r = search('"payment terms net 30"')
    check("files", "found by exact words", bool(r) and r[0]["filename"] == "march invoice.txt", f"top results {top_names(r)}")
    r = search("march invoise")
    check("files", "found by a misspelled file name", bool(r) and r[0]["filename"] == "march invoice.txt", f"top results {top_names(r)}")
    for name, phrase in FORMAT_PHRASES.items():
        r = search(phrase)
        fmt = name.rsplit(".", 1)[1].upper()
        check("files", f"{fmt} read by its content", bool(r) and r[0]["filename"] == name, f"'{phrase}' -> {top_names(r)}")
    r = search("type:pdf turbine maintenance")
    check("files", "type filter keeps only that type", bool(r) and all(x["filename"].endswith(".pdf") for x in r), f"type:pdf -> {top_names(r, 5)}")
    r = search("zqxv wlorbt fnarp")
    strong = [x["filename"] for x in r if x.get("confidence") == "strong"]
    check("files", "nonsense finds no confident match", not strong, f"strong matches: {strong or 'none'}")

    print("\nPhoto identification")
    if status.get("models", {}).get("photos") is None:
        skip("photos", "photo search", "the photo model (CLIP) is not installed")
    else:
        for name, (colour, shape) in SHAPES.items():
            q = f"a {colour} {shape}"
            v = b.get("/search-visual", q=q, kind="photo").json()
            res = v.get("results", [])
            ok = bool(res) and res[0]["filename"] == name and res[0].get("confidence") == "strong"
            check("photos", f"'{q}' finds {name}", ok, f"top {[(x['filename'], x.get('confidence')) for x in res[:3]]}")
        v = b.get("/search-visual", q="zqxv wlorbt", kind="photo").json()
        check("photos", "nonsense description finds nothing", not v.get("results"), f"results {len(v.get('results', []))}, unrecognized {v.get('unrecognized')}")
    if not status.get("models", {}).get("ocr"):
        skip("photos", "words inside a screenshot (OCR)", "Windows OCR is not available on this PC")
    else:
        r = search("payment received northwind traders")
        check("photos", "words inside a screenshot are found (OCR)", any(x["filename"] == "receipt screenshot.png" for x in r[:3]), f"top results {top_names(r)}")

    print("\nVideo identification")
    if status.get("models", {}).get("photos") is None:
        skip("videos", "video search", "the photo model (CLIP) is not installed")
    else:
        moments = {}
        for q, window, label in (("a red circle", (0, 3), "first scene (0-3 s)"), ("a blue square", (3, 6), "second scene (3-6 s)")):
            res = b.get("/search-visual", q=q, kind="video").json().get("results", [])
            hit = next((x for x in res if x["filename"] == "shapes clip.mp4"), None)
            t = hit.get("timestamp_offset_seconds") if hit else None
            moments[q] = t
            check("videos", f"'{q}' finds the video", hit is not None and res[0]["filename"] == "shapes clip.mp4", f"top {top_names(res)}")
            check("videos", f"'{q}' points to the {label}", t is not None and window[0] <= t < window[1], f"matched moment {t} s")
        video = next((x for x in b.get("/search-visual", q="a blue square", kind="video").json().get("results", []) if x["filename"] == "shapes clip.mp4"), None)
        if video and video.get("path"):
            th = b.get("/thumbnail", path=video["path"], size=160, t=moments.get("a blue square") or 0)
            check("videos", "frame at the matched moment can be shown", th.ok and th.headers.get("content-type", "").startswith("image/"), f"HTTP {th.status_code}, {th.headers.get('content-type')}, {len(th.content)} bytes")

    print("\nAudio identification")
    if not made["audio"]:
        skip("audio", "spoken words in a recording", "no speech engine to make a test recording")
    elif status.get("models", {}).get("speech") is None:
        skip("audio", "spoken words in a recording", "the speech model (Whisper) is not installed")
    else:
        r = search("quarterly planning meeting thursday")
        check("audio", "spoken words find the recording", any(x["filename"] == "meeting note.wav" for x in r[:3]), f"top results {top_names(r)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--packaged", type=Path, help="the unzipped IntelliFile folder (holds backend/intellifile-backend.exe)")
    parser.add_argument("--report", type=Path, help="write the results as JSON here")
    parser.add_argument("--keep", action="store_true", help="keep the temporary test folder for inspection")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="intellifile_feature_check_"))
    files, data = tmp / "test files", tmp / "data"
    data.mkdir()
    print(f"IntelliFile feature check ({'packaged: ' + str(args.packaged) if args.packaged else 'source backend'})")
    print(f"Test folder: {files}")
    made = make_folder(files)
    before = fingerprint(files)
    backend = Backend(args.packaged, data)
    try:
        started = backend.wait_healthy()
        print(f"Backend ready in {started:.1f} s on port {backend.port}")
        a = backend.post("/access", {"mode": "limited"}).json()
        q = backend.post("/index-folder", {"folder": str(files)}).json()
        if a.get("error") or q.get("error"):
            check("indexing", "folder accepted for indexing", False, f"access: {a.get('error')}, index: {q.get('error')}")
        t0 = time.time()
        status = wait_indexed(backend, sum(made.values()))
        print(f"Indexed in {time.time() - t0:.1f} s")
        run_checks(backend, made, status)
        print("\nSafety")
        guard = backend.get("/offline-guard").json()
        check("safety", "offline guard on, 0 network attempts", guard.get("enabled") is True and guard.get("blocked_attempts") == 0, f"enabled={guard.get('enabled')}, blocked attempts={guard.get('blocked_attempts')}")
        after = fingerprint(files)
        changed = [k for k in before if before[k] != after.get(k)]
        check("safety", "test files unchanged (read-only)", not changed and set(before) == set(after), f"{len(before)} files, {len(changed)} changed")
    except Exception as e:
        check("run", "backend started and answered", False, f"{type(e).__name__}: {e}")
    finally:
        backend.stop()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(r["result"] == "PASS" for r in RESULTS)
    failed = [r for r in RESULTS if r["result"] == "FAIL"]
    skipped = sum(r["result"] == "SKIP" for r in RESULTS)
    print(f"\n{passed} passed, {len(failed)} failed, {skipped} skipped")
    for r in failed:
        print(f"  FAILED: {r['group']}: {r['check']} ({r['evidence']})")
    if args.report:
        args.report.write_text(json.dumps({"passed": passed, "failed": len(failed), "skipped": skipped, "results": RESULTS}, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
