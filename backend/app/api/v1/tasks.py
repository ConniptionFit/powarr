from fastapi import APIRouter

from app.services.tasks import list_active_tasks, TaskProgress

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskProgress])
def get_active_tasks():
    """Snapshot of currently-running tracked operations, for the Active Processes
    tray to hydrate on mount — live updates afterward arrive over the existing
    /imports/events SSE stream as "task_update" events."""
    return list_active_tasks()
