"""Runs every phase's regression script (backend/scripts/prototype_*.py)
in order and prints one pass/fail summary at the end. Each script is a
real end-to-end check (real PDFs/DOCX generated on the fly, real
embeddings, real SQLite FTS5, real speech for transcription) — this
runner doesn't replace them, it just sequences them and reports the
combined result, since there's no pytest suite wiring these together.

Run with:

    backend/venv/bin/python backend/scripts/run_all_phases.py

Exits non-zero if any phase fails, so it's CI-friendly too.
"""

import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable

# (phase label, script filename). Order matches the README's phase order.
# prototype_transcription.py is macOS-only and skips itself gracefully
# elsewhere (see its own docstring) — it still counts as a normal pass.
PHASES = [
    ("Phase 1 — LanceDB storage", "prototype_lancedb.py"),
    ("Phase 2 — File discovery & watching", "prototype_file_watch.py"),
    ("Phase 3 — Text extraction & chunking", "prototype_extraction.py"),
    ("Phase 4 — Indexing pipeline (keyword + vector)", "prototype_indexing.py"),
    ("Phase 5 — Hybrid search & ranking", "prototype_search.py"),
    ("Phase 6 — Typo/grammar tolerance", "prototype_typo_tolerance.py"),
    ("Phase 7 — Voice search / transcription", "prototype_transcription.py"),
    ("Phase 8 — Visual search (photos)", "prototype_visual_search.py"),
    ("Phase 16 — Activity memory (usage events, sessions, settings)", "prototype_usage.py"),
    ("Phase 17 — User profile, personalized ranking, recommendations", "prototype_profile.py"),
    ("Phase 18 — Query router, metadata filters, escalation, reranker", "prototype_router.py"),
    ("Phase 10 — Power-aware indexing (fake power source)", "prototype_power.py"),
    ("Phase 11 — Reliability: corrupt / vanishing / unreadable files, crash recovery", "prototype_reliability.py"),
    ("Phase 12 — Security: API token, access policy, path validation, untrusted content", "prototype_security.py"),
    ("Phase 19 — Local LLM agent (scripted mechanics + real model)", "prototype_agent.py"),
    ("Phase 20 — Routing evaluation (router vs always-hybrid, reranker)", "evaluate_routing.py"),
    ("Phase 20 — Personalization evaluation (lift vs harm, weight sweep)", "evaluate_personalization.py"),
    ("Phase 13 — Retrieval / chunking / resource benchmarks", "evaluate_retrieval.py"),
    ("Phase 13 — Embedding model comparison", "evaluate_embeddings.py"),
    ("Improvement 6 — Learned router vs rules (5-fold CV)", "evaluate_learned_router.py"),
]


def main() -> int:
    results = []
    for label, filename in PHASES:
        script_path = SCRIPTS_DIR / filename
        print(f"\n{'=' * 70}\n{label}  ({filename})\n{'=' * 70}")
        start = time.monotonic()
        proc = subprocess.run([PYTHON, str(script_path)], cwd=SCRIPTS_DIR.parent)
        elapsed = time.monotonic() - start
        passed = proc.returncode == 0
        results.append((label, passed, elapsed))
        if not passed:
            print(f"\n!! {label} FAILED (exit code {proc.returncode}) !!")

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for label, passed, elapsed in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {label}  ({elapsed:.1f}s)")

    failures = [label for label, passed, _ in results if not passed]
    total = len(results)
    print(f"\n{total - len(failures)}/{total} phases passed.")
    if failures:
        print("Failed: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
