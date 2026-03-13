"""
Core modules for Open Orchestrator.

This package contains the core business logic for:
- Worktree management
- Project detection
- Environment setup
- tmux session management
- Cleanup and maintenance
- Synchronization
- Status tracking
"""

from open_orchestrator.core.agent_detector import (
    detect_all_agents,
    detect_installed_agents,
)
from open_orchestrator.core.branch_namer import (
    generate_branch_name,
)
from open_orchestrator.core.cleanup import (
    CleanupConfig,
    CleanupReport,
    CleanupService,
    UsageTracker,
    WorktreeUsageStats,
)
from open_orchestrator.core.merge import (
    MergeConflictError,
    MergeError,
    MergeManager,
    MergeResult,
    MergeStatus,
)
from open_orchestrator.core.pane_actions import (
    PaneActionError,
    PaneResult,
    create_pane,
    popup_result_path,
    remove_pane,
)
from open_orchestrator.core.status import (
    StatusConfig,
    StatusTracker,
)
from open_orchestrator.core.sync import (
    SyncConfig,
    SyncReport,
    SyncService,
    SyncStatus,
    WorktreeSyncResult,
)

__all__ = [
    "CleanupConfig",
    "CleanupReport",
    "CleanupService",
    "UsageTracker",
    "WorktreeUsageStats",
    "SyncConfig",
    "SyncReport",
    "SyncService",
    "SyncStatus",
    "WorktreeSyncResult",
    "StatusConfig",
    "StatusTracker",
    "detect_all_agents",
    "detect_installed_agents",
    "generate_branch_name",
    "MergeConflictError",
    "MergeError",
    "MergeManager",
    "MergeResult",
    "MergeStatus",
    "PaneActionError",
    "PaneResult",
    "create_pane",
    "popup_result_path",
    "remove_pane",
]
