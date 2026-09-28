"""知识 revision 状态机、有效期冲突和 append-only 审核事件。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Iterable

from .ids import revision_id, stable_id
from .models import ReviewDecision, ReviewEvent, RevisionLifecycle


MUTABLE_STATES = {RevisionLifecycle.DRAFT, RevisionLifecycle.CHANGES_REQUESTED}
IMMUTABLE_STATES = {
    RevisionLifecycle.APPROVED,
    RevisionLifecycle.RELEASED,
    RevisionLifecycle.RETIRED,
}
ALLOWED_TRANSITIONS = {
    RevisionLifecycle.DRAFT: {RevisionLifecycle.IN_REVIEW, RevisionLifecycle.REJECTED},
    RevisionLifecycle.IN_REVIEW: {
        RevisionLifecycle.APPROVED,
        RevisionLifecycle.CHANGES_REQUESTED,
        RevisionLifecycle.REJECTED,
    },
    RevisionLifecycle.CHANGES_REQUESTED: {
        RevisionLifecycle.DRAFT,
        RevisionLifecycle.IN_REVIEW,
        RevisionLifecycle.REJECTED,
    },
    RevisionLifecycle.APPROVED: {RevisionLifecycle.RELEASED},
    RevisionLifecycle.RELEASED: {RevisionLifecycle.RETIRED},
    RevisionLifecycle.REJECTED: set(),
    RevisionLifecycle.RETIRED: set(),
}


@dataclass(frozen=True)
class RevisionRecord:
    logical_id: str
    revision_id: str
    lifecycle: RevisionLifecycle
    content: dict[str, Any]
    effective_from: date
    effective_to: date
    supersedes_revision_id: str | None = None


def transition_revision(record: RevisionRecord, target: RevisionLifecycle) -> RevisionRecord:
    if target not in ALLOWED_TRANSITIONS[record.lifecycle]:
        raise ValueError(f"非法 revision 状态迁移: {record.lifecycle}->{target}")
    return replace(record, lifecycle=target)


def edit_revision(record: RevisionRecord, content: dict[str, Any]) -> RevisionRecord:
    if record.lifecycle not in MUTABLE_STATES:
        raise ValueError("不可原地修改 APPROVED/RELEASED/RETIRED revision；请克隆 superseding draft")
    return replace(
        record,
        revision_id=revision_id(record.logical_id, content),
        content=content,
    )


def clone_superseding_revision(record: RevisionRecord, content: dict[str, Any]) -> RevisionRecord:
    if record.lifecycle not in IMMUTABLE_STATES:
        raise ValueError("只有不可变 revision 需要克隆 superseding draft")
    return RevisionRecord(
        logical_id=record.logical_id,
        revision_id=revision_id(record.logical_id, content),
        lifecycle=RevisionLifecycle.DRAFT,
        content=content,
        effective_from=record.effective_from,
        effective_to=record.effective_to,
        supersedes_revision_id=record.revision_id,
    )


def validate_effective_window_overlaps(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    """按 logical rule + revision 去重，正常 OR 分支共享日期不误报。"""
    revisions: dict[str, dict[str, Any]] = {}
    for row in rows:
        lifecycle = str(row.get("lifecycle") or "")
        if lifecycle not in {"APPROVED", "RELEASED"}:
            continue
        revision = str(row["revision_id"])
        revisions.setdefault(revision, row)
    by_logical: dict[str, list[dict[str, Any]]] = {}
    for row in revisions.values():
        by_logical.setdefault(str(row["logical_id"]), []).append(row)
    conflicts = []
    for items in by_logical.values():
        ordered = sorted(items, key=lambda row: (str(row["effective_from"]), str(row["revision_id"])))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                left_start = date.fromisoformat(str(left["effective_from"]))
                left_end = date.fromisoformat(str(left["effective_to"]))
                right_start = date.fromisoformat(str(right["effective_from"]))
                right_end = date.fromisoformat(str(right["effective_to"]))
                if left_start <= right_end and right_start <= left_end:
                    conflicts.append(tuple(sorted((str(left["revision_id"]), str(right["revision_id"])))))
    return sorted(set(conflicts))


class ReviewLedger:
    """只允许 append；投影不删除前序意见。"""

    def __init__(self) -> None:
        self._events: list[ReviewEvent] = []

    @property
    def events(self) -> tuple[ReviewEvent, ...]:
        return tuple(self._events)

    def append(self, event: ReviewEvent) -> None:
        if any(existing.event_id == event.event_id for existing in self._events):
            raise ValueError("review event ID 重复")
        entity_events = [existing for existing in self._events if existing.entity_id == event.entity_id]
        expected_previous = entity_events[-1].event_id if entity_events else None
        if event.previous_event_id != expected_previous:
            raise ValueError("review event previous_event_id 不是当前尾事件")
        self._events.append(event)

    def project(self, entity_id: str) -> dict[str, Any]:
        events = [event for event in self._events if event.entity_id == entity_id]
        if not events:
            return {"entity_id": entity_id, "decision": None, "event_count": 0, "history": []}
        latest = events[-1]
        return {
            "entity_id": entity_id,
            "decision": latest.decision.value,
            "event_count": len(events),
            "history": [event.model_dump(mode="json") for event in events],
        }


def review_event_id(entity_id: str, decision: ReviewDecision, reviewed_at: str, reviewer_id: str) -> str:
    return stable_id("review", entity_id, decision, reviewed_at, reviewer_id)
