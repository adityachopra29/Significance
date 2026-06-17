"""In-process SSE event hub for feed updates."""
from __future__ import annotations

import json
import queue
import threading
from typing import Any

_lock = threading.Lock()
_subscribers: list[queue.Queue[str]] = []


def publish(event: str, data: dict[str, Any]) -> None:
    """Broadcast to all SSE clients (thread-safe; callable from sync worker)."""
    payload = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
    with _lock:
        dead: list[queue.Queue[str]] = []
        for q in _subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            if q in _subscribers:
                _subscribers.remove(q)


def register() -> queue.Queue[str]:
    q: queue.Queue[str] = queue.Queue(maxsize=200)
    with _lock:
        _subscribers.append(q)
    return q


def unregister(q: queue.Queue[str]) -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)
