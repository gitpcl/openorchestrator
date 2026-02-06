"""
Status tracking service for worktree AI tool sessions.

This module provides functionality to:
- Track what AI tools (Claude, OpenCode, Droid) are doing in each worktree
- Record commands sent between worktrees
- Generate status summaries across all worktrees
- Monitor health and detect issues
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from open_orchestrator.config import AITool
from open_orchestrator.models.status import (
    AIActivityStatus,
    StatusStore,
    StatusSummary,
    WorktreeAIStatus,
)
from open_orchestrator.utils.io import atomic_write_text, shared_file_lock

if TYPE_CHECKING:
    from open_orchestrator.models.status import HealthReport, HealthSummary


@dataclass
class StatusConfig:
    """Configuration for status tracking."""

    storage_path: Path | None = None
    max_command_history: int = 20
    auto_cleanup_orphans: bool = True
    store_commands: bool = True
    redact_commands: bool = True
    enable_hooks: bool = True

    def __post_init__(self) -> None:
        if self.max_command_history < 1:
            raise ValueError("max_command_history must be at least 1")


class StatusTracker:
    """
    Tracks and persists AI tool activity status for worktrees.

    This service maintains a JSON store of what AI tools are doing
    in each worktree, allowing the main worktree to see activity
    across all parallel development sessions.
    """

    DEFAULT_STATUS_FILENAME = "ai_status.json"

    def __init__(self, config: StatusConfig | None = None):
        self.config = config or StatusConfig()
        self._storage_path = self.config.storage_path or self._get_default_path()
        self._store: StatusStore = StatusStore()
        self._hook_service = None
        self._load_store()

    def _get_hook_service(self):
        """Lazy-load the hook service to avoid circular imports."""
        if self._hook_service is None and self.config.enable_hooks:
            from open_orchestrator.core.hooks import HookService

            self._hook_service = HookService()
        return self._hook_service

    def _trigger_hooks(
        self,
        old_status: AIActivityStatus | None,
        new_status: AIActivityStatus,
        worktree_name: str,
        task: str | None = None,
    ) -> None:
        """Trigger appropriate hooks for a status change."""
        if not self.config.enable_hooks:
            return

        hook_service = self._get_hook_service()
        if not hook_service:
            return

        from open_orchestrator.core.hooks import get_hook_type_for_status

        hook_type = get_hook_type_for_status(old_status, new_status)

        context = {
            "status": new_status.value if hasattr(new_status, "value") else str(new_status),
            "old_status": old_status.value
            if old_status and hasattr(old_status, "value")
            else str(old_status)
            if old_status
            else "",
            "task": task or "",
        }

        try:
            hook_service.trigger_hooks(hook_type, worktree_name, context)
        except Exception:
            pass

    def _get_default_path(self) -> Path:
        """Get default path for status storage in user's home directory."""
        return Path.home() / ".open-orchestrator" / self.DEFAULT_STATUS_FILENAME

    def _load_store(self) -> None:
        """Load status store from persistent storage."""
        if self._storage_path.exists():
            try:
                with open(self._storage_path) as f:
                    with shared_file_lock(f):
                        data = json.load(f)
                        self._store = StatusStore.model_validate(data)
            except (OSError, json.JSONDecodeError, ValueError):
                self._store = StatusStore()
        else:
            self._store = StatusStore()

    def _save_store(self) -> None:
        """Persist status store to storage using atomic write and 0o600 perms."""
        data = json.dumps(
            self._store.model_dump(mode="json"),
            indent=2,
            default=str,
        )
        atomic_write_text(self._storage_path, data, perms=0o600)

    def get_status(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Get status for a specific worktree."""
        return self._store.get_status(worktree_name)

    def get_all_statuses(self) -> list[WorktreeAIStatus]:
        """Get statuses for all tracked worktrees."""
        return self._store.get_all_statuses()

    def initialize_status(
        self,
        worktree_name: str,
        worktree_path: str,
        branch: str,
        tmux_session: str | None = None,
        ai_tool: AITool | str = AITool.CLAUDE,
    ) -> WorktreeAIStatus:
        """
        Initialize status tracking for a new worktree.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Absolute path to the worktree
            branch: Git branch name
            tmux_session: Associated tmux session name
            ai_tool: AI tool being used (claude, opencode, droid)

        Returns:
            The newly created WorktreeAIStatus
        """
        ai_tool_str = ai_tool.value if isinstance(ai_tool, AITool) else ai_tool

        status = WorktreeAIStatus(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            branch=branch,
            tmux_session=tmux_session,
            ai_tool=ai_tool_str,
            activity_status=AIActivityStatus.IDLE,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self._store.set_status(status)
        self._save_store()
        return status

    def update_task(
        self, worktree_name: str, task: str, status: AIActivityStatus = AIActivityStatus.WORKING
    ) -> WorktreeAIStatus | None:
        """
        Update the current task for a worktree.

        Args:
            worktree_name: Name of the worktree
            task: Description of the current task
            status: Activity status (default: WORKING)

        Returns:
            Updated WorktreeAIStatus or None if not found
        """
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        old_status = wt_status.activity_status
        wt_status.update_task(task, status)
        self._store.set_status(wt_status)
        self._save_store()

        # Trigger hooks for status change
        self._trigger_hooks(old_status, status, worktree_name, task)

        return wt_status

    def record_command(
        self, target_worktree: str, command: str, source_worktree: str | None = None, pane_index: int = 0, window_index: int = 0
    ) -> WorktreeAIStatus | None:
        """
        Record a command sent to a worktree.

        Args:
            target_worktree: Name of the worktree receiving the command
            command: The command that was sent
            source_worktree: Name of the worktree that sent the command (None if manual)
            pane_index: Target pane index
            window_index: Target window index

        Returns:
            Updated WorktreeAIStatus or None if not found
        """
        wt_status = self._store.get_status(target_worktree)

        if not wt_status:
            return None

        if self.config.redact_commands:
            command_to_store = self._sanitize_command(command)
        else:
            command_to_store = command

        if self.config.store_commands:
            wt_status.add_command(
                command=command_to_store,
                source_worktree=source_worktree,
                pane_index=pane_index,
                window_index=window_index,
                max_history=self.config.max_command_history,
            )

        if wt_status.activity_status == AIActivityStatus.IDLE:
            wt_status.activity_status = AIActivityStatus.WORKING

        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def mark_completed(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Mark a worktree's current task as completed."""
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        old_status = wt_status.activity_status
        task = wt_status.current_task
        wt_status.mark_completed()
        self._store.set_status(wt_status)
        self._save_store()

        # Trigger hooks for completion
        self._trigger_hooks(old_status, AIActivityStatus.COMPLETED, worktree_name, task)

        return wt_status

    def mark_idle(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Mark a worktree as idle."""
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        old_status = wt_status.activity_status
        wt_status.mark_idle()
        self._store.set_status(wt_status)
        self._save_store()

        # Trigger hooks for idle state
        self._trigger_hooks(old_status, AIActivityStatus.IDLE, worktree_name)

        return wt_status

    def set_notes(self, worktree_name: str, notes: str) -> WorktreeAIStatus | None:
        """Set notes for a worktree."""
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        wt_status.notes = notes
        wt_status.updated_at = datetime.now()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def remove_status(self, worktree_name: str) -> bool:
        """
        Remove status tracking for a worktree.

        Returns:
            True if removed, False if not found
        """
        removed = self._store.remove_status(worktree_name)

        if removed:
            self._save_store()

        return removed

    def get_summary(self, worktree_names: list[str] | None = None) -> StatusSummary:
        """
        Generate a summary of AI tool status across worktrees.

        Args:
            worktree_names: Optional list of worktree names to filter by.
                          If None, includes all tracked worktrees.

        Returns:
            StatusSummary with aggregated statistics
        """
        all_statuses = self._store.get_all_statuses()

        if worktree_names:
            all_statuses = [s for s in all_statuses if s.worktree_name in worktree_names]

        summary = StatusSummary(
            timestamp=datetime.now(),
            total_worktrees=len(worktree_names) if worktree_names else len(all_statuses),
            worktrees_with_status=len(all_statuses),
            statuses=all_statuses,
        )

        for status in all_statuses:
            if status.activity_status == AIActivityStatus.WORKING:
                summary.active_ai_sessions += 1
            elif status.activity_status == AIActivityStatus.IDLE:
                summary.idle_ai_sessions += 1
            elif status.activity_status == AIActivityStatus.BLOCKED:
                summary.blocked_ai_sessions += 1
            else:
                summary.unknown_status += 1

            summary.total_commands_sent += len(status.recent_commands)

            # Aggregate token usage
            summary.total_input_tokens += status.token_usage.input_tokens
            summary.total_output_tokens += status.token_usage.output_tokens
            summary.total_estimated_cost_usd += status.token_usage.estimated_cost_usd

            if status.updated_at:
                if summary.most_recent_activity is None or status.updated_at > summary.most_recent_activity:
                    summary.most_recent_activity = status.updated_at

        return summary

    def cleanup_orphans(self, valid_worktree_names: list[str]) -> list[str]:
        """
        Remove status entries for worktrees that no longer exist.

        Args:
            valid_worktree_names: List of currently valid worktree names

        Returns:
            List of removed worktree names
        """
        removed = []
        current_names = [s.worktree_name for s in self._store.get_all_statuses()]

        for name in current_names:
            if name not in valid_worktree_names:
                self._store.remove_status(name)
                removed.append(name)

        if removed:
            self._save_store()

        return removed

    def set_status(self, status: WorktreeAIStatus) -> None:
        """Public API to persist a WorktreeAIStatus update."""
        self._store.set_status(status)
        self._save_store()

    def update_token_usage(
        self,
        worktree_name: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> WorktreeAIStatus | None:
        """
        Update token usage for a worktree.

        Args:
            worktree_name: Name of the worktree
            input_tokens: Number of input tokens to add
            output_tokens: Number of output tokens to add
            cache_read_tokens: Number of cache read tokens to add
            cache_write_tokens: Number of cache write tokens to add

        Returns:
            Updated WorktreeAIStatus or None if not found
        """
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        wt_status.update_token_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def reset_token_usage(self, worktree_name: str) -> WorktreeAIStatus | None:
        """Reset token usage to zero for a worktree."""
        wt_status = self._store.get_status(worktree_name)

        if not wt_status:
            return None

        wt_status.reset_token_usage()
        self._store.set_status(wt_status)
        self._save_store()
        return wt_status

    def _sanitize_command(self, text: str) -> str:
        """Best-effort redaction of secrets in commands."""
        import re as _re

        redactions = [
            # Authorization: Bearer <token>
            (r"(Authorization\s*:\s*Bearer\s+)[^\s]+", r"\1[REDACTED]"),
            # password=... or password: ... (with optional quotes)
            (r'(?i)(password\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            # api key/token patterns (with optional quotes)
            (r'(?i)(api[_-]?key\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            (r'(?i)(token\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            (r'(?i)(secret\s*[:=]\s*)["\']?([^"\'\s]+)["\']?', r"\1[REDACTED]"),
            # URLs with embedded credentials (user:pass@host)
            (r"(https?://)[^/:@\s]+:[^/:@\s]+@", r"\1[REDACTED]:[REDACTED]@"),
            # JWT tokens (three base64 segments separated by dots)
            (r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[JWT REDACTED]"),
            # AWS Access Key ID pattern
            (r"AKIA[0-9A-Z]{16}", "AKIA[REDACTED]"),
            # AWS Secret Access Key (40 character base64)
            (r"(?i)(aws_secret_access_key\s*[:=]\s*)[A-Za-z0-9/+=]{40}", r"\1[REDACTED]"),
            # Private key block markers
            (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC )?PRIVATE KEY-----", "[PRIVATE KEY REDACTED]"),
        ]
        redacted = text
        for pat, repl in redactions:
            redacted = _re.sub(pat, repl, redacted)
        return redacted

    def get_current_worktree_name(self) -> str | None:
        """
        Get the worktree name for the current directory.

        Returns:
            Worktree name if in a tracked worktree, None otherwise
        """
        current_path = str(Path.cwd())

        for status in self._store.get_all_statuses():
            if current_path.startswith(status.worktree_path):
                return status.worktree_name

        return None

    def check_health(
        self,
        worktree_name: str,
        stuck_threshold_minutes: int = 30,
        high_token_threshold: int = 100_000,
        high_cost_threshold_usd: float = 10.0,
        stale_threshold_days: int = 7,
        idle_threshold_hours: int = 24,
    ) -> "HealthReport":
        """
        Check the health of a worktree and detect issues.

        Detects:
        - Stuck tasks (same task for too long)
        - High token usage (possible infinite loop)
        - High cost (expensive session)
        - Repeated errors (failed commands)
        - Stale worktrees (no activity for days)
        - Idle too long (no productive work)
        - Blocked state

        Args:
            worktree_name: Name of the worktree to check
            stuck_threshold_minutes: Minutes before task is considered stuck
            high_token_threshold: Token count threshold for high usage warning
            high_cost_threshold_usd: Cost threshold in USD
            stale_threshold_days: Days of inactivity before stale warning
            idle_threshold_hours: Hours of idle before warning

        Returns:
            HealthReport with detected issues and recommendations
        """
        from open_orchestrator.models.status import (
            HealthIssue,
            HealthIssueType,
            HealthIssueSeverity,
            HealthReport,
        )

        status = self.get_status(worktree_name)

        if not status:
            # Worktree not tracked
            return HealthReport(
                worktree_name=worktree_name,
                healthy=False,
                issues=[
                    HealthIssue(
                        issue_type=HealthIssueType.STALE_WORKTREE,
                        severity=HealthIssueSeverity.WARNING,
                        message="Worktree not tracked in status system",
                        recommendation="Initialize status tracking with: owt status --set-status working",
                    )
                ],
            )

        issues: list[HealthIssue] = []
        now = datetime.now()

        # Check 1: Stuck task (same task for too long)
        if status.last_task_update and status.current_task:
            stuck_duration = (now - status.last_task_update).total_seconds() / 60
            if stuck_duration > stuck_threshold_minutes:
                issues.append(
                    HealthIssue(
                        issue_type=HealthIssueType.STUCK_TASK,
                        severity=HealthIssueSeverity.WARNING,
                        message=f"AI appears stuck on same task for {int(stuck_duration)} minutes",
                        recommendation=f"Try: owt send {worktree_name} \"Let's try a different approach\"",
                        details={"stuck_minutes": int(stuck_duration), "task": status.current_task},
                    )
                )

        # Check 2: High token usage (possible runaway loop)
        if status.token_usage.total_tokens > high_token_threshold:
            issues.append(
                HealthIssue(
                    issue_type=HealthIssueType.HIGH_TOKEN_USAGE,
                    severity=HealthIssueSeverity.CRITICAL,
                    message=f"Very high token usage detected: {status.token_usage.total_tokens:,} tokens",
                    recommendation="Check for infinite loops or consider switching to a cheaper AI tool",
                    details={
                        "total_tokens": status.token_usage.total_tokens,
                        "input_tokens": status.token_usage.input_tokens,
                        "output_tokens": status.token_usage.output_tokens,
                    },
                )
            )

        # Check 3: High cost
        if status.token_usage.estimated_cost_usd > high_cost_threshold_usd:
            issues.append(
                HealthIssue(
                    issue_type=HealthIssueType.HIGH_COST,
                    severity=HealthIssueSeverity.WARNING,
                    message=f"High cost session: ${status.token_usage.estimated_cost_usd:.2f}",
                    recommendation="Consider switching to a cheaper AI tool (claude-haiku, gpt-4o-mini)",
                    details={"cost_usd": status.token_usage.estimated_cost_usd},
                )
            )

        # Check 4: Repeated errors (failed commands)
        if status.recent_commands:
            error_keywords = ["error", "failed", "fail", "exception", "traceback"]
            error_commands = [
                cmd for cmd in status.recent_commands[-10:] if any(keyword in cmd.command.lower() for keyword in error_keywords)
            ]
            if len(error_commands) >= 3:
                issues.append(
                    HealthIssue(
                        issue_type=HealthIssueType.REPEATED_ERRORS,
                        severity=HealthIssueSeverity.WARNING,
                        message=f"Multiple error-related commands detected ({len(error_commands)} in last 10)",
                        recommendation="AI may be blocked. Review errors or reset the session",
                        details={"error_count": len(error_commands)},
                    )
                )

        # Check 5: Stale worktree (no activity for days)
        if status.updated_at:
            stale_duration = (now - status.updated_at).days
            if stale_duration >= stale_threshold_days:
                issues.append(
                    HealthIssue(
                        issue_type=HealthIssueType.STALE_WORKTREE,
                        severity=HealthIssueSeverity.INFO,
                        message=f"No activity for {stale_duration} days",
                        recommendation=f"Consider cleanup: owt delete {worktree_name}",
                        details={"days_inactive": stale_duration},
                    )
                )

        # Check 6: Idle too long
        if status.activity_status == AIActivityStatus.IDLE and status.updated_at:
            idle_duration = (now - status.updated_at).total_seconds() / 3600
            if idle_duration > idle_threshold_hours:
                issues.append(
                    HealthIssue(
                        issue_type=HealthIssueType.IDLE_TOO_LONG,
                        severity=HealthIssueSeverity.INFO,
                        message=f"AI idle for {int(idle_duration)} hours",
                        recommendation="Send a task or clean up the worktree",
                        details={"idle_hours": int(idle_duration)},
                    )
                )

        # Check 7: Blocked state
        if status.activity_status == AIActivityStatus.BLOCKED:
            issues.append(
                HealthIssue(
                    issue_type=HealthIssueType.BLOCKED_STATE,
                    severity=HealthIssueSeverity.CRITICAL,
                    message="AI is in blocked state",
                    recommendation="Review the blocking issue and provide guidance",
                    details={"notes": status.notes or "No notes provided"},
                )
            )

        # Determine overall health (no critical issues = healthy)
        critical_issues = [i for i in issues if i.severity == HealthIssueSeverity.CRITICAL]
        healthy = len(critical_issues) == 0

        return HealthReport(
            worktree_name=worktree_name,
            timestamp=now,
            healthy=healthy,
            issues=issues,
            status=status,
        )

    def check_all_health(
        self,
        stuck_threshold_minutes: int = 30,
        high_token_threshold: int = 100_000,
        high_cost_threshold_usd: float = 10.0,
        stale_threshold_days: int = 7,
        idle_threshold_hours: int = 24,
    ) -> "HealthSummary":
        """
        Check health of all tracked worktrees.

        Args:
            stuck_threshold_minutes: Minutes before task is considered stuck
            high_token_threshold: Token count threshold for high usage warning
            high_cost_threshold_usd: Cost threshold in USD
            stale_threshold_days: Days of inactivity before stale warning
            idle_threshold_hours: Hours of idle before warning

        Returns:
            HealthSummary with reports for all worktrees
        """
        from open_orchestrator.models.status import HealthSummary

        all_statuses = self.get_all_statuses()
        reports = []

        for status in all_statuses:
            report = self.check_health(
                worktree_name=status.worktree_name,
                stuck_threshold_minutes=stuck_threshold_minutes,
                high_token_threshold=high_token_threshold,
                high_cost_threshold_usd=high_cost_threshold_usd,
                stale_threshold_days=stale_threshold_days,
                idle_threshold_hours=idle_threshold_hours,
            )
            reports.append(report)

        # Calculate summary stats
        healthy = sum(1 for r in reports if r.healthy)
        with_warnings = sum(1 for r in reports if not r.healthy and not r.critical_issues)
        with_critical = sum(1 for r in reports if r.critical_issues)

        return HealthSummary(
            timestamp=datetime.now(),
            total_worktrees=len(reports),
            healthy_worktrees=healthy,
            worktrees_with_warnings=with_warnings,
            worktrees_with_critical_issues=with_critical,
            reports=reports,
        )

    def recommend_ai_tool(
        self,
        task_description: str,
        budget_usd: float | None = None,
        prefer_quality: bool = False,
    ) -> dict[str, Any]:
        """
        Recommend the most cost-effective AI tool for a task.

        Uses simple heuristics based on task keywords:
        - Simple/trivial tasks → cheaper models (haiku, gpt-4o-mini)
        - Complex/research tasks → premium models (opus, gpt-4o)
        - Medium tasks → balanced models (sonnet, gpt-4o)

        Args:
            task_description: Description of the task
            budget_usd: Optional budget constraint in USD
            prefer_quality: Prefer quality over cost

        Returns:
            Dictionary with recommended tool, reasoning, and alternatives
        """
        from open_orchestrator.config import AITool

        task_lower = task_description.lower()

        # Keywords for complexity classification
        simple_keywords = ["typo", "fix", "small", "quick", "simple", "minor", "doc", "comment", "rename"]
        complex_keywords = [
            "architecture",
            "design",
            "refactor",
            "security",
            "performance",
            "research",
            "complex",
            "algorithm",
        ]

        # Detect task complexity
        is_simple = any(keyword in task_lower for keyword in simple_keywords)
        is_complex = any(keyword in task_lower for keyword in complex_keywords)

        # Recommendation logic
        if prefer_quality or is_complex:
            recommended = "claude-opus"
            reasoning = "Complex task requires high-quality reasoning"
            alternatives = ["gpt-4o", "claude-sonnet"]
        elif is_simple:
            recommended = "claude-haiku"
            reasoning = "Simple task can use cost-effective model"
            alternatives = ["gpt-4o-mini", "claude-sonnet"]
        else:
            recommended = "claude-sonnet"
            reasoning = "Balanced quality and cost for general tasks"
            alternatives = ["gpt-4o", "claude-haiku"]

        # Check budget constraint
        if budget_usd is not None:
            # Estimate tokens (rough heuristic: 1000 tokens ≈ 750 words)
            estimated_tokens = len(task_description.split()) * 2 * 1000  # Input + output
            dummy_usage = TokenUsage(input_tokens=estimated_tokens // 2, output_tokens=estimated_tokens // 2)

            recommended_cost = dummy_usage.calculate_cost_for_tool(recommended)
            if recommended_cost > budget_usd:
                # Find cheaper alternative
                cheapest, cost = dummy_usage.get_cheapest_tool(exclude_free=True)
                if cost <= budget_usd:
                    recommended = cheapest
                    reasoning = f"Budget constraint (${budget_usd:.2f}) requires cheaper model"
                    alternatives = []

        return {
            "recommended_tool": recommended,
            "reasoning": reasoning,
            "alternatives": alternatives,
            "task_complexity": "complex" if is_complex else ("simple" if is_simple else "medium"),
        }

    def show_cost_comparison(self, worktree_name: str | None = None) -> dict[str, Any]:
        """
        Show cost comparison across AI tools for a worktree's usage.

        Args:
            worktree_name: Name of worktree (uses current if None)

        Returns:
            Dictionary with current cost, alternative costs, and savings
        """
        if not worktree_name:
            worktree_name = self.get_current_worktree_name()
            if not worktree_name:
                return {"error": "Not in a tracked worktree"}

        status = self.get_status(worktree_name)
        if not status:
            return {"error": f"Worktree not tracked: {worktree_name}"}

        token_usage = status.token_usage
        current_tool = status.ai_tool
        current_cost = token_usage.calculate_cost_for_tool(current_tool)

        # Get all costs
        costs = token_usage.compare_costs()

        # Calculate savings
        cheapest_tool, cheapest_cost = token_usage.get_cheapest_tool(exclude_free=True)
        savings = current_cost - cheapest_cost

        return {
            "worktree": worktree_name,
            "current_tool": current_tool,
            "current_cost": current_cost,
            "total_tokens": token_usage.total_tokens,
            "all_costs": costs,
            "cheapest_tool": cheapest_tool,
            "cheapest_cost": cheapest_cost,
            "potential_savings": savings,
            "savings_percentage": (savings / current_cost * 100) if current_cost > 0 else 0,
        }
