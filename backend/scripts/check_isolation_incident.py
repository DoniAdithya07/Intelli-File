"""Post-incident regression tests (2026-11-02, POST_INCIDENT_REMEDIATION_REPORT.md).

Two incidents on 2026-11-02 involved an IntelliFile instance reading real
personal files it should not have: once via a mis-targeted installer launch
(real app-data), once via a "whole computer" access mode appearing in a
supposedly fresh, isolated app-data directory for a reason this
investigation could not conclusively prove (see the report, Task 2).

This script is pure static/unit-level verification: it imports backend
modules directly and calls their functions against temporary synthetic
directories. It never starts a server, never launches the desktop app or
its backend sidecar, and never touches any real folder — consistent with
the incident directive's "do not launch IntelliFile, its backend, desktop
executable or installer" restriction. Every directory used below is a
disposable tempfile.mkdtemp(), never the real home directory or D:\\.

Run:  backend\\venv\\Scripts\\python.exe backend\\scripts\\check_isolation_incident.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

root = Path(tempfile.mkdtemp(prefix="intellifile_isolation_check_"))
try:
    # 1. Fresh application-data initialization: a brand-new app-data dir has
    #    every subdir but no config files at all -- no access.json, no
    #    watched_folders.json. This is the precondition Incident 2's "fresh,
    #    empty" check (reported in the incident log) was based on.
    os.environ["INTELLIFILE_APP_DATA_DIR"] = str(root / "appdata1")
    from app.paths import ensure_app_dirs, get_app_data_dir

    dirs = ensure_app_dirs()
    assert get_app_data_dir() == root / "appdata1", "env var override must be honored exactly"
    for name in ("database", "vector_index", "keyword_index", "cache", "logs", "config"):
        assert dirs[name].is_dir(), f"{name} must exist after ensure_app_dirs()"
    config_files = list(dirs["config"].iterdir())
    assert config_files == [], f"a fresh app-data dir must start with zero config files, found: {config_files}"
    print("1. Fresh app-data init: all subdirs exist, zero config files: OK")

    # 2. Watched-folder / access defaults: with no access.json on disk, the
    #    access policy must default to "unset" (allows_indexing=False), and
    #    load_watched_folders() on a config dir with no watched_folders.json
    #    must return an empty list. This is the state a real first run (and
    #    Incident 2's isolated instance, in the first ~30s) is in.
    from app.files.access import AccessPolicy
    from app.watch_setup import load_watched_folders

    policy = AccessPolicy(dirs["config"])
    assert policy.mode == "unset", f"fresh config dir must default to 'unset', got {policy.mode!r}"
    assert policy.allows_indexing is False, "unset mode must not allow indexing"
    assert load_watched_folders(dirs["config"]) == [], "a fresh config dir must have zero watched folders"
    print("2. Fresh access/watched-folder defaults: mode='unset', 0 watched folders: OK")

    # 3. Environment-variable propagation: get_app_data_dir() must honor
    #    INTELLIFILE_APP_DATA_DIR exactly, with no merge against the real
    #    %LOCALAPPDATA%\IntelliFile default, in both directions (set and unset).
    del os.environ["INTELLIFILE_APP_DATA_DIR"]
    real_default = get_app_data_dir()
    assert str(real_default).replace("\\", "/").endswith("IntelliFile"), real_default
    os.environ["INTELLIFILE_APP_DATA_DIR"] = str(root / "appdata2")
    assert get_app_data_dir() == root / "appdata2", "override must win, no merge with the real default"
    print(f"3. Env-var propagation: unset -> real default ({real_default}); set -> override only, no merge: OK")

    # 4. Frontend/backend configuration consistency (static source check):
    #    the frontend must have no separate, persisted access/folder state of
    #    its own (localStorage/IndexedDB) that could let a stale value from
    #    one launch influence a later, differently-configured launch. This
    #    guards the exact ambiguity Task 2 of the incident investigation hit
    #    -- if someone adds such a cache later, this test must start failing.
    desktop_src = Path(__file__).resolve().parents[2] / "desktop" / "src"
    offending = []
    for path in desktop_src.rglob("*.ts*"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for kw in ("localStorage", "sessionStorage", "indexedDB"):
            if kw in text:
                for line in text.splitlines():
                    # Require an access/watch-mode term, not just "folder" --
                    # lib/favorites.ts legitimately stores pinned-folder
                    # *shortcuts* in localStorage (display only, never calls
                    # setAccess/indexFolder); that is not this check's
                    # concern and must not false-positive here.
                    if kw in line and any(tok in line for tok in ("setAccess", "chooseAccess", "AccessMode", "whole_computer", "whole computer", "indexFolder(")):
                        offending.append((path.name, line.strip()))
    assert not offending, f"found access/folder-registration code reading or writing browser storage -- re-review for cross-launch leakage: {offending}"
    print("4. No access-mode or watched-folder-registration state cached in frontend browser storage (localStorage/sessionStorage/IndexedDB): OK")
    print("   (note: lib/favorites.ts does use localStorage for pinned-folder *shortcuts*, display-only, never calls setAccess/indexFolder -- confirmed by reading it, not a cause of either incident, but still shares the same WebView2 origin as everything else -- see item 8.)")

    # 5. First-run access selection (static source check, no JS runtime
    #    available in this project -- see COMPLETE_TEST_INVENTORY.md): the
    #    onboarding screen's default selection must be "limited", never
    #    "all". This is a regression guard on the literal source text, not a
    #    rendered-component test.
    firstrun = (desktop_src / "pages" / "FirstRun.tsx").read_text(encoding="utf-8")
    assert 'useState<Choice>("limited")' in firstrun, "FirstRun's default choice must stay 'limited', not 'all'"
    print("5. FirstRun.tsx's default selection is 'limited' (source-level guard): OK")

    # 6/7. Prevention of implicit whole-computer scanning / unexpected folder
    #    registration: whole_computer_roots() must never be consulted unless
    #    mode is explicitly "all" (AccessPolicy.as_dict() already encodes
    #    this), and ensure_app_dirs() / a fresh AccessPolicy must never
    #    pre-populate watched_folders.json or access.json themselves.
    assert policy.as_dict()["whole_computer_roots"] == [], "whole_computer_roots must be [] unless mode=='all'"
    assert not (dirs["config"] / "access.json").exists(), "ensure_app_dirs() must never create access.json itself"
    assert not (dirs["config"] / "watched_folders.json").exists(), "ensure_app_dirs() must never create watched_folders.json itself"
    print("6/7. whole_computer_roots() empty unless mode=='all'; init never pre-creates access/watched-folder config: OK")

    # 7b. The guard rail added after this incident (app/files/access.py):
    #    whole_computer_roots() calls Path.home() directly with no isolation
    #    override, so without a guard, "all" mode reaching this function on
    #    ANY launch -- real or isolated -- resolves to the real home
    #    directory and real drives. Both 2026-11-02 incidents did exactly
    #    that. The guard: when INTELLIFILE_APP_DATA_DIR is set (an isolated
    #    instance -- a real packaged app never sets this itself) and
    #    INTELLIFILE_ALLOW_WHOLE_COMPUTER isn't "1", refuse and return [].
    from app.files.access import whole_computer_roots

    assert os.environ.get("INTELLIFILE_APP_DATA_DIR"), "this check needs APP_DATA_DIR set from step 3, to test the guard"
    os.environ.pop("INTELLIFILE_ALLOW_WHOLE_COMPUTER", None)
    guarded = whole_computer_roots()
    assert guarded == [], f"an isolated instance (APP_DATA_DIR set) without the explicit opt-in must get zero roots, got: {guarded}"
    print("7b. Guard CONFIRMED: an isolated instance without INTELLIFILE_ALLOW_WHOLE_COMPUTER=1 gets zero whole-computer roots (fail safe), not the real home/drives.")

    os.environ["INTELLIFILE_ALLOW_WHOLE_COMPUTER"] = "1"
    allowed = whole_computer_roots()
    assert allowed and allowed[0] == str(Path.home()), allowed
    print("7c. Escape hatch CONFIRMED: with the explicit opt-in set, whole_computer_roots() still works normally (real home) -- a deliberate isolated test of 'all' mode remains possible.")
    os.environ.pop("INTELLIFILE_ALLOW_WHOLE_COMPUTER", None)

    del os.environ["INTELLIFILE_APP_DATA_DIR"]
    unaffected = whole_computer_roots()
    assert unaffected and unaffected[0] == str(Path.home()), unaffected
    print("7d. Real end-user install CONFIRMED unaffected: with INTELLIFILE_APP_DATA_DIR unset (how the packaged app actually runs), whole_computer_roots() behaves exactly as before -- no regression for real users.")

    # 8. Cross-instance configuration leakage: CANNOT be fully verified
    #    without launching the desktop GUI (forbidden by this task's rules).
    #    Static finding only, confirmed by direct filesystem/source
    #    inspection during the incident investigation (not re-derived here):
    #    desktop/src-tauri/tauri.conf.json has no per-launch webview
    #    data-directory override, and BACKEND_PORT is a single hardcoded
    #    constant (8756) in lib.rs -- so the WebView2 profile
    #    (%LOCALAPPDATA%\com.intellifile.app\EBWebView) and the backend's
    #    origin (http://127.0.0.1:8756) are identical across every launch,
    #    regardless of INTELLIFILE_APP_DATA_DIR. No localStorage/IndexedDB
    #    code was found that explicitly depends on this sharing (see check 4
    #    above), but the shared profile itself remains a structural gap.
    tauri_conf = Path(__file__).resolve().parents[2] / "desktop" / "src-tauri" / "tauri.conf.json"
    conf_text = tauri_conf.read_text(encoding="utf-8")
    assert "dataDirectory" not in conf_text and "userDataFolder" not in conf_text, "if a per-launch data dir was added, update this assertion to match, not fail blindly"
    print("8. STATIC-ONLY finding confirmed: no per-launch WebView2 data-directory override existed before this session's fix (dynamic verification needs a GUI launch, out of scope for this script). The fix itself (desktop/src-tauri/src/lib.rs) is verified below in check 9.")

    # 9. Two independent app-data directories: construct two separate
    #    FileRecordStore/AccessPolicy instances (simulating two concurrent
    #    or sequential isolated launches) and confirm a write to one is
    #    invisible to the other -- the backend-side half of "cross-instance
    #    configuration isolation" (sprint priority item A/D).
    from app.storage import FileRecordStore
    from app.files.identity import build_file_record, new_file_id

    appA = root / "instanceA"
    appB = root / "instanceB"
    appA.mkdir()
    appB.mkdir()
    (appA / "config").mkdir()
    (appB / "config").mkdir()
    AccessPolicy(appA / "config").set("all" if os.environ.get("INTELLIFILE_ALLOW_WHOLE_COMPUTER") == "1" else "limited")
    storeA = FileRecordStore(appA / "files.db")
    storeB = FileRecordStore(appB / "files.db")
    fake = root / "fakefile.txt"
    fake.write_text("synthetic content, not a real personal file")
    rec = build_file_record(fake, file_id=new_file_id(), file_hash="deadbeef" * 8, indexed=True)
    storeA.upsert(rec)
    assert len(storeA.list_active()) == 1, "instance A must see its own write"
    assert len(storeB.list_active()) == 0, "instance B must NOT see instance A's write -- cross-instance leakage"
    assert AccessPolicy(appB / "config").mode == "unset", "instance B's access mode must be unaffected by instance A's"
    storeA.close()
    storeB.close()
    print("9. Two independent app-data directories: a write to instance A's store is fully invisible to instance B (SQLite layer); B's access mode stays 'unset' regardless of A's: OK")

    # 10. The WebView2 isolation fix itself (source-level check only -- the
    #    Rust side cannot be unit-tested without launching a webview,
    #    forbidden under this sprint's safety rules). Confirms the fix is
    #    present, unconditional on nothing but INTELLIFILE_APP_DATA_DIR, and
    #    does not touch the real end-user path.
    lib_rs = (Path(__file__).resolve().parents[2] / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert "WEBVIEW2_USER_DATA_FOLDER" in lib_rs, "the WebView2 isolation fix must be present in lib.rs"
    assert 'std::env::var("INTELLIFILE_APP_DATA_DIR")' in lib_rs, "the fix must be gated on INTELLIFILE_APP_DATA_DIR, matching the backend's own gate"
    print("10. WebView2 isolation fix present in lib.rs, gated on INTELLIFILE_APP_DATA_DIR (source-level only -- NOT dynamically verified by launching the app, per this sprint's safety rules).")

    print("\ncheck_isolation_incident OK: all static/unit-level checks pass. Items 7b, 8 and 10 are disclosed architectural findings/fixes verified at the source level only, not dynamically through a GUI launch.")
finally:
    os.environ.pop("INTELLIFILE_APP_DATA_DIR", None)
    shutil.rmtree(root, ignore_errors=True)
