import logging
import time
import threading
from collections import deque

log = logging.getLogger(__name__)


class EventLog:
    def __init__(self, max_size=200):
        self._events = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def append(self, event_type: str, detail: str, target: str | None = None):
        event = {
            "time": time.time(),
            "type": event_type,
            "detail": detail,
            "target": target,
        }
        with self._lock:
            self._events.append(event)

    def get_recent(self, limit: int = 50, event_type: str | None = None) -> list[dict]:
        with self._lock:
            events = list(self._events)
        events.reverse()
        if event_type:
            events = [e for e in events if e["type"] == event_type]
        return events[:limit]
