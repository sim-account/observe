import threading
import time
from typing import Any, Dict, Optional

_store: Dict[str, dict] = {}
_lock = threading.Lock()
_TTL = 2 * 60 * 60  # 2 hours


def _cleanup():
    while True:
        time.sleep(300)
        now = time.time()
        with _lock:
            stale = [k for k, v in _store.items() if now - v["created_at"] > _TTL]
            for k in stale:
                del _store[k]


threading.Thread(target=_cleanup, daemon=True).start()


def create(session_id: str) -> dict:
    entry = {"created_at": time.time(), "discovered_model": None, "baseline_model": None, "traces": None}
    with _lock:
        _store[session_id] = entry
    return entry


def get(session_id: str) -> Optional[dict]:
    with _lock:
        return _store.get(session_id)


def update(session_id: str, **kwargs: Any) -> bool:
    with _lock:
        if session_id not in _store:
            return False
        _store[session_id].update(kwargs)
        return True
