"""Ephemeral in-memory tracking of long-running, user-triggered background
operations (LLM batch runs, scans, Plex sync, deletions) for the frontend's
Active Processes tray. No DB persistence — this is UI state, not data, and
doesn't need to survive a restart.

Progress fans out over the existing /imports/events SSE bus
(import_matcher.publish()) rather than a second one — media_llm.py already
established that convention for a different domain's events, so this is the
third, not the first, non-imports consumer of that "endpoint" (the name is
historical; it's the app's general event bus)."""
import asyncio
import time
import uuid
from typing import Optional

from pydantic import BaseModel

from app.services.import_matcher import publish

_GC_AGE_SECONDS = 60  # finished tasks older than this are purged on the next create_task

# Strong references to fire-and-forget background coroutines. asyncio only keeps a
# weak reference to a bare create_task() result, so without this the loop can GC
# and silently cancel an in-flight LLM/scan run mid-way.
_background: set["asyncio.Task"] = set()


def spawn_background(coro) -> "asyncio.Task":
    """Schedule a fire-and-forget coroutine on the running loop, retaining a strong
    reference until it finishes. Use this instead of
    asyncio.get_event_loop().create_task (deprecated in 3.12 and GC-unsafe)."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


class TaskProgress(BaseModel):
    id: str
    kind: str  # "llm_run" | "scan" | "plex_sync" | "deletion" | "import_batch"
    label: str
    status: str = "running"  # running | done | failed
    current: Optional[int] = None
    total: Optional[int] = None
    message: Optional[str] = None
    started_at: float  # unix timestamp — simpler JSON than datetime, frontend only needs relative time


_tasks: dict[str, TaskProgress] = {}


def _gc() -> None:
    cutoff = time.time() - _GC_AGE_SECONDS
    stale = [tid for tid, t in _tasks.items() if t.status != "running" and t.started_at < cutoff]
    for tid in stale:
        _tasks.pop(tid, None)


def create_task(kind: str, label: str, total: Optional[int] = None) -> str:
    _gc()
    task_id = str(uuid.uuid4())
    task = TaskProgress(id=task_id, kind=kind, label=label, total=total, started_at=time.time())
    _tasks[task_id] = task
    publish({"type": "task_update", "task": task.model_dump()})
    return task_id


def update_task(task_id: str, current: Optional[int] = None, total: Optional[int] = None,
                message: Optional[str] = None, label: Optional[str] = None) -> None:
    task = _tasks.get(task_id)
    if not task:
        return
    if current is not None:
        task.current = current
    if total is not None:
        task.total = total
    if message is not None:
        task.message = message
    if label is not None:
        task.label = label
    publish({"type": "task_update", "task": task.model_dump()})


def finish_task(task_id: str, status: str, message: Optional[str] = None) -> None:
    """status: "done" | "failed"."""
    task = _tasks.get(task_id)
    if not task:
        return
    task.status = status
    if message is not None:
        task.message = message
    publish({"type": "task_update", "task": task.model_dump()})


def list_active_tasks() -> list[TaskProgress]:
    """Snapshot for a client hydrating on page load — SSE only pushes updates
    from the moment it connects, so a task already 60% done needs this to be
    visible immediately rather than waiting for its next update. Only
    currently-running tasks: a finished one has no "just happened" context
    left by the time a fresh page load asks for it."""
    return [t for t in _tasks.values() if t.status == "running"]
