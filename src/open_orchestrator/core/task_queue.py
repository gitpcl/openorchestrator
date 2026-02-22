"""
Task queue system for autonomous agent orchestration.

This module manages task queues for worktrees, allowing users to enqueue
tasks that will be processed autonomously by AI agents without user interaction.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from open_orchestrator.config import AITool
from open_orchestrator.core.auto_agent import AutoAgent, AutoAgentMonitor
from open_orchestrator.utils.io import atomic_write_text, shared_file_lock

logger = logging.getLogger(__name__)


class TaskPriority(Enum):
    """Task priority levels."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class TaskStatus(Enum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    """A task to be executed by an autonomous agent."""

    id: str = Field(description="Unique task identifier")
    description: str = Field(description="Task description/prompt for the AI")
    worktree_name: str = Field(description="Target worktree name")
    priority: int = Field(default=TaskPriority.NORMAL.value, description="Task priority")
    status: str = Field(default=TaskStatus.PENDING.value, description="Current status")
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = Field(default=None, description="When task started execution")
    completed_at: datetime | None = Field(default=None, description="When task completed")
    error_message: str | None = Field(default=None, description="Error message if failed")
    result: str | None = Field(default=None, description="Task result/output")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    def mark_running(self) -> None:
        """Mark task as running."""
        self.status = TaskStatus.RUNNING.value
        self.started_at = datetime.now()

    def mark_completed(self, result: str | None = None) -> None:
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED.value
        self.completed_at = datetime.now()
        self.result = result

    def mark_failed(self, error: str) -> None:
        """Mark task as failed."""
        self.status = TaskStatus.FAILED.value
        self.completed_at = datetime.now()
        self.error_message = error

    def mark_cancelled(self) -> None:
        """Mark task as cancelled."""
        self.status = TaskStatus.CANCELLED.value
        self.completed_at = datetime.now()


class QueueStore(BaseModel):
    """Persistent storage for task queues."""

    queues: dict[str, list[Task]] = Field(default_factory=dict, description="Map of worktree name to task list")
    version: int = Field(default=1, description="Store format version")


class TaskQueueError(Exception):
    """Base exception for task queue errors."""

    pass


class WorktreeNotFoundError(TaskQueueError):
    """Raised when a worktree is not found."""

    pass


class TaskQueue:
    """
    Manages task queues for worktrees with autonomous execution.

    Each worktree can have its own queue of tasks that will be executed
    sequentially by an autonomous agent.
    """

    DEFAULT_STORE_FILENAME = "task_queues.json"

    def __init__(self, storage_path: Path | None = None):
        """
        Initialize task queue manager.

        Args:
            storage_path: Path to store queue data (default: ~/.open-orchestrator/task_queues.json)
        """
        self._storage_path = storage_path or self._get_default_path()
        self._store: QueueStore = QueueStore()
        self._load_store()
        self._next_task_id = 0

    def _get_default_path(self) -> Path:
        """Get default storage path."""
        cache_dir = Path.home() / ".open-orchestrator"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / self.DEFAULT_STORE_FILENAME

    def _load_store(self) -> None:
        """Load queue store from disk."""
        if not self._storage_path.exists():
            self._store = QueueStore()
            return

        try:
            with open(self._storage_path) as f:
                with shared_file_lock(f):
                    data = json.load(f)
                    self._store = QueueStore.model_validate(data)

            # Clean up completed/failed tasks older than 7 days
            self._cleanup_old_tasks()

        except Exception as e:
            logger.warning(f"Failed to load queue store: {e}")
            self._store = QueueStore()

    def _save_store(self) -> None:
        """Persist queue store to disk."""
        try:
            atomic_write_text(
                self._storage_path,
                self._store.model_dump_json(indent=2),
            )
        except Exception as e:
            logger.error(f"Failed to save queue store: {e}")
            raise TaskQueueError(f"Failed to save queue store: {e}") from e

    def _cleanup_old_tasks(self, max_age_days: int = 7) -> None:
        """Remove completed/failed tasks older than max_age_days."""
        cutoff = datetime.now().timestamp() - (max_age_days * 24 * 60 * 60)

        for worktree_name, tasks in self._store.queues.items():
            self._store.queues[worktree_name] = [
                task
                for task in tasks
                if task.status in [TaskStatus.PENDING.value, TaskStatus.RUNNING.value]
                or (task.completed_at and task.completed_at.timestamp() > cutoff)
            ]

    def _generate_task_id(self, worktree_name: str) -> str:
        """Generate unique task ID."""
        self._next_task_id += 1
        timestamp = int(datetime.now().timestamp())
        return f"{worktree_name}-{timestamp}-{self._next_task_id}"

    def enqueue(
        self,
        worktree_name: str,
        description: str,
        priority: TaskPriority = TaskPriority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """
        Add a task to a worktree's queue.

        Args:
            worktree_name: Name of the target worktree
            description: Task description/prompt
            priority: Task priority level
            metadata: Optional metadata

        Returns:
            The created Task
        """
        task_id = self._generate_task_id(worktree_name)

        task = Task(
            id=task_id,
            description=description,
            worktree_name=worktree_name,
            priority=priority.value,
            metadata=metadata or {},
        )

        # Initialize queue if it doesn't exist
        if worktree_name not in self._store.queues:
            self._store.queues[worktree_name] = []

        # Insert task based on priority (higher priority first)
        queue = self._store.queues[worktree_name]
        inserted = False

        for i, existing_task in enumerate(queue):
            if existing_task.status != TaskStatus.PENDING.value:
                continue

            if task.priority > existing_task.priority:
                queue.insert(i, task)
                inserted = True
                break

        if not inserted:
            queue.append(task)

        self._save_store()
        logger.info(f"Enqueued task {task_id} for {worktree_name} (priority: {priority.name})")

        return task

    def dequeue(self, worktree_name: str) -> Task | None:
        """
        Get next pending task from a worktree's queue.

        Args:
            worktree_name: Name of the worktree

        Returns:
            Next Task or None if queue is empty
        """
        if worktree_name not in self._store.queues:
            return None

        queue = self._store.queues[worktree_name]

        # Find first pending task
        for task in queue:
            if task.status == TaskStatus.PENDING.value:
                return task

        return None

    def get_queue(self, worktree_name: str) -> list[Task]:
        """Get all tasks for a worktree."""
        return self._store.queues.get(worktree_name, [])

    def get_task(self, task_id: str) -> Task | None:
        """Get a specific task by ID."""
        for queue in self._store.queues.values():
            for task in queue:
                if task.id == task_id:
                    return task
        return None

    def update_task(self, task: Task) -> None:
        """Update a task in the store."""
        for worktree_name, queue in self._store.queues.items():
            for i, existing_task in enumerate(queue):
                if existing_task.id == task.id:
                    queue[i] = task
                    self._save_store()
                    return

        raise TaskQueueError(f"Task not found: {task.id}")

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a pending or running task.

        Args:
            task_id: ID of the task to cancel

        Returns:
            True if cancelled, False if not found or already completed
        """
        task = self.get_task(task_id)

        if not task:
            return False

        if task.status in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value]:
            return False

        task.mark_cancelled()
        self.update_task(task)
        logger.info(f"Cancelled task {task_id}")

        return True

    def clear_queue(self, worktree_name: str, include_running: bool = False) -> int:
        """
        Clear all pending tasks from a worktree's queue.

        Args:
            worktree_name: Name of the worktree
            include_running: If True, also cancel running tasks

        Returns:
            Number of tasks cancelled
        """
        if worktree_name not in self._store.queues:
            return 0

        queue = self._store.queues[worktree_name]
        cancelled = 0

        for task in queue:
            if task.status == TaskStatus.PENDING.value or (include_running and task.status == TaskStatus.RUNNING.value):
                task.mark_cancelled()
                cancelled += 1

        if cancelled > 0:
            self._save_store()
            logger.info(f"Cleared {cancelled} tasks from {worktree_name}")

        return cancelled

    def get_queue_stats(self, worktree_name: str) -> dict[str, int]:
        """
        Get statistics for a worktree's queue.

        Args:
            worktree_name: Name of the worktree

        Returns:
            Dictionary with counts by status
        """
        queue = self.get_queue(worktree_name)

        stats = {
            "total": len(queue),
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }

        for task in queue:
            if task.status == TaskStatus.PENDING.value:
                stats["pending"] += 1
            elif task.status == TaskStatus.RUNNING.value:
                stats["running"] += 1
            elif task.status == TaskStatus.COMPLETED.value:
                stats["completed"] += 1
            elif task.status == TaskStatus.FAILED.value:
                stats["failed"] += 1
            elif task.status == TaskStatus.CANCELLED.value:
                stats["cancelled"] += 1

        return stats

    def start_worker(
        self,
        worktree_name: str,
        worktree_path: Path,
        ai_tool: AITool = AITool.CLAUDE,
        log_dir: Path | None = None,
    ) -> "AutoAgentWorker":
        """
        Start an autonomous worker for a worktree's queue.

        Args:
            worktree_name: Name of the worktree
            worktree_path: Path to the worktree directory
            ai_tool: AI tool to use
            log_dir: Directory for agent logs

        Returns:
            Started AutoAgentWorker
        """
        worker = AutoAgentWorker(
            worktree_name=worktree_name,
            worktree_path=worktree_path,
            queue=self,
            ai_tool=ai_tool,
            log_dir=log_dir,
        )

        worker.start()
        return worker


