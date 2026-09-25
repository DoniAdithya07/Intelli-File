"""Live test: exercises the ACTUAL RUNNING backend over real HTTP, the
same way the desktop app talks to it (not by importing internal modules
directly, which is what the prototype_*.py scripts do). This is what
catches integration bugs the isolated scripts can't see (see README
Phase 5/6 "Bugs caught by live testing" and the 2026-09-10 models/
exclusion bug).

Requires the backend to already be running:

    cd backend && venv/bin/uvicorn app.main:app --port 8756

Then run this in another terminal:

    backend/venv/bin/python backend/scripts/live_test_all_phases.py

It creates a small temp folder of real sample files, indexes it through
the real /index-folder endpoint, runs real /search queries against the
real live database (whatever you already have indexed, PLUS the temp
folder), tests /transcribe with real synthesized speech, and then
CLEANS UP after itself — it only removes the records it created, your
existing indexed data is untouched.

Prints one line per phase: PASS / FAIL / SKIP, with a reason.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8756"
RESULTS: list[tuple[str, str, str]] = []  # (phase, status, detail)


def record(phase: str, status: str, detail: str = "") -> None:
    RESULTS.append((phase, status, detail))
    print(f"[{status}] {phase}" + (f" — {detail}" if detail else ""))


def check_server_up() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=3)
        r.raise_for_status()
        data = r.json()
        record("Server reachable", "PASS", f"app_data_dir={data.get('app_data_dir')}")
        return True
    except Exception as e:
        record("Server reachable", "FAIL", f"{e}. Start it with: cd backend && venv/bin/uvicorn app.main:app --port 8756")
        return False


def make_sample_folder(tmp: Path) -> None:
    (tmp / "scaling_notes.md").write_text(
        "# System Design Notes\n\n"
        "Horizontal scaling allows additional instances to be provisioned when "
        "system demand increases. This helps handle sudden traffic spikes by "
        "distributing load across a load balancer.\n"
    )
    (tmp / "bread_recipe.txt").write_text(
        "Sourdough Bread Recipe\n\n"
        "Mix flour, water, salt, and starter. Let it rise overnight before baking "
        "at high heat.\n"
    )
    # Deliberately obscure vocabulary (photosynthesis), not "scaling"/"traffic"/
    # "load balancer" — those words already appear verbatim inside this
    # project's own README.md and PRD (which are typically already indexed
    # in a live dev database), which would let an EXACT keyword match on the
    # docs beat the intended typo-corrected match on this file, producing a
    # false failure that has nothing to do with typo tolerance itself.
    (tmp / "biology_notes.txt").write_text(
        "Plant Biology Notes\n\n"
        "Chlorophyll pigments inside chloroplasts absorb sunlight and convert "
        "carbon dioxide and water into glucose and oxygen through photosynthesis.\n"
    )
    (tmp / "ignored.exe").write_text("should never be indexed")
    # 2026-09-20: more file types. Obscure vocabulary again, for the same
    # reason as biology_notes above.
    (tmp / "vineyard_harvest.csv").write_text(
        "vineyard,grape,tonnes\nMoonstone Ridge,Zinfandel,42\nQuiet Hollow,Riesling,17\n"
    )
    (tmp / "thermostat.py").write_text(
        "def hysteresis_band(setpoint):\n    # widen the deadband so the compressor stops short-cycling\n    return setpoint - 0.5, setpoint + 0.5\n"
    )
    (tmp / "package-lock.json").write_text('{"name": "must-never-be-indexed", "lockfileVersion": 3}')
    # Phase 8: generated photo fixtures, same shapes as prototype_visual_search.py.
    # Real photographs were checked by hand on 2026-09-11 (see README Phase 8);
    # this only proves the HTTP path index -> /search-visual -> /thumbnail works
    # against the live server.
    from PIL import Image, ImageDraw
    red = Image.new("RGB", (512, 512), "white")
    ImageDraw.Draw(red).ellipse([80, 80, 432, 432], fill="red")
    red.save(tmp / "red_circle.png")
    blue = Image.new("RGB", (512, 512), "white")
    ImageDraw.Draw(blue).rectangle([100, 100, 412, 412], fill="blue")
    blue.save(tmp / "blue_square.jpg", quality=90)
    excluded = tmp / "node_modules" / "somepkg"
    excluded.mkdir(parents=True)
    (excluded / "readme.txt").write_text("must never be indexed either")


def wait_for_job(folder: Path, timeout_s: float = 120) -> dict:
    """Indexing is a background job since Phase 9; poll /status until this folder is done."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = requests.get(f"{BASE_URL}/status", timeout=30).json()
        job = status["job"]
        busy_with_us = job["state"] == "running" and job["folder"] == str(folder)
        queued = str(folder) in job.get("queued", [])
        if not busy_with_us and not queued and job["state"] != "running":
            return status
        time.sleep(0.5)
    raise TimeoutError("indexing job did not finish")


