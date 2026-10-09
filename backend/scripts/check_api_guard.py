"""API guard check: starts the backend from source on a throwaway data
folder and asserts that a wrong Host header, a missing token, a wrong token
and a token in the URL are all refused, that the token in the header works,
and that a thumbnail loads through the header. Exits non-zero on failure."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
TOKEN = "guard-check-token-not-secret"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    work = Path(tempfile.mkdtemp())
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    auth = {"X-IntelliFile-Token": TOKEN}
    env = {**os.environ, "INTELLIFILE_APP_DATA_DIR": str(work / "appdata"), "INTELLIFILE_API_TOKEN": TOKEN, "INTELLIFILE_OFFLINE_GUARD": "1"}
    proc = subprocess.Popen([sys.executable, str(BACKEND / "run_backend.py"), "--port", str(port)], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.time() + 180
        while True:
            assert proc.poll() is None and time.time() < end, "backend did not start"
            try:
                if requests.get(f"{base}/health", timeout=2).ok:
                    break
            except requests.RequestException:
                time.sleep(1)

        assert requests.get(f"{base}/status", headers={**auth, "Host": "evil.example"}, timeout=10).status_code == 400
        assert requests.get(f"{base}/health", headers={"Host": "evil.example:8756"}, timeout=10).status_code == 400
        assert requests.get(f"{base}/status", headers={**auth, "Host": f"localhost:{port}"}, timeout=30).ok
        print("1. Wrong Host header refused (400); 127.0.0.1 and localhost accepted: OK")

        assert requests.get(f"{base}/status", timeout=10).status_code == 401
        assert requests.get(f"{base}/status", headers={"X-IntelliFile-Token": "wrong"}, timeout=10).status_code == 401
        assert requests.get(f"{base}/status", params={"token": TOKEN}, timeout=10).status_code == 401
        assert requests.get(f"{base}/status", headers=auth, timeout=30).ok
        print("2. No token, wrong token and token-in-URL refused (401); token in the header works: OK")

        photos = work / "photos"
        photos.mkdir()
        img = photos / "red.png"
        Image.new("RGB", (64, 64), "red").save(img)
        assert requests.post(f"{base}/access", json={"mode": "limited"}, headers=auth, timeout=10).json()["mode"] == "limited"
        assert "queued" in requests.post(f"{base}/index-folder", json={"folder": str(photos)}, headers=auth, timeout=10).json()
        end = time.time() + 120
        while True:
            r = requests.get(f"{base}/thumbnail", params={"path": str(img), "size": 32}, headers=auth, timeout=30)
            if r.status_code == 200 or time.time() > end:
                break
            time.sleep(1)
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg", r.status_code
        assert requests.get(f"{base}/thumbnail", params={"path": str(img), "token": TOKEN}, timeout=10).status_code == 401
        assert requests.get(f"{base}/is-indexed", params={"path": str(img)}, headers=auth, timeout=10).json() == {"indexed": True}
        assert requests.get(f"{base}/is-indexed", params={"path": str(work / "nope.png")}, headers=auth, timeout=10).json() == {"indexed": False}
        print("3. Thumbnail loads with the header, refused with the token in the URL; /is-indexed answers: OK")
        print("API guard OK.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
