"""Seeding a durable store on every boot fills it with copies.

init_memory_bank() guarded on `mb.lessons`, which reads the local store's list.
The managed backend has no such list, so the guard read empty every time and
seeded again on every cold start. Eleven restarts later the Agent Engine held
fifty-five memories: eleven copies of each of the five seeds. Similarity search
then returned five hits that were all the same lesson, and the console showed
the same sentence five times.

The guard has to ask the store that is actually in use whether it already holds
anything.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.memory_bank import MemoryBank, init_memory_bank


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MEMORY_BACKEND", raising=False)
    monkeypatch.delenv("VERTEX_MEMORY_ENGINE", raising=False)
    MemoryBank.reset()
    yield
    MemoryBank.reset()


class TestTheLocalStoreSeedsOnce:
    def test_seeding_twice_does_not_double(self):
        first = len(init_memory_bank().lessons)
        second = len(init_memory_bank().lessons)
        assert first == second and first > 0


class TestTheManagedStoreIsNotReseeded:
    def _vertex(self, monkeypatch, existing):
        """A managed backend that already holds `existing` lessons."""
        monkeypatch.setenv("MEMORY_BACKEND", "vertex")
        monkeypatch.setenv("VERTEX_MEMORY_ENGINE", "projects/p/locations/l/reasoningEngines/1")
        MemoryBank.reset()
        store = MagicMock()
        store.find_lessons.return_value = existing
        store.lessons = []          # the managed adapter has no such list
        bank = MemoryBank.get()
        bank._store = store
        bank._backend = "vertex"
        return bank, store

    def test_a_store_that_already_has_lessons_is_left_alone(self, monkeypatch):
        bank, store = self._vertex(monkeypatch, [{"title": "already here"}])
        with patch.object(MemoryBank, "get", return_value=bank):
            init_memory_bank()
        assert store.store_lesson.call_count == 0, (
            "re-seeded a store that already held lessons; this is how eleven "
            "restarts became fifty-five memories"
        )

    def test_an_empty_store_is_seeded_once(self, monkeypatch):
        bank, store = self._vertex(monkeypatch, [])
        with patch.object(MemoryBank, "get", return_value=bank):
            init_memory_bank()
        assert store.store_lesson.call_count > 0

    def test_it_asks_the_store_rather_than_the_local_list(self, monkeypatch):
        """The old guard read a list the managed adapter does not have."""
        bank, store = self._vertex(monkeypatch, [{"title": "already here"}])
        with patch.object(MemoryBank, "get", return_value=bank):
            init_memory_bank()
        assert store.find_lessons.called, "never asked the store what it holds"

    def test_a_store_that_cannot_answer_is_not_seeded(self, monkeypatch):
        """An unreadable store is not an empty one. Seeding on a failed read is
        how duplicates get written during an outage."""
        bank, store = self._vertex(monkeypatch, [])
        store.find_lessons.side_effect = RuntimeError("backend down")
        with patch.object(MemoryBank, "get", return_value=bank):
            init_memory_bank()
        assert store.store_lesson.call_count == 0
