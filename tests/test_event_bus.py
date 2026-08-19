"""Tests for the event bus and task manager."""

import asyncio

import pytest

from src.core.event_bus import EventBus, create_event
from src.core.task_manager import TaskManager, TaskStatus
from src.models.events import EventType


@pytest.fixture(autouse=True)
def fresh_state():
    EventBus.reset()
    TaskManager.reset()
    yield
    EventBus.reset()
    TaskManager.reset()


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_and_history(self):
        bus = EventBus.get()
        event = create_event(EventType.INCIDENT_DECLARED, "INC-001", "coordinator")
        event_id = await bus.publish(event)
        assert event_id

        history = bus.get_history(incident_id="INC-001")
        assert len(history) == 1
        assert history[0].type == EventType.INCIDENT_DECLARED

    @pytest.mark.asyncio
    async def test_subscribe(self):
        bus = EventBus.get()
        received = []

        def handler(event):
            received.append(event)

        bus.subscribe(EventType.INCIDENT_DECLARED, handler)
        event = create_event(EventType.INCIDENT_DECLARED, "INC-001")
        await bus.publish(event)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_async_subscribe(self):
        bus = EventBus.get()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe(EventType.CHECKIN_RECEIVED, handler)
        event = create_event(EventType.CHECKIN_RECEIVED, "INC-001", "accountability")
        await bus.publish(event)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_wildcard_subscriber(self):
        bus = EventBus.get()
        received = []

        bus.subscribe_all(lambda e: received.append(e))
        await bus.publish(create_event(EventType.INCIDENT_DECLARED, "INC-001"))
        await bus.publish(create_event(EventType.TASK_CREATED, "INC-001"))

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_history_filter_by_type(self):
        bus = EventBus.get()
        await bus.publish(create_event(EventType.INCIDENT_DECLARED, "INC-001"))
        await bus.publish(create_event(EventType.TASK_CREATED, "INC-001"))
        await bus.publish(create_event(EventType.TASK_COMPLETED, "INC-001"))

        tasks_only = bus.get_history(event_type="task.created")
        assert len(tasks_only) == 1


class TestTaskManager:
    @pytest.mark.asyncio
    async def test_create_task(self):
        tm = TaskManager.get()
        task = await tm.create_task(
            incident_id="INC-001",
            agent_id="accountability",
            action="send_checkins",
        )
        assert task.status == TaskStatus.PENDING
        assert task.agent_id == "accountability"

    @pytest.mark.asyncio
    async def test_execute_task_success(self):
        tm = TaskManager.get()

        async def mock_handler(task):
            return {"status": "done", "count": 34}

        tm.register_handler("accountability", mock_handler)

        task = await tm.create_task("INC-001", "accountability", "send_checkins")
        result = await tm.execute_task(task.task_id)

        assert result["status"] == "completed"
        assert result["result"]["count"] == 34
        assert task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_execute_task_failure_and_retry(self):
        tm = TaskManager.get()
        call_count = 0

        async def flaky_handler(task):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Temporary failure")
            return {"status": "done"}

        tm.register_handler("safety_intel", flaky_handler)

        task = await tm.create_task(
            "INC-001", "safety_intel", "find_routes", max_retries=2
        )
        result = await tm.execute_task(task.task_id)

        assert result["status"] == "completed"
        assert call_count == 3  # failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_execute_task_exhausted_retries_escalates(self):
        tm = TaskManager.get()

        async def always_fail(task):
            raise RuntimeError("Persistent failure")

        tm.register_handler("sitrep", always_fail)

        task = await tm.create_task(
            "INC-001", "sitrep", "generate_sitrep", max_retries=1
        )
        result = await tm.execute_task(task.task_id)

        assert result["status"] == "escalated"
        assert task.status == TaskStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_execute_task_timeout(self):
        tm = TaskManager.get()

        async def slow_handler(task):
            await asyncio.sleep(5)
            return {"status": "done"}

        tm.register_handler("intake", slow_handler)

        task = await tm.create_task(
            "INC-001", "intake", "classify",
            timeout_seconds=1, max_retries=0,
        )
        result = await tm.execute_task(task.task_id)

        assert result["status"] == "escalated"

    @pytest.mark.asyncio
    async def test_fallback_agent(self):
        tm = TaskManager.get()

        async def primary_fail(task):
            raise RuntimeError("Primary agent failed")

        async def fallback_success(task):
            return {"status": "done", "agent": "fallback"}

        tm.register_handler("safety_intel", primary_fail)
        tm.register_handler("coordinator_fallback", fallback_success)
        tm.register_fallback("safety_intel", "coordinator_fallback")

        task = await tm.create_task(
            "INC-001", "safety_intel", "find_routes", max_retries=0
        )
        result = await tm.execute_task(task.task_id)

        assert result["status"] == "completed"
        assert result["result"]["agent"] == "fallback"

    @pytest.mark.asyncio
    async def test_no_handler_registered(self):
        tm = TaskManager.get()
        task = await tm.create_task("INC-001", "unknown_agent", "some_action")
        result = await tm.execute_task(task.task_id)

        assert result["status"] == "failed"
        assert "No handler" in result["error"]

    @pytest.mark.asyncio
    async def test_get_incident_tasks(self):
        tm = TaskManager.get()

        async def noop(task):
            return {}

        tm.register_handler("a", noop)
        tm.register_handler("b", noop)

        await tm.create_task("INC-001", "a", "action1")
        await tm.create_task("INC-001", "b", "action2")
        await tm.create_task("INC-002", "a", "action3")

        inc1 = tm.get_incident_tasks("INC-001")
        assert len(inc1) == 2

    @pytest.mark.asyncio
    async def test_events_emitted(self):
        bus = EventBus.get()
        tm = TaskManager.get()
        events = []

        bus.subscribe_all(lambda e: events.append(e))

        async def handler(task):
            return {"ok": True}

        tm.register_handler("test_agent", handler)

        task = await tm.create_task("INC-001", "test_agent", "test_action")
        await tm.execute_task(task.task_id)

        event_types = [str(e.type) for e in events]
        assert "task.created" in event_types
        assert "task.completed" in event_types