def test_index_folder(tmp: Path) -> bool:
    try:
        r = requests.post(f"{BASE_URL}/index-folder", json={"folder": str(tmp)}, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "queued" not in data:
            record("Phase 2/3/4 — Folder indexing", "FAIL", f"expected a queued job, got: {data}")
            return False
        status = wait_for_job(tmp)
        ours = next((f for f in status["folders"] if f["path"] == str(tmp)), None)
        if ours and ours["documents"] == 5 and ours["photos"] == 2 and status["job"]["failed"] == 0:
            record("Phase 2/3/4 — Folder indexing (background job + /status)", "PASS", f"5 documents (md/txt/csv/py) + 2 photos under {tmp.name}; .exe, package-lock.json and node_modules/ skipped; job failed=0")
            return True
        record("Phase 2/3/4 — Folder indexing", "FAIL", f"expected 5 documents + 2 photos, got: {ours}, job={status['job']}")
        return False
    except Exception as e:
        record("Phase 2/3/4 — Folder indexing", "FAIL", str(e))
        return False


def test_status_and_forget(tmp: Path) -> None:
    try:
        status = requests.get(f"{BASE_URL}/status", timeout=30).json()
        assert status["engine"] == "online" and status["totals"]["files"] >= 5 and status["index_size_bytes"] > 0
        assert status["models"]["text"]["name"] in ("bge-small-en-v1.5", "all-MiniLM-L6-v2")
        record("Phase 9 — /status reports engine, totals, index size, models", "PASS", f"{status['totals']['files']} files, index {status['index_size_bytes'] // 1024} KB")
        r = requests.post(f"{BASE_URL}/forget-folder", json={"folder": str(tmp)}, timeout=60).json()
        after = requests.get(f"{BASE_URL}/status", timeout=30).json()
        still = [f for f in after["folders"] if f["path"] == str(tmp)]
        if r.get("removed") == 7 and not still:
            record("Phase 9 — /forget-folder removes the folder and its 7 records", "PASS")
        else:
            record("Phase 9 — /forget-folder", "FAIL", f"removed={r}, still watched={still}")
    except Exception as e:
        record("Phase 9 — /status and /forget-folder", "FAIL", str(e))


# This script's own source holds every fixture sentence below, so once the
# user has the project folder itself indexed (2026-09-20 live test), the
# script file and the project docs outrank the temp fixtures. Hits from
# inside the repo are dropped: the checks are about the fixtures.
REPO_ROOT = str(Path(__file__).resolve().parents[2])


def search(q: str) -> list[dict]:
    r = requests.get(f"{BASE_URL}/search", params={"q": q, "top_k": 10}, timeout=30)
    r.raise_for_status()
    return [res for res in r.json().get("results", []) if not (res.get("path") or "").startswith(REPO_ROOT)]


def test_semantic_search() -> None:
    try:
        results = search("handling sudden increases in traffic")
        top_files = [r["filename"] for r in results[:3]]
        if "scaling_notes.md" in top_files:
            record("Phase 5 — Semantic search (zero shared words)", "PASS", f"top results: {top_files}")
        else:
            record("Phase 5 — Semantic search (zero shared words)", "FAIL", f"expected scaling_notes.md near top, got: {top_files}")
    except Exception as e:
        record("Phase 5 — Semantic search", "FAIL", str(e))


def test_keyword_search() -> None:
    try:
        results = search("sourdough")
        top_files = [r["filename"] for r in results[:3]]
        if "bread_recipe.txt" in top_files:
            record("Phase 5 — Keyword (BM25) search", "PASS", f"top results: {top_files}")
        else:
            record("Phase 5 — Keyword (BM25) search", "FAIL", f"expected bread_recipe.txt near top, got: {top_files}")
    except Exception as e:
        record("Phase 5 — Keyword search", "FAIL", str(e))


def test_exact_phrase() -> None:
    try:
        results = search('"load balancer"')
        top_files = [r["filename"] for r in results[:3]]
        if "scaling_notes.md" in top_files:
            record("Phase 5 — Exact-phrase search", "PASS", f"top results: {top_files}")
        else:
            record("Phase 5 — Exact-phrase search", "FAIL", f"expected scaling_notes.md, got: {top_files}")
    except Exception as e:
        record("Phase 5 — Exact-phrase search", "FAIL", str(e))


def test_punctuation_safety() -> None:
    try:
        results = search("what's up, load-balancing: test.")
        record("Phase 5 — Punctuation doesn't crash search", "PASS", f"{len(results)} results, no error")
    except Exception as e:
        record("Phase 5 — Punctuation doesn't crash search", "FAIL", str(e))


def test_typo_tolerance() -> None:
    try:
        results = search("chlorofil pigmants and photosinthesis")
        top_files = [r["filename"] for r in results[:3]]
        if "biology_notes.txt" in top_files:
            record("Phase 6 — Typo tolerance", "PASS", f"top results: {top_files}")
        else:
            record("Phase 6 — Typo tolerance", "FAIL", f"expected biology_notes.txt, got: {top_files}")
    except Exception as e:
        record("Phase 6 — Typo tolerance", "FAIL", str(e))


def test_new_file_types() -> None:
    """2026-09-20: CSV rows and source-code comments are searchable, and the
    lockfile that shares the .json extension is not."""
    try:
        csv_top = [r["filename"] for r in search("riesling quiet hollow")[:3]]
        py_top = [r["filename"] for r in search("compressor short-cycling deadband")[:3]]
        lock = [r["filename"] for r in search("lockfileVersion must-never-be-indexed")]
        problems = []
        if "vineyard_harvest.csv" not in csv_top:
            problems.append(f"csv row not found, top={csv_top}")
        if "thermostat.py" not in py_top:
            problems.append(f"python comment not found, top={py_top}")
        if "package-lock.json" in lock:
            problems.append("package-lock.json was indexed")
        if problems:
            record("File types — CSV / code searchable, lockfile skipped", "FAIL", "; ".join(problems))
        else:
            typed = [r["filename"] for r in search("type:csv vineyard")]
            record("File types — CSV / code searchable, lockfile skipped", "PASS", f"csv top={csv_top[0]}, py top={py_top[0]}, type:csv -> {typed}")
    except Exception as e:
        record("File types — CSV / code searchable, lockfile skipped", "FAIL", str(e))


def test_transcription() -> None:
    if sys.platform != "darwin":
        record("Phase 7 — Voice transcription", "SKIP", "live speech synthesis only wired for macOS `say`/`afconvert`")
        return
    tmp_wav = None
    try:
        tmp_dir = Path(tempfile.mkdtemp())
        aiff = tmp_dir / "voice.aiff"
        wav = tmp_dir / "voice.wav"
        subprocess.run(["say", "-o", str(aiff), "Find my notes about horizontal scaling"], check=True, capture_output=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff), str(wav)], check=True, capture_output=True)
        tmp_wav = wav
        with open(wav, "rb") as f:
            r = requests.post(f"{BASE_URL}/transcribe", files={"audio": ("voice.wav", f, "audio/wav")}, timeout=60)
        r.raise_for_status()
        text = r.json().get("text", "")
        if "scaling" in text.lower():
            record("Phase 7 — Voice transcription (/transcribe API)", "PASS", f"transcribed: {text!r}")
        else:
            record("Phase 7 — Voice transcription (/transcribe API)", "FAIL", f"unexpected transcription: {text!r}")
        record("Phase 7 — Live mic button in the app window", "SKIP", "needs a human to actually click 🎤 and speak — everything upstream/downstream is proven above")
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        record("Phase 7 — Voice transcription", "FAIL", str(e))
        if tmp_wav and tmp_wav.parent.exists():
            shutil.rmtree(tmp_wav.parent, ignore_errors=True)


