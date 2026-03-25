"""Seed demo status data for VHS recording."""

from open_orchestrator.core.status import StatusTracker
from open_orchestrator.models.status import AIActivityStatus

t = StatusTracker()
for s in t.get_all_statuses():
    if "authentication" in s.worktree_name:
        t.update_task(s.worktree_name, "Implementing JWT auth flow", AIActivityStatus.WORKING)
    elif "documentation" in s.worktree_name:
        t.update_task(s.worktree_name, "Writing endpoint docs", AIActivityStatus.WORKING)
    elif "tests" in s.worktree_name:
        t.update_task(s.worktree_name, "Waiting for input", AIActivityStatus.WAITING)
