"""Tests for transcription queue state machine.

The actual queue is in Swift, so we model the state machine in Python
and test all transitions: enqueue, process, complete, fail, pause, resume,
cancel, remove, reorder.
"""
from __future__ import annotations

from enum import Enum



# ---------------------------------------------------------------------------
# Queue state machine model
# ---------------------------------------------------------------------------
class ItemState(Enum):
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TranscriptionQueue:
    """Python model of the Swift transcription queue state machine."""

    def __init__(self):
        self._items: list[dict] = []
        self._paused: bool = False

    @property
    def items(self) -> list[dict]:
        return list(self._items)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def enqueue(self, path: str) -> None:
        self._items.append({"path": path, "state": ItemState.QUEUED})

    def process_next(self) -> str | None:
        """Pick the next queued item and move it to transcribing.

        Returns the path of the item being processed, or None if nothing
        to process (empty, paused, or nothing queued).
        """
        if self._paused:
            return None
        for item in self._items:
            if item["state"] == ItemState.QUEUED:
                item["state"] = ItemState.TRANSCRIBING
                return item["path"]
        return None

    def _find(self, path: str) -> dict | None:
        for item in self._items:
            if item["path"] == path:
                return item
        return None

    def mark_complete(self, path: str) -> None:
        item = self._find(path)
        if item and item["state"] == ItemState.TRANSCRIBING:
            item["state"] = ItemState.COMPLETED

    def mark_failed(self, path: str) -> None:
        item = self._find(path)
        if item and item["state"] == ItemState.TRANSCRIBING:
            item["state"] = ItemState.FAILED

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def cancel_all(self) -> None:
        for item in self._items:
            if item["state"] in (ItemState.QUEUED, ItemState.TRANSCRIBING):
                item["state"] = ItemState.CANCELLED

    def remove(self, path: str) -> None:
        self._items = [i for i in self._items if i["path"] != path]

    def move(self, from_idx: int, to_idx: int) -> None:
        if 0 <= from_idx < len(self._items) and 0 <= to_idx < len(self._items):
            item = self._items.pop(from_idx)
            self._items.insert(to_idx, item)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestEnqueue:
    def test_enqueue_single(self):
        q = TranscriptionQueue()
        q.enqueue("/recordings/a.mp3")
        assert len(q.items) == 1
        assert q.items[0]["state"] == ItemState.QUEUED

    def test_enqueue_multiple(self):
        q = TranscriptionQueue()
        q.enqueue("/recordings/a.mp3")
        q.enqueue("/recordings/b.mp3")
        q.enqueue("/recordings/c.mp3")
        assert len(q.items) == 3
        assert all(i["state"] == ItemState.QUEUED for i in q.items)

    def test_enqueue_preserves_order(self):
        q = TranscriptionQueue()
        q.enqueue("first")
        q.enqueue("second")
        q.enqueue("third")
        assert [i["path"] for i in q.items] == ["first", "second", "third"]


class TestProcessNext:
    def test_process_first_queued(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        result = q.process_next()
        assert result == "a.mp3"
        assert q.items[0]["state"] == ItemState.TRANSCRIBING
        assert q.items[1]["state"] == ItemState.QUEUED

    def test_process_empty_queue(self):
        q = TranscriptionQueue()
        assert q.process_next() is None

    def test_process_skips_non_queued(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.process_next()  # a -> transcribing
        result = q.process_next()
        assert result == "b.mp3"

    def test_process_returns_none_when_all_done(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.process_next()
        q.mark_complete("a.mp3")
        assert q.process_next() is None


class TestStateTransitions:
    def test_queued_to_completed(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.process_next()
        q.mark_complete("a.mp3")
        assert q.items[0]["state"] == ItemState.COMPLETED

    def test_queued_to_failed(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.process_next()
        q.mark_failed("a.mp3")
        assert q.items[0]["state"] == ItemState.FAILED

    def test_complete_only_when_transcribing(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        # Try to complete without processing first — should be a no-op
        q.mark_complete("a.mp3")
        assert q.items[0]["state"] == ItemState.QUEUED

    def test_fail_only_when_transcribing(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.mark_failed("a.mp3")
        assert q.items[0]["state"] == ItemState.QUEUED

    def test_full_lifecycle(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        # Process and complete a
        q.process_next()
        q.mark_complete("a.mp3")
        # Process and fail b
        q.process_next()
        q.mark_failed("b.mp3")
        assert q.items[0]["state"] == ItemState.COMPLETED
        assert q.items[1]["state"] == ItemState.FAILED


class TestPauseResume:
    def test_paused_queue_does_not_process(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.pause()
        assert q.process_next() is None
        assert q.items[0]["state"] == ItemState.QUEUED

    def test_resume_allows_processing(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.pause()
        q.resume()
        result = q.process_next()
        assert result == "a.mp3"

    def test_pause_does_not_affect_in_progress(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.process_next()  # a -> transcribing
        q.pause()
        # Item already transcribing stays transcribing
        assert q.items[0]["state"] == ItemState.TRANSCRIBING
        # Can still complete it
        q.mark_complete("a.mp3")
        assert q.items[0]["state"] == ItemState.COMPLETED

    def test_is_paused_flag(self):
        q = TranscriptionQueue()
        assert not q.is_paused
        q.pause()
        assert q.is_paused
        q.resume()
        assert not q.is_paused


class TestCancelAll:
    def test_cancels_queued_items(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.enqueue("c.mp3")
        q.cancel_all()
        assert all(i["state"] == ItemState.CANCELLED for i in q.items)

    def test_cancels_transcribing_items(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.process_next()  # a -> transcribing
        q.cancel_all()
        assert q.items[0]["state"] == ItemState.CANCELLED
        assert q.items[1]["state"] == ItemState.CANCELLED

    def test_does_not_cancel_completed(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.process_next()
        q.mark_complete("a.mp3")
        q.cancel_all()
        assert q.items[0]["state"] == ItemState.COMPLETED
        assert q.items[1]["state"] == ItemState.CANCELLED

    def test_does_not_cancel_failed(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.process_next()
        q.mark_failed("a.mp3")
        q.cancel_all()
        assert q.items[0]["state"] == ItemState.FAILED
        assert q.items[1]["state"] == ItemState.CANCELLED


class TestRemove:
    def test_remove_item(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.remove("a.mp3")
        assert len(q.items) == 1
        assert q.items[0]["path"] == "b.mp3"

    def test_remove_nonexistent_is_noop(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.remove("nonexistent.mp3")
        assert len(q.items) == 1

    def test_remove_from_empty_queue(self):
        q = TranscriptionQueue()
        q.remove("a.mp3")
        assert len(q.items) == 0


class TestReorder:
    def test_move_forward(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.enqueue("c.mp3")
        q.move(2, 0)
        assert [i["path"] for i in q.items] == ["c.mp3", "a.mp3", "b.mp3"]

    def test_move_backward(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.enqueue("c.mp3")
        q.move(0, 2)
        assert [i["path"] for i in q.items] == ["b.mp3", "c.mp3", "a.mp3"]

    def test_move_same_position(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.move(1, 1)
        assert [i["path"] for i in q.items] == ["a.mp3", "b.mp3"]

    def test_move_out_of_bounds_is_noop(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.move(5, 0)
        assert [i["path"] for i in q.items] == ["a.mp3"]

    def test_reorder_affects_process_order(self):
        q = TranscriptionQueue()
        q.enqueue("a.mp3")
        q.enqueue("b.mp3")
        q.enqueue("c.mp3")
        q.move(2, 0)  # c moves to front
        result = q.process_next()
        assert result == "c.mp3"