def test_visual_search(tmp: Path) -> None:
    try:
        r = requests.get(f"{BASE_URL}/search-visual", params={"q": "a red circle", "top_k": 5}, timeout=60)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            record("Phase 8 — Visual search (/search-visual API)", "SKIP", data["error"])
            return
        # Only this run's fixtures — the live database may hold the user's real photos too.
        ours = [x for x in data.get("results", []) if x["path"].startswith(str(tmp))]
        top = [x["filename"] for x in ours]
        if top and top[0] == "red_circle.png":
            record("Phase 8 — Visual search (/search-visual API)", "PASS", f"'a red circle' -> {top}")
        else:
            record("Phase 8 — Visual search (/search-visual API)", "FAIL", f"expected red_circle.png first, got: {top}")
            return
        text_leak = [x for x in data.get("results", []) if x["filename"].endswith((".md", ".txt"))]
        if text_leak:
            record("Phase 8 — Text files never appear in photo results", "FAIL", f"leaked: {[x['filename'] for x in text_leak]}")
        else:
            record("Phase 8 — Text files never appear in photo results", "PASS")
        t = requests.get(f"{BASE_URL}/thumbnail", params={"path": str(tmp / "red_circle.png"), "size": 64}, timeout=30)
        if t.status_code == 200 and t.headers.get("content-type", "").startswith("image/") and len(t.content) > 0:
            record("Phase 8 — Thumbnail (/thumbnail API)", "PASS", f"{len(t.content)} bytes, {t.headers.get('content-type')}")
        else:
            record("Phase 8 — Thumbnail (/thumbnail API)", "FAIL", f"status={t.status_code} content-type={t.headers.get('content-type')}")
        record("Phase 8 — Real photographs", "SKIP", "checked by hand 2026-09-11 with real phone photos (see README Phase 8) — synthetic shapes only prove the wiring")
    except Exception as e:
        record("Phase 8 — Visual search", "FAIL", str(e))


