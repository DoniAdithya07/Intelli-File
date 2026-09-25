"""Offline guard (Phase 12's "verify offline operation", added 2026-09-21).

With INTELLIFILE_OFFLINE_GUARD=1 the backend patches Python's socket layer
so that any attempt to connect or resolve a name outside this machine is
refused and counted. The live test suite then exercises every feature —
indexing, keyword/semantic/hybrid search, voice, photos, the profile, the
router, the agent — against the guarded server and asserts the count is
still zero. That turns "the app is offline" from a claim into a measured
fact, and it would catch a future dependency that phones home.

The guard is only for testing: a shipped build simply has nothing to
connect to, and it must not depend on this patch for its privacy.
"""

import os
import socket
import threading

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", None}
_state = {"enabled": False, "blocked": []}
_lock = threading.Lock()


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    return host in _LOOPBACK or (isinstance(host, str) and (host.startswith("127.") or host.startswith("::ffff:127.")))


def _block(kind: str, target) -> None:
    with _lock:
        _state["blocked"].append({"kind": kind, "target": str(target)})
    raise OSError(f"offline guard: blocked {kind} to {target!r}")


def install() -> None:
    if _state["enabled"]:
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(host):
            _block("connect", address)
        return real_connect(self, address)

    def connect_ex(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_local(host):
            _block("connect", address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            _block("dns", host)
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    # Belt and braces for the model libraries: never even try the Hub.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    _state["enabled"] = True


def status() -> dict:
    with _lock:
        return {"enabled": _state["enabled"], "blocked_attempts": len(_state["blocked"]), "blocked": list(_state["blocked"][-20:])}


def wanted() -> bool:
    return os.environ.get("INTELLIFILE_OFFLINE_GUARD", "") not in ("", "0", "false", "no")
