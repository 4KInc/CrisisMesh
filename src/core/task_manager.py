"""Task Manager — tracks agent tasks with timeout, retry, and failure recovery.

Each task represents a unit of work delegated by the Coordinator to a specialist
agent. The task manager enforces deadlines, retries on failure, and escalates
to the Coordinator when retries are exhausted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from enum import StrEnum
from typing import Any, Callable, Coroutine

from src.core.event_bus import EventBus, create_event
from src.models.events import EventType

logger = logging.getLogger(__name__)


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RETRYING = "retrying"
    ESCALATED = "escalated"


class Task:
    def __init__(
        self,
        task_id: str,
        incident_id: str,
        agent_id: str,
        action: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        priority: int = 0,
    ) -> None:
        self.task_id = task_id
        self.incident_id = incident_id
        self.agent_id = agent_id
        self.action = action
        self.params = params or {}
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.priority = priority
        self.status = TaskStatus.PENDING
        self.retries = 0
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.created_at = datetime.now(timezone.utc)
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.deadline: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "incident_id": self.incident_id,
            "agent_id": self.agent_id,
            "action": self.action,
            "status": self.status,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


TaskHandler = Callable[[Task], Coroutine[Any, Any, dict[str, Any]]]


class TaskManager:
    """Manages task lifecycle with timeout, retry, and failure recovery."""

    _instance: TaskManager | None = None

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._handlers: dict[str, TaskHandler] = {}
        self._fallback_handlers: dict[str, str] = {}  # agent_id -> fallback_agent_id

    @classmethod
    def get(cls) -> TaskManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def register_handler(self, agent_id: str, handler: TaskHandler) -> None:
        self._handlers[agent_id] = handler

    def register_fallback(self, agent_id: str, fallback_agent_id: str) -> None:
        self._fallback_handlers[agent_id] = fallback_agent_id

    async def create_task(
        self,
        incident_id: str,
        agent_id: str,
        action: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        priority: int = 0,
    ) -> Task:
        task_id = str(uuid.uuid4())[:8]
        task = Task(
            task_id=task_id,
            incident_id=incident_id,
            agent_id=agent_id,
            action=action,
            params=params,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            priority=priority,
        )
        self._tasks[task_id] = task

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.TASK_CREATED,
            incident_id=incident_id,
            agent_id=agent_id,
            data={"task_id": task_id, "action": action},
        ))

        logger.info(f"Task created: {task_id} -> {agent_id}.{action}")
        return task

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        task.status = TaskStatus.IN_PROGRESS
        task.started_at = datetime.now(timezone.utc)
        task.deadline = task.started_at + timedelta(seconds=task.timeout_seconds)

        handler = self._handlers.get(task.agent_id)
        if not handler:
            return await self._fail_task(task, f"No handler registered for agent: {task.agent_id}")

        try:
            result = await asyncio.wait_for(
                handler(task),
                timeout=task.timeout_seconds,
            )
            return await self._complete_task(task, result)

        except asyncio.TimeoutError:
            return await self._timeout_task(task)

        except Exception as e:
            return await self._handle_failure(task, str(e))

    async def _complete_task(self, task: Task, result: dict[str, Any]) -> dict[str, Any]:
        task.status = TaskStatus.COMPLETED
        task.result = result
        task.completed_at = datetime.now(timezone.utc)

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.TASK_COMPLETED,
            incident_id=task.incident_id,
            agent_id=task.agent_id,
            data={"task_id": task.task_id, "action": task.action},
        ))

        logger.info(f"Task completed: {task.task_id}")
        return {"status": "completed", "task_id": task.task_id, "result": result}

    async def _timeout_task(self, task: Task) -> dict[str, Any]:
        task.status = TaskStatus.TIMED_OUT
        task.error = f"Task timed out after {task.timeout_seconds}s"

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.TASK_TIMEOUT,
            incident_id=task.incident_id,
            agent_id=task.agent_id,
            data={"task_id": task.task_id, "action": task.action, "timeout": task.timeout_seconds},
        ))

        logger.warning(f"Task timed out: {task.task_id}")
        return await self._handle_failure(task, task.error)

    async def _handle_failure(self, task: Task, error: str) -> dict[str, Any]:
        task.error = error
        task.retries += 1

        bus = EventBus.get()

        # Retry if under limit
        if task.retries <= task.max_retries:
            task.status = TaskStatus.RETRYING
            logger.info(f"Retrying task {task.task_id} (attempt {task.retries}/{task.max_retries})")

            await bus.publish(create_event(
                EventType.TASK_FAILED,
                incident_id=task.incident_id,
                agent_id=task.agent_id,
                data={"task_id": task.task_id, "error": error, "retry": task.retries},
            ))

            return await self.execute_task(task.task_id)

        # Try fallback agent
        fallback = self._fallback_handlers.get(task.agent_id)
        if fallback and fallback in self._handlers:
            logger.info(f"Falling back to {fallback} for task {task.task_id}")
            task.agent_id = fallback
            task.retries = 0
            return await self.execute_task(task.task_id)

        # Escalate to coordinator
        return await self._escalate_task(task, error)

    async def _escalate_task(self, task: Task, error: str) -> dict[str, Any]:
        task.status = TaskStatus.ESCALATED
        task.completed_at = datetime.now(timezone.utc)

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.AGENT_ERROR,
            incident_id=task.incident_id,
            agent_id=task.agent_id,
            data={
                "task_id": task.task_id,
                "action": task.action,
                "error": error,
                "retries_exhausted": True,
                "escalated_to": "coordinator",
            },
        ))

        logger.error(f"Task escalated: {task.task_id} — {error}")
        return {
            "status": "escalated",
            "task_id": task.task_id,
            "error": error,
            "message": "Task failed after retries. Escalated to Coordinator for human review.",
        }

    async def _fail_task(self, task: Task, error: str) -> dict[str, Any]:
        task.status = TaskStatus.FAILED
        task.error = error
        task.completed_at = datetime.now(timezone.utc)

        bus = EventBus.get()
        await bus.publish(create_event(
            EventType.TASK_FAILED,
            incident_id=task.incident_id,
            agent_id=task.agent_id,
            data={"task_id": task.task_id, "error": error},
        ))

        return {"status": "failed", "task_id": task.task_id, "error": error}

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_incident_tasks(self, incident_id: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.incident_id == incident_id]

    def get_pending_tasks(self, incident_id: str = "") -> list[Task]:
        tasks = self._tasks.values()
        if incident_id:
            tasks = [t for t in tasks if t.incident_id == incident_id]
        return [t for t in tasks if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.RETRYING)]

    def get_failed_tasks(self, incident_id: str = "") -> list[Task]:
        tasks = self._tasks.values()
        if incident_id:
            tasks = [t for t in tasks if t.incident_id == incident_id]
        return [t for t in tasks if t.status in (TaskStatus.FAILED, TaskStatus.TIMED_OUT, TaskStatus.ESCALATED)]