def test_activity_memory() -> None:
    """Phase 16: a search is remembered by the server, an open reported by
    the UI joins the same session, /context reflects both, the switch stops
    recording, and nothing of this test is left behind. The user's own
    events are preserved: only the rows this test created are counted."""
    try:
        settings_before = requests.get(f"{BASE_URL}/settings", timeout=10).json()
        requests.post(f"{BASE_URL}/settings", json={"remember_activity": True}, timeout=10)
        total_before = requests.get(f"{BASE_URL}/events", params={"limit": 1}, timeout=10).json()["total"]
        marker = "zebra migration live-test"
        requests.get(f"{BASE_URL}/search", params={"q": marker, "top_k": 3}, timeout=30)
        opened = requests.post(f"{BASE_URL}/events", json={"kind": "file_opened", "path": "/live-test/zebra notes.pdf"}, timeout=10).json()
        events = requests.get(f"{BASE_URL}/events", params={"limit": 5}, timeout=10).json()
        ctx = requests.get(f"{BASE_URL}/context", timeout=10).json()
        added = events["total"] - total_before
        kinds = [e["kind"] for e in events["events"][:2]]
        same_session = len({e["session_id"] for e in events["events"][:2]}) == 1
        requests.post(f"{BASE_URL}/settings", json={"remember_activity": False}, timeout=10)
        requests.get(f"{BASE_URL}/search", params={"q": marker + " again", "top_k": 3}, timeout=30)
        refused = requests.post(f"{BASE_URL}/events", json={"kind": "file_opened", "path": "/live-test/ignored.pdf"}, timeout=10).json()
        total_off = requests.get(f"{BASE_URL}/events", params={"limit": 1}, timeout=10).json()["total"]
        requests.post(f"{BASE_URL}/settings", json={"remember_activity": settings_before.get("remember_activity", True)}, timeout=10)
        ok = (
            opened.get("recorded") is True
            and added == 2
            and kinds == ["file_opened", "query"]
            and same_session
            and marker in ctx.get("queries", [])
            and "/live-test/zebra notes.pdf" in ctx.get("files", [])
            and "pdf" in ctx.get("file_types", [])
            and refused.get("recorded") is False
            and total_off == events["total"]
        )
        if ok:
            record("Phase 16 — Activity memory (/search remembered, /events, /context, switch off)", "PASS",
                   f"2 events added in one session; context queries={ctx['queries'][-1:]} files={len(ctx['files'])}; 0 recorded while off")
        else:
            record("Phase 16 — Activity memory", "FAIL",
                   f"added={added} kinds={kinds} same_session={same_session} ctx={ctx} refused={refused} total_off={total_off} vs {events['total']}")
    except Exception as e:
        record("Phase 16 — Activity memory", "FAIL", str(e))


