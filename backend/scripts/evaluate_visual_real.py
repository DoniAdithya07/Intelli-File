"""Photo and video search on real pictures, measured.

The feature check proves the wiring with drawn shapes; this measures
accuracy on real photographs: the 26 pictures every Windows PC ships in
C:\\Windows\\Web (lakes, mountains, sunsets, cherry blossoms and abstract 3D
art, several of them near-duplicates, which makes it hard). They are copied
into .local/tmp, never changed, and a real backend indexes them.

Photos: 18 plain descriptions, each with the pictures that correctly answer
it (written by looking at every picture). Videos: two slideshow videos made
from these pictures, 4 s per scene; a description must find the right video
and point at the right scene.

    top-1   the first result is a correct picture
    top-3   a correct picture is among the first three
    strong  the first result is marked "Strong match" and is correct

Run (from backend/):  venv\\Scripts\\python.exe scripts\\evaluate_visual_real.py
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_features import Backend  # noqa: E402  the same throwaway backend the feature check uses

REPO = Path(__file__).resolve().parents[2]
WEB = Path(r"C:\Windows\Web")

# name in the test folder -> source under C:\Windows\Web
PHOTO_QUERIES = {
    "sunset over a lake": {"screen-img102.jpg", "themec-img30.jpg"},
    "a calm lake with mountains and a forest": {"themec-img28.jpg", "themec-img29.jpg", "themec-img30.jpg", "screen-img102.jpg"},
    "snowy mountains with pine trees next to a lake": {"themec-img29.jpg", "themec-img28.jpg"},
    "a pale pink desert landscape with a lake": {"themec-img31.jpg"},
    "cherry blossom branch": {"spotlight-img50.jpg"},
    "a beach, a blossom and a lake side by side": {"spotlight-img50.jpg"},
    "a plain solid blue background": {"screen-img105.jpg"},
    "a blue flower shape on a light background": {"windows-img0.jpg", "spotlight-img14.jpg"},
    "a blue flower shape on a dark background": {"windows-img19.jpg"},
    "a soft pink flower": {"themed-img34.jpg"},
    "a sage green folded shape": {"themed-img33.jpg"},
    "a glowing purple ring on black": {"themea-img20.jpg", "themea-img21.jpg", "screen-img101.jpg"},
    "a glowing green ring on black": {"themea-img23.jpg"},
    "a red and orange glowing arc": {"themea-img22.jpg", "themea-img20.jpg"},
    "colourful ribbons on a black background": {"themeb-img24.jpg", "themeb-img25.jpg", "themeb-img26.jpg", "themeb-img27.jpg", "screen-img103.jpg"},
    "orange glass bubbles": {"themeb-img27.jpg"},
    "abstract blue waves": {"screen-img100.jpg"},
    "soft grey and white waves": {"screen-img104.jpg"},
}
# video name -> scenes in order (4 s each); query -> (video, scene index)
VIDEOS = {
    "nature trip.mp4": ["themec-img29.jpg", "spotlight-img50.jpg", "screen-img102.jpg"],
    "wallpaper reel.mp4": ["windows-img19.jpg", "themeb-img27.jpg", "themed-img34.jpg"],
}
VIDEO_QUERIES = {
    "snowy mountains with pine trees next to a lake": ("nature trip.mp4", 0),
    "cherry blossom branch": ("nature trip.mp4", 1),
    "sunset over a lake": ("nature trip.mp4", 2),
    "a blue flower shape on a dark background": ("wallpaper reel.mp4", 0),
    "orange glass bubbles": ("wallpaper reel.mp4", 1),
    "a soft pink flower": ("wallpaper reel.mp4", 2),
}
SCENE_SECONDS = 4


def make_folder(root: Path) -> None:
    photos, videos = root / "photos", root / "videos"
    photos.mkdir(parents=True)
    videos.mkdir()
    for src in sorted(WEB.glob("Screen/*.jpg")) + sorted(WEB.glob("Wallpaper/**/*.jpg")):
        shutil.copy2(src, photos / f"{src.parent.name}-{src.name}".lower())
    import av
    from PIL import Image
    for video, scenes in VIDEOS.items():
        with av.open(str(videos / video), mode="w") as container:
            stream = container.add_stream("libx264", rate=2)
            stream.width, stream.height, stream.pix_fmt = 960, 540, "yuv420p"
            stream.options = {"g": "1"}
            for scene in scenes:
                frame = Image.open(photos / scene).convert("RGB").resize((960, 540))
                for _ in range(SCENE_SECONDS * 2):
                    for packet in stream.encode(av.VideoFrame.from_image(frame)):
                        container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)


def main() -> int:
    if not WEB.exists():
        print("Windows only: C:\\Windows\\Web not found; skipped.")
        return 0
    work = REPO / ".local" / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="visual_real_", dir=work))
    files, data = tmp / "files", tmp / "data"
    data.mkdir()
    make_folder(files)
    backend = Backend(None, data)
    report: dict = {"photos": [], "videos": []}
    try:
        backend.wait_healthy()
        backend.post("/access", {"mode": "limited"})
        backend.post("/index-folder", {"folder": str(files)})
        t0 = time.time()
        while time.time() - t0 < 600:
            s = backend.get("/status").json()
            if s["job"]["state"] != "running" and not s["job"]["queued"] and s["totals"]["photos"] >= 26 and s["totals"]["videos"] >= 2:
                break
            time.sleep(1)
        print(f"Indexed {s['totals']['photos']} photos and {s['totals']['videos']} videos in {time.time() - t0:.0f} s\n")

        top1 = top3 = strong = 0
        print("Photos")
        for q, correct in PHOTO_QUERIES.items():
            res = backend.get("/search-visual", q=q, kind="photo", top_k=10).json().get("results", [])
            names = [r["filename"] for r in res]
            ok1 = bool(names) and names[0] in correct
            ok3 = any(n in correct for n in names[:3])
            oks = ok1 and res[0].get("confidence") == "strong"
            top1 += ok1
            top3 += ok3
            strong += oks
            report["photos"].append({"query": q, "correct": sorted(correct), "results": [(r["filename"], r.get("confidence"), round(r["score"], 4)) for r in res[:5]], "top1": ok1, "top3": ok3})
            print(f"  [{'OK ' if ok1 else 'top3' if ok3 else 'MISS'}] {q!r}: {[(r['filename'], r.get('confidence')) for r in res[:3]]}")
        n = len(PHOTO_QUERIES)
        print(f"\nPhotos: top-1 {top1}/{n} ({100 * top1 / n:.0f}%), top-3 {top3}/{n} ({100 * top3 / n:.0f}%), correct and marked strong {strong}/{n}")

        vid_ok = moment_ok = 0
        print("\nVideos")
        for q, (video, scene) in VIDEO_QUERIES.items():
            res = backend.get("/search-visual", q=q, kind="video", top_k=5).json().get("results", [])
            first = res[0] if res else {}
            t = first.get("timestamp_offset_seconds")
            right_video = first.get("filename") == video
            right_moment = right_video and t is not None and scene * SCENE_SECONDS <= t < (scene + 1) * SCENE_SECONDS
            vid_ok += right_video
            moment_ok += right_moment
            report["videos"].append({"query": q, "expected": [video, scene], "got": [first.get("filename"), t]})
            print(f"  [{'OK ' if right_moment else 'video' if right_video else 'MISS'}] {q!r}: {first.get('filename')} at {t} s (expected {video}, {scene * SCENE_SECONDS}-{(scene + 1) * SCENE_SECONDS} s)")
        m = len(VIDEO_QUERIES)
        print(f"\nVideos: right video {vid_ok}/{m} ({100 * vid_ok / m:.0f}%), right video and moment {moment_ok}/{m} ({100 * moment_ok / m:.0f}%)")
        report["summary"] = {"photo_top1": top1, "photo_top3": top3, "photo_strong": strong, "photo_n": n, "video_right": vid_ok, "video_moment": moment_ok, "video_n": m}
        (REPO / "backend" / "data" / "eval_visual_real.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        backend.stop()
        for _ in range(10):
            shutil.rmtree(tmp, ignore_errors=True)
            if not tmp.exists():
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
