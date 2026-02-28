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

from open_orchestrator.core.cleanup import (
    CleanupConfig,
    CleanupReport,
    CleanupService,
    UsageTracker,
    WorktreeUsageStats,
)
from open_orchestrator.core.dashboard import (
    Dashboard,
    DashboardConfig,
)
from open_orchestrator.core.hooks import (
    HookError,
    HookExecutionError,
    HooksConfig,
    HookService,
    get_hook_type_for_status,
)
from open_orchestrator.core.pr_linker import (
    GitHubAPIError,
    PRLinker,
    PRLinkerConfig,
    PRLinkError,
    PRNotFoundError,
)
from open_orchestrator.core.process_manager import (
    ProcessAlreadyRunningError,
    ProcessError,
    ProcessInfo,
    ProcessManager,
    ProcessManagerConfig,
    ProcessNotFoundError,
)
from open_orchestrator.core.session import (
    SessionConfig,
    SessionCopyError,
    SessionError,
    SessionManager,
    SessionNotFoundError,
)
from open_orchestrator.core.skill_installer import (
    SkillInstaller,
    SkillInstallError,
    SkillNotFoundError,
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
from open_orchestrator.core.agent_detector import (
    detect_all_agents,
    detect_installed_agents,
)
from open_orchestrator.core.branch_namer import (
    generate_branch_name,
)
from open_orchestrator.core.merge import (
    MergeConflictError,
    MergeError,
    MergeManager,
    MergeResult,
    MergeStatus,
)

__all__ = [
    "CleanupConfig",
    "CleanupReport",
    "CleanupService",
    "UsageTracker",
    "WorktreeUsageStats",
    "Dashboard",
    "DashboardConfig",
    "get_hook_type_for_status",
    "HookError",
    "HookExecutionError",
    "HooksConfig",
    "HookService",
    "GitHubAPIError",
    "PRLinker",
    "PRLinkerConfig",
    "PRLinkError",
    "PRNotFoundError",
    "ProcessAlreadyRunningError",
    "ProcessError",
    "ProcessInfo",
    "ProcessManager",
    "ProcessManagerConfig",
    "ProcessNotFoundError",
    "SessionConfig",
    "SessionCopyError",
    "SessionError",
    "SessionManager",
    "SessionNotFoundError",
    "SyncConfig",
    "SyncReport",
    "SyncService",
    "SyncStatus",
    "WorktreeSyncResult",
    "SkillInstallError",
    "SkillInstaller",
    "SkillNotFoundError",
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
]
