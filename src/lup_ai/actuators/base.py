"""Protocols, DTOs, and autonomy tier definitions for actuators."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class AutonomyTier(StrEnum):
    """Autonomy boundary classification for safe execution."""

    TIER_1_AUTONOMOUS = "TIER_1_AUTONOMOUS"  # Low risk (e.g., reserve focus block on open slots)
    TIER_2_APPROVAL_REQUIRED = (
        "TIER_2_APPROVAL_REQUIRED"  # Disruptive (e.g., reschedule/cancel meetings)
    )


class ActionType(StrEnum):
    """Types of schedule mutations executed or proposed by the agent."""

    DEFEND_FOCUS = "DEFEND_FOCUS"
    RESCHEDULE_MEETING = "RESCHEDULE_MEETING"
    DECLINE_CONFLICT = "DECLINE_CONFLICT"
    RESTORE_EVENT = "RESTORE_EVENT"


class ApprovalStatus(StrEnum):
    """Lifecycle states of an agent action."""

    AUTO_APPROVED = "AUTO_APPROVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True)
class CalendarEvent:
    """Canonical calendar event representation."""

    event_id: str
    summary: str
    start_ts: datetime
    end_ts: datetime
    is_deep_work: bool = False
    is_flexible: bool = False
    attendees_count: int = 1
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionResult:
    """Result of an executed or queued calendar mutation."""

    action_id: str
    action_type: ActionType
    autonomy_tier: AutonomyTier
    approval_status: ApprovalStatus
    calendar_id: str
    target_event_id: str | None
    title: str
    scheduled_start: str | None
    scheduled_end: str | None
    before_state: dict[str, Any]
    after_state: dict[str, Any]
    rationale: str
    undo_token: str
    success: bool
    error_message: str | None = None


class CalendarActuatorProtocol(Protocol):
    """Interface for calendar actuators."""

    def list_events(
        self, start_dt: datetime, end_dt: datetime, calendar_id: str = "primary"
    ) -> list[CalendarEvent]: ...

    def find_free_slots(
        self,
        start_dt: datetime,
        end_dt: datetime,
        min_duration_minutes: int = 90,
        work_start_hour: int = 8,
        work_end_hour: int = 18,
        calendar_id: str = "primary",
    ) -> list[tuple[datetime, datetime]]: ...

    def defend_focus_block(
        self,
        start_dt: datetime,
        end_dt: datetime,
        title: str = "Deep Work - Focus Defense [LUP AI]",
        rationale: str = "Automatic focus protection",
        calendar_id: str = "primary",
    ) -> ActionResult: ...

    def propose_reschedule(
        self,
        event_id: str,
        new_start_dt: datetime,
        new_end_dt: datetime,
        rationale: str,
        calendar_id: str = "primary",
    ) -> ActionResult: ...

    def execute_approved_action(self, action_id: str) -> ActionResult: ...

    def undo_action(self, action_id: str) -> ActionResult: ...
