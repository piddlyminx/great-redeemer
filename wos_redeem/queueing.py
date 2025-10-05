from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from threading import Lock
from typing import Callable, Deque, Generic, Iterable, List, Optional, Tuple, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class QueueItem:
    """A unit of work for the redemption worker.

    Identifies a user/code pair by stable IDs and carries display helpers.
    """

    user_id: int
    fid: int
    name: Optional[str]
    gift_code_id: int
    code: str

    def key(self) -> Tuple[int, int]:
        return (self.user_id, self.gift_code_id)


class ObservableQueue(Generic[T]):
    """A minimal thread-safe FIFO queue with change observers.

    - Uses an internal deque with a single Lock for coarse-grained safety.
    - Observers receive event name and a shallow copy snapshot of the queue
      whenever the contents change.
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._dq: Deque[T] = deque()
        self._lock = Lock()
        self._observers: List[Callable[[str, List[T]], None]] = []
        self._maxsize = maxsize if maxsize > 0 else 0

    def register(self, cb: Callable[[str, List[T]], None]) -> None:
        with self._lock:
            self._observers.append(cb)

    def _notify(self, event: str) -> None:
        snap: List[T]
        with self._lock:
            snap = list(self._dq)
        # Notify without holding the lock to avoid re-entrancy issues
        for cb in list(self._observers):
            try:
                cb(event, snap)
            except Exception:
                # Observers are best-effort; ignore failures
                pass

    def put(self, item: T) -> None:
        with self._lock:
            if self._maxsize and len(self._dq) >= self._maxsize:
                # Drop-tail policy for simplicity
                return
            self._dq.append(item)
        self._notify("put")

    def extend(self, items: Iterable[T]) -> None:
        changed = False
        with self._lock:
            for it in items:
                if self._maxsize and len(self._dq) >= self._maxsize:
                    break
                self._dq.append(it)
                changed = True
        if changed:
            self._notify("put_many")

    def get(self) -> Optional[T]:
        with self._lock:
            if not self._dq:
                return None
            it = self._dq.popleft()
        self._notify("get")
        return it

    def peek(self, n: int) -> List[T]:
        with self._lock:
            return list(list(self._dq)[: max(0, n)])

    def clear(self) -> None:
        with self._lock:
            self._dq.clear()
        self._notify("clear")

    def __len__(self) -> int:  # pragma: no cover - tiny wrapper
        with self._lock:
            return len(self._dq)


class _WorkerState:
    """Singleton container for cross-thread worker state."""

    def __init__(self) -> None:
        # Unbounded by default; the worker controls fill level
        self.queue: ObservableQueue[QueueItem] = ObservableQueue()
        # Keys of items currently in the queue to avoid duplicates
        self._keys: set[tuple[int, int]] = set()
        self._lock = Lock()

    def add_unique(self, items: Iterable[QueueItem]) -> int:
        added = 0
        with self._lock:
            for it in items:
                k = it.key()
                if k in self._keys:
                    continue
                self._keys.add(k)
                self.queue.put(it)
                added += 1
        return added

    def pop(self) -> Optional[QueueItem]:
        it = self.queue.get()
        if it is None:
            return None
        with self._lock:
            self._keys.discard(it.key())
        return it

    def snapshot(self, limit: int = 10) -> List[QueueItem]:
        return self.queue.peek(limit)

    def clear(self) -> None:
        with self._lock:
            self._keys.clear()
        self.queue.clear()


# Module-level singleton used by tasks/app
worker_state = _WorkerState()