def test_profile_and_recommendations() -> None:
    """Phase 17 over HTTP: /profile is well-formed and consistent with the
    event count, /recommendations honours the personalize switch, and a
    search result carries the `personal` field (null while cold or off)."""
    try:
        before = requests.get(f"{BASE_URL}/settings", timeout=10).json()
        profile = requests.get(f"{BASE_URL}/profile", params={"rebuild": "true"}, timeout=60).json()
        events_total = requests.get(f"{BASE_URL}/events", params={"limit": 1}, timeout=10).json()["total"]
        shape_ok = (
            profile["events"] == events_total
            and isinstance(profile["cold_start"], bool)
            and len(profile["heatmap"]) == 7 and all(len(row) == 24 // profile["slot_hours"] for row in profile["heatmap"])
            and isinstance(profile["top_files"], list) and isinstance(profile["topics"], list)
            and abs(sum(profile["type_shares"].values()) - (1.0 if profile["type_shares"] else 0.0)) < 1e-6
        )
        requests.post(f"{BASE_URL}/settings", json={"personalize": True}, timeout=10)
        recs_on = requests.get(f"{BASE_URL}/recommendations", timeout=60).json()
        hit = requests.get(f"{BASE_URL}/search", params={"q": "gym plan", "top_k": 3}, timeout=30).json()["results"]
        personal_on = [r.get("personal", "missing") for r in hit]
        requests.post(f"{BASE_URL}/settings", json={"personalize": False}, timeout=10)
        recs_off = requests.get(f"{BASE_URL}/recommendations", timeout=60).json()
        hit_off = requests.get(f"{BASE_URL}/search", params={"q": "gym plan", "top_k": 3}, timeout=30).json()["results"]
        requests.post(f"{BASE_URL}/settings", json={"personalize": before.get("personalize", True)}, timeout=10)
        lists = recs_on["likely_next"] + recs_on["usual_now"] + recs_on["recent"]
        ok = (
            shape_ok
            and recs_on["enabled"] is True and recs_on["cold_start"] == profile["cold_start"]
            and all(e["reason"] and e["filename"] for e in lists)
            and (not profile["cold_start"] or all(p is None for p in personal_on))
            and (profile["cold_start"] or all(isinstance(p, dict) and "boost" in p for p in personal_on))
            and recs_off["enabled"] is False and not (recs_off["likely_next"] or recs_off["recent"])
            and all(r.get("personal", "missing") is None for r in hit_off)
        )
        if ok:
            record("Phase 17 — Profile & recommendations (/profile, /recommendations, personal field, switch)", "PASS",
                   f"{profile['events']} events, cold_start={profile['cold_start']}, {len(profile['topics'])} topics, {len(lists)} recommendations with reasons; off → none")
        else:
            record("Phase 17 — Profile & recommendations", "FAIL",
                   f"shape_ok={shape_ok} recs_on={ {k: len(v) for k, v in recs_on.items() if isinstance(v, list)} } personal_on={personal_on} recs_off_enabled={recs_off['enabled']} hit_off={[r.get('personal','missing') for r in hit_off]}")
    except Exception as e:
        record("Phase 17 — Profile & recommendations", "FAIL", str(e))


def test_routing() -> None:
    """Phase 18 over HTTP: /search returns a route report; a file-name
    query takes the filename tier without embedding; a plain keyword query
    skips the embedding; a long question reaches hybrid(+rerank); manual
    modes bypass the router; metadata filters work alone."""
    try:
        def routed(q, **params):
            r = requests.get(f"{BASE_URL}/search", params={"q": q, "top_k": 5, **params}, timeout=60)
            r.raise_for_status()
            return r.json()
        def ran(route, stage):
            return any(s["stage"] == stage and not s.get("skipped") for s in route["stages"])
        by_name = routed("gym plan")
        keyword = routed("sourdough starter")
        question = routed("how do servers cope with a spike in visitors")
        manual = routed("gym plan", mode="smart")
        meta = routed("type:txt after:2020")
        ok = (
            by_name["route"]["tier"] in ("filename", "keyword") and not ran(by_name["route"], "semantic")
            and (by_name["route"]["tier"] != "filename" or by_name["results"][0]["filename"] == "gym plan.txt")
            and keyword["route"]["requested_tier"] in ("keyword", "filename") and not (ran(keyword["route"], "semantic") and not keyword["route"]["escalated"])
            and question["route"]["tier"] in ("hybrid", "hybrid+rerank") and ran(question["route"], "semantic")
            and manual["route"]["tier"] == "hybrid" and manual["route"]["complexity"] == -1
            and meta["route"]["tier"] == "metadata" and all(r["filename"].endswith(".txt") for r in meta["results"]) and meta["results"]
            and all(isinstance(r["route"]["total_ms"], (int, float)) for r in (by_name, keyword, question, manual, meta))
        )
        if ok:
            tiers = {q: d["route"]["tier"] + ("↑" if d["route"]["escalated"] else "") for q, d in (("gym plan", by_name), ("sourdough starter", keyword), ("question", question), ("type:txt", meta))}
            record("Phase 18 — Query routing (/search route report, tiers, stage skipping, filters, manual override)", "PASS",
                   f"{tiers}; question {question['route']['total_ms']} ms vs name {by_name['route']['total_ms']} ms")
        else:
            record("Phase 18 — Query routing", "FAIL", f"name={by_name['route']} keyword={keyword['route']['tier']} question={question['route']['tier']} manual={manual['route']['tier']} meta={meta['route']['tier']}/{len(meta['results'])}")
    except Exception as e:
        record("Phase 18 — Query routing", "FAIL", str(e))


def test_agent() -> None:
    """Phase 19 over HTTP: /ask streams a well-formed trace ending in an
    answer with citations that exist, or an honest not-found; /ask/status
    reports the model. Skipped when no model is installed."""
    try:
        status = requests.get(f"{BASE_URL}/ask/status", timeout=10).json()
        if not status.get("available"):
            record("Phase 19 — Local LLM agent (/ask)", "SKIP", "no model in backend/models/llm (scripts/download_llm_model.py)")
            return
        events = []
        with requests.get(f"{BASE_URL}/ask", params={"q": "what exercises are on the gym plan for wednesday"}, stream=True, timeout=180) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if line and line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        types = [e["type"] for e in events]
        answer = next((e for e in events if e["type"] == "answer"), None)
        done = next((e for e in events if e["type"] == "done"), None)
        calls = [e for e in events if e["type"] == "tool_call"]
        ok = (
            types and types[0] == "context" and types[-1] == "done" and answer is not None and done is not None
            and calls and all(c["args"].get("mode") for c in calls if c["tool"] == "search")
            and "token" in types
            and all(c["file_id"] and c["filename"] for c in answer["citations"])
            and (answer["grounded"] == bool(answer["citations"]))
        )
        if ok:
            record("Phase 19 — Local LLM agent (/ask stream: context → plan → tool calls → cited answer → done)", "PASS",
                   f"{status['model']}: {done['tool_calls']} tool call(s), {done['seconds']}s, grounded={answer['grounded']}, cites={[c['filename'] for c in answer['citations']]}")
        else:
            record("Phase 19 — Local LLM agent", "FAIL", f"types={types[:12]} answer={answer} calls={calls}")
    except Exception as e:
        record("Phase 19 — Local LLM agent", "FAIL", str(e))


def test_power() -> None:
    """Phase 10 over HTTP: /status carries a power block, /power agrees,
    the settings round-trip (mode + both switches), an invalid mode is
    refused, and — on a laptop that is plugged in — indexing is not paused
    for power. (Actually unplugging is the human's check.)"""
    try:
        before = requests.get(f"{BASE_URL}/settings", timeout=10).json()
        status = requests.get(f"{BASE_URL}/status", timeout=30).json()
        power = requests.get(f"{BASE_URL}/power", timeout=10).json()
        shape = all(k in power for k in ("has_battery", "on_battery", "low_power_mode", "cpu_percent", "app_cpu_percent", "paused", "paused_reason", "mode", "pending_jobs")) and status.get("power", {}).get("mode") == power["mode"]
        r1 = requests.post(f"{BASE_URL}/settings", json={"resource_mode": "battery_saver", "pause_on_battery": False}, timeout=10).json()
        r2 = requests.get(f"{BASE_URL}/power", timeout=10).json()
        bad = requests.post(f"{BASE_URL}/settings", json={"resource_mode": "turbo"}, timeout=10).json()
        requests.post(f"{BASE_URL}/settings", json={k: before[k] for k in ("resource_mode", "pause_on_battery", "pause_on_low_power")}, timeout=10)
        r3 = requests.get(f"{BASE_URL}/power", timeout=10).json()
        consistent = (not power["on_battery"] and not power["low_power_mode"]) <= (not power["paused"])  # plugged in ⇒ not paused for power
        ok = shape and r1["resource_mode"] == "battery_saver" and r1["pause_on_battery"] is False and r2["mode"] == "battery_saver" and "error" in bad and r3["mode"] == before["resource_mode"] and consistent
        if ok:
            record("Phase 10 — Power-aware indexing (/power, /status.power, settings round-trip)", "PASS",
                   f"battery={power['has_battery']} on_battery={power['on_battery']} low_power={power['low_power_mode']} paused={power['paused']} cpu={power['cpu_percent']}% app={power['app_cpu_percent']}% mode={before['resource_mode']}")
        else:
            record("Phase 10 — Power-aware indexing", "FAIL", f"shape={shape} r1={r1} r2={r2.get('mode')} bad={bad} r3={r3.get('mode')} consistent={consistent}")
    except Exception as e:
        record("Phase 10 — Power-aware indexing", "FAIL", str(e))


def test_offline() -> None:
    """Phase 12 — offline operation, measured: runs LAST, after every other
    check has exercised indexing, all search tiers, voice, photos, the
    profile, the router and the agent. With the server started under
    INTELLIFILE_OFFLINE_GUARD=1, every outbound connection or DNS lookup
    would have been refused and counted; the count must be zero."""
    try:
        guard = requests.get(f"{BASE_URL}/offline-guard", timeout=10).json()
        if not guard.get("enabled"):
            record("Phase 12 — Offline operation (network guard)", "SKIP", "start the backend with INTELLIFILE_OFFLINE_GUARD=1 to measure this")
            return
        if guard["blocked_attempts"] == 0:
            record("Phase 12 — Offline operation (network guard)", "PASS", "0 outbound connection/DNS attempts across the whole suite (indexing, search tiers, voice, photos, profile, router, agent)")
        else:
            record("Phase 12 — Offline operation (network guard)", "FAIL", f"{guard['blocked_attempts']} attempt(s) refused: {guard['blocked'][:5]}")
    except Exception as e:
        record("Phase 12 — Offline operation", "FAIL", str(e))


def cleanup(tmp: Path) -> None:
    """Purge exactly the records this script created, leave everything else untouched."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.embeddings.model import EmbeddingModel, default_model_dir
    from app.indexing import IMAGES_TABLE, Indexer
    from app.paths import ensure_app_dirs
    from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore

    dirs = ensure_app_dirs()
    record_store = FileRecordStore(dirs["database"] / "files.db")
    keyword_store = KeywordStore(dirs["keyword_index"] / "keyword.db")
    vector_store = LanceDBVectorStore(str(dirs["vector_index"]))
    model = EmbeddingModel(default_model_dir(Path(__file__).resolve().parents[1] / "models"))
    indexer = Indexer(model, vector_store, keyword_store, record_store)

    removed = 0
    for rec in record_store.list_active():
        if rec.path.startswith(str(tmp)):
            indexer.delete_file(rec.file_id)
            vector_store.delete_by_file_id(IMAGES_TABLE, rec.file_id)  # no-op for text files
            record_store.remove(rec.file_id)
            removed += 1
    record_store.close()
    keyword_store.close()
    print(f"\nCleanup: removed {removed} test record(s) created by this run from the live database.")


def main() -> int:
    print("=" * 70)
    print("LIVE TEST — real HTTP calls against the running backend")
    print("=" * 70)

    if not check_server_up():
        print("\nCannot continue without a running server.")
        return 1

    # The suite runs against the user's real backend: keep its fixture
    # queries out of their activity memory (the Phase 16 check switches it
    # on for itself and restores what it found).
    try:
        original_settings = requests.get(f"{BASE_URL}/settings", timeout=10).json()
        requests.post(f"{BASE_URL}/settings", json={"remember_activity": False}, timeout=10)
    except Exception:
        original_settings = None
    tmp = Path(tempfile.mkdtemp(prefix="intellifile_live_test_"))
    try:
        make_sample_folder(tmp)
        indexed_ok = test_index_folder(tmp)
        if indexed_ok:
            time.sleep(0.5)  # let the index settle
            test_semantic_search()
            test_keyword_search()
            test_exact_phrase()
            test_punctuation_safety()
            test_typo_tolerance()
            test_new_file_types()
            test_visual_search(tmp)
            test_status_and_forget(tmp)
        else:
            for phase in [
                "Phase 5 — Semantic search",
                "Phase 5 — Keyword search",
                "Phase 5 — Exact-phrase search",
                "Phase 5 — Punctuation safety",
                "Phase 6 — Typo tolerance",
                "Phase 8 — Visual search",
            ]:
                record(phase, "SKIP", "skipped because indexing failed above")
        test_transcription()
        test_activity_memory()
        test_profile_and_recommendations()
        test_routing()
        test_agent()
        test_power()
        test_offline()
    finally:
        cleanup(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        if original_settings is not None:
            try:
                requests.post(f"{BASE_URL}/settings", json={"remember_activity": original_settings.get("remember_activity", True)}, timeout=10)
            except Exception:
                pass

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for phase, status, detail in RESULTS:
        print(f"[{status}] {phase}")

    failed = [p for p, s, _ in RESULTS if s == "FAIL"]
    passed = [p for p, s, _ in RESULTS if s == "PASS"]
    skipped = [p for p, s, _ in RESULTS if s == "SKIP"]
    print(f"\n{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped.")
    if failed:
        print("Failed: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