@dataclass
class AutoAgentWorker:
    """
    Worker that processes tasks from a queue autonomously.

    Runs in a background thread, processing tasks sequentially
    until the queue is empty.
    """

    worktree_name: str
    worktree_path: Path
    queue: TaskQueue
    ai_tool: AITool = AITool.CLAUDE
    log_dir: Path | None = None
    stop_on_error: bool = False
    _thread: threading.Thread | None = field(default=None, init=False)
    _running: bool = field(default=False, init=False)
    _current_agent: AutoAgent | None = field(default=None, init=False)

    def start(self) -> None:
        """Start the worker thread."""
        if self._running:
            logger.warning(f"Worker for {self.worktree_name} is already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"Started worker for {self.worktree_name}")

    def stop(self) -> None:
        """Stop the worker thread."""
        self._running = False

        if self._current_agent:
            self._current_agent.stop()
            self._current_agent = None

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        logger.info(f"Stopped worker for {self.worktree_name}")

    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running

    def _run(self) -> None:
        """Main worker loop - processes tasks from queue."""
        logger.info(f"Worker loop started for {self.worktree_name}")

        while self._running:
            # Get next task
            task = self.queue.dequeue(self.worktree_name)

            if not task:
                # No more tasks, sleep and check again
                time.sleep(5)
                continue

            logger.info(f"Processing task {task.id}: {task.description}")

            try:
                # Mark task as running
                task.mark_running()
                self.queue.update_task(task)

                # Create log file for this task
                log_file = None
                if self.log_dir:
                    self.log_dir.mkdir(parents=True, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    log_file = self.log_dir / f"{self.worktree_name}_{task.id}_{timestamp}.log"

                # Start autonomous agent
                self._current_agent = AutoAgent(
                    worktree_path=self.worktree_path,
                    task=task.description,
                    ai_tool=self.ai_tool,
                    log_file=log_file,
                )

                self._current_agent.start()

                # Create monitor
                monitor = AutoAgentMonitor(self._current_agent)

                # Monitor agent until completion
                while self._running and not self._current_agent.is_complete():
                    time.sleep(10)  # Check every 10 seconds

                    # Check health
                    healthy, issue = monitor.check_health()

                    if not healthy:
                        logger.warning(f"Agent health issue: {issue}")

                        # Try auto-recovery
                        if monitor.auto_recover():
                            logger.info("Auto-recovery attempted")
                            continue

                        # Recovery failed, mark task as failed
                        task.mark_failed(f"Agent health issue: {issue}")
                        self.queue.update_task(task)

                        if self.stop_on_error:
                            self._running = False

                        break

                # Task completed successfully
                if self._current_agent.is_complete() and not task.status == TaskStatus.FAILED.value:
                    output = self._current_agent.get_output(lines=100)
                    task.mark_completed(result=output)
                    self.queue.update_task(task)
                    logger.info(f"Task {task.id} completed successfully")

                # Clean up agent
                self._current_agent.stop()
                self._current_agent = None

            except Exception as e:
                logger.error(f"Error processing task {task.id}: {e}")
                task.mark_failed(str(e))
                self.queue.update_task(task)

                if self._current_agent:
                    self._current_agent.stop()
                    self._current_agent = None

                if self.stop_on_error:
                    self._running = False

        logger.info(f"Worker loop ended for {self.worktree_name}")


__all__ = [
    "Task",
    "TaskPriority",
    "TaskStatus",
    "TaskQueue",
    "TaskQueueError",
    "AutoAgentWorker",
]
