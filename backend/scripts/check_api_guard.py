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
        why = f"{base}/files/why-missing"
        assert requests.get(why, params={"name": "red.png"}, timeout=10).status_code == 401
        assert requests.get(why, params={"name": "red.png", "token": TOKEN}, timeout=10).status_code == 401
        assert requests.get(why, params={"name": "red.png"}, headers={**auth, "Host": "evil.example"}, timeout=10).status_code == 400
        # The picture is searchable before its text has been read: until then the answer is "waiting to be read".
        end = time.time() + 120
        while True:
            found = requests.get(why, params={"name": "RED"}, headers=auth, timeout=10).json()
            if found["status"] != "indexing" or time.time() > end:
                break
            time.sleep(1)
        assert found["status"] == "indexed" and found["path"] == str(img) and found["name"] == "RED" and "is indexed" in found["reason"], found
        assert requests.get(why, params={"name": "nope.png"}, headers=auth, timeout=10).json()["status"] in ("unknown", "indexing")
        assert requests.get(why, params={"name": "setup.exe"}, headers=auth, timeout=10).json()["status"] == "unsupported"
        assert requests.get(why, params={"name": "*.png"}, headers=auth, timeout=10).json()["status"] == "invalid"

        # A file deleted while the app runs is still "deleted" after its record
        # is purged (which takes a fraction of a second), for this run of the app.
        def status_of(name: str, wanted: str, seconds: int = 90) -> dict:
            end = time.time() + seconds
            while True:
                body = requests.get(why, params={"name": name}, headers=auth, timeout=10).json()
                if body["status"] == wanted or time.time() > end:
                    return body
                time.sleep(1)
        blue = photos / "blue.png"
        Image.new("RGB", (64, 64), "blue").save(blue)
        assert status_of("blue.png", "indexed")["status"] == "indexed"
        blue.unlink()
        gone = status_of("blue.png", "deleted")
        time.sleep(5)
        later = requests.get(why, params={"name": "blue.png"}, headers=auth, timeout=10).json()
        assert gone["status"] == "deleted" and later["status"] == "deleted" and "this run of the app" in later["reason"] and later["path"] == str(blue), (gone, later)
        print("3. Thumbnail loads with the header, refused with the token in the URL; /is-indexed answers; /files/why-missing is behind the token and the Host check and answers, and still says 'deleted' 5 s after a file was removed: OK")
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
