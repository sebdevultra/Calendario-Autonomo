"""Autonomous Calendar Agent driving focus defense, collision management, and bounded mutations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from lup_ai.actuators.base import (
    ActionResult,
    ActionType,
    ApprovalStatus,
    AutonomyTier,
    CalendarActuatorProtocol,
)
from lup_ai.config.settings import Settings, get_settings
from lup_ai.data.local_store import LocalStore

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduleHealthReport:
    """Quantitative diagnostics of schedule balance, fragmentation, and focus availability."""

    date_evaluated: str
    total_scheduled_hours: float
    meeting_hours: float
    deep_work_hours: float
    free_hours: float
    fragmentation_index: float  # 0.0 (unfragmented) to 1.0 (severely fragmented)
    deep_work_deficit: bool
    meeting_overload: bool
    events_count: int
    open_focus_slots_count: int


@dataclass(frozen=True)
class CalendarAgentCycleResult:
    """Summary of health diagnostics, autonomous executions, and staged approval actions."""

    cycle_timestamp: str
    lookahead_days: int
    health_reports: list[ScheduleHealthReport]
    actions_executed: list[ActionResult] = field(default_factory=list)
    actions_proposed: list[ActionResult] = field(default_factory=list)


class CalendarAgent:
    """Autonomous agent governing personal time, focus preservation, and schedule optimization."""

    def __init__(
        self,
        store: LocalStore,
        actuator: CalendarActuatorProtocol,
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._actuator = actuator
        self._settings = settings or get_settings()

    def evaluate_day_health(
        self,
        target_date: datetime,
        calendar_id: str = "primary",
    ) -> ScheduleHealthReport:
        """Compute metrics on schedule fragmentation, meeting load, and focus deficit."""
        day = target_date.date()
        day_start = datetime.combine(
            day,
            time(self._settings.calendar_agent_work_start_hour, 0),
            tzinfo=target_date.tzinfo or UTC,
        )
        day_end = datetime.combine(
            day,
            time(self._settings.calendar_agent_work_end_hour, 0),
            tzinfo=target_date.tzinfo or UTC,
        )

        events = self._actuator.list_events(day_start, day_end, calendar_id=calendar_id)
        work_hours_total = (
            self._settings.calendar_agent_work_end_hour
            - self._settings.calendar_agent_work_start_hour
        )

        meeting_mins = 0.0
        deep_work_mins = 0.0

        for ev in events:
            dur = max(
                0.0, (min(ev.end_ts, day_end) - max(ev.start_ts, day_start)).total_seconds() / 60
            )
            if ev.is_deep_work:
                deep_work_mins += dur
            else:
                meeting_mins += dur

        free_slots = self._actuator.find_free_slots(
            day_start,
            day_end,
            min_duration_minutes=30,
            work_start_hour=self._settings.calendar_agent_work_start_hour,
            work_end_hour=self._settings.calendar_agent_work_end_hour,
            calendar_id=calendar_id,
        )

        free_mins = sum((s[1] - s[0]).total_seconds() / 60 for s in free_slots)
        usable_large_slots = [s for s in free_slots if (s[1] - s[0]).total_seconds() / 60 >= 90]

        # Fragmentation calculation:
        # Measures context-switching load across meetings and fragmented slots.
        # 0.0 for clean/unfragmented schedule, scaling up as multiple meetings fragment the day.
        if len(events) <= 1:
            frag_index = 0.0
        else:
            small_gaps = sum(1 for s in free_slots if (s[1] - s[0]).total_seconds() / 60 < 180)
            frag_index = min(1.0, round(((len(events) - 1) * 0.2) + (small_gaps * 0.1), 2))

        deep_work_hours = round(deep_work_mins / 60, 2)
        meeting_hours = round(meeting_mins / 60, 2)
        free_hours = round(free_mins / 60, 2)

        dw_deficit = deep_work_hours < self._settings.calendar_agent_min_deep_work_hours
        meeting_overload = meeting_hours >= 4.0

        return ScheduleHealthReport(
            date_evaluated=day.isoformat(),
            total_scheduled_hours=round(work_hours_total - free_hours, 2),
            meeting_hours=meeting_hours,
            deep_work_hours=deep_work_hours,
            free_hours=free_hours,
            fragmentation_index=frag_index,
            deep_work_deficit=dw_deficit,
            meeting_overload=meeting_overload,
            events_count=len(events),
            open_focus_slots_count=len(usable_large_slots),
        )

    def evaluate_schedule_health(
        self,
        target_date: datetime | None = None,
        lookahead_days: int = 1,
        anchor_date: datetime | None = None,
        calendar_id: str = "primary",
    ) -> list[ScheduleHealthReport]:
        """Compute metrics across one or more days."""
        start = target_date or anchor_date or datetime.now(UTC)
        reports: list[ScheduleHealthReport] = []
        for offset in range(lookahead_days):
            day_dt = start + timedelta(days=offset)
            reports.append(self.evaluate_day_health(day_dt, calendar_id=calendar_id))
        return reports

    def run_cycle(
        self,
        lookahead_days: int = 3,
        anchor_date: datetime | None = None,
        calendar_id: str = "primary",
        auto_execute_tier1: bool = True,
    ) -> CalendarAgentCycleResult:
        """Run autonomous agent cycle: diagnose health, book focus, and stage reschedules."""
        now = anchor_date or datetime.now(UTC)
        reports: list[ScheduleHealthReport] = []
        executed: list[ActionResult] = []
        proposed: list[ActionResult] = []

        for offset in range(lookahead_days):
            target_dt = now + timedelta(days=offset)
            health = self.evaluate_day_health(target_dt, calendar_id=calendar_id)
            reports.append(health)

            day = target_dt.date()
            day_start = datetime.combine(
                day,
                time(self._settings.calendar_agent_work_start_hour, 0),
                tzinfo=target_dt.tzinfo or UTC,
            )
            day_end = datetime.combine(
                day,
                time(self._settings.calendar_agent_work_end_hour, 0),
                tzinfo=target_dt.tzinfo or UTC,
            )

            # 1. Defend Deep Work if in deficit and suitable free slots exist (Tier 1: Autonomous)
            if health.deep_work_deficit and auto_execute_tier1:
                free_large_slots = self._actuator.find_free_slots(
                    day_start,
                    day_end,
                    min_duration_minutes=90,
                    work_start_hour=self._settings.calendar_agent_work_start_hour,
                    work_end_hour=self._settings.calendar_agent_work_end_hour,
                    calendar_id=calendar_id,
                )
                if free_large_slots:
                    best_slot = free_large_slots[0]
                    # Cap slot duration at 2 hours (120 mins)
                    slot_end = min(best_slot[0] + timedelta(minutes=120), best_slot[1])
                    res = self._actuator.defend_focus_block(
                        start_dt=best_slot[0],
                        end_dt=slot_end,
                        title="Deep Work - Focus Defense [LUP AI]",
                        rationale=(
                            f"Deficit de foco detectado ({health.deep_work_hours}h < "
                            f"{self._settings.calendar_agent_min_deep_work_hours}h target). "
                            f"Protegiendo bloque continuo en {day.isoformat()}."
                        ),
                        calendar_id=calendar_id,
                    )
                    executed.append(res)

            # 2. Identify flexible meetings causing fragmentation or blocking focus (Tier 2)
            if (
                health.fragmentation_index > 0.3
                or health.meeting_overload
                or (health.deep_work_deficit and health.meeting_hours > 0)
            ):
                events = self._actuator.list_events(day_start, day_end, calendar_id=calendar_id)
                flexible_meetings = [e for e in events if e.is_flexible and e.attendees_count <= 2]
                if flexible_meetings:
                    target_meeting = flexible_meetings[0]
                    # Propose moving to afternoon boundary if possible
                    proposed_start = day_start + timedelta(hours=6)
                    proposed_end = proposed_start + (
                        target_meeting.end_ts - target_meeting.start_ts
                    )
                    res = self._actuator.propose_reschedule(
                        event_id=target_meeting.event_id,
                        new_start_dt=proposed_start,
                        new_end_dt=proposed_end,
                        rationale=(
                            f"Reunion flexible '{target_meeting.summary}' fragmenta el dia. "
                            f"Se propone reagruparla para liberar bloques de foco."
                        ),
                        calendar_id=calendar_id,
                    )
                    proposed.append(res)

        return CalendarAgentCycleResult(
            cycle_timestamp=datetime.now(UTC).isoformat(),
            lookahead_days=lookahead_days,
            health_reports=reports,
            actions_executed=executed,
            actions_proposed=proposed,
        )

    def approve_action(self, action_id: str, reviewer_notes: str | None = None) -> ActionResult:
        """User confirms execution of a staged Tier 2 action."""
        return self._actuator.execute_approved_action(action_id)

    def reject_action(
        self, action_id: str, reviewer_notes: str | None = None
    ) -> ActionResult | None:
        """User declines a staged Tier 2 action."""
        ok = self._store.update_agent_action_status(action_id, ApprovalStatus.REJECTED.value)
        if not ok:
            return None
        rec = self._store.get_agent_action(action_id)
        if not rec:
            return None
        return ActionResult(
            action_id=action_id,
            action_type=ActionType(rec["action_type"]),
            autonomy_tier=AutonomyTier(rec["autonomy_tier"]),
            approval_status=ApprovalStatus.REJECTED,
            calendar_id=rec.get("calendar_id", "primary"),
            target_event_id=rec.get("target_event_id"),
            title=rec.get("title", ""),
            scheduled_start=rec.get("scheduled_start"),
            scheduled_end=rec.get("scheduled_end"),
            before_state={},
            after_state={},
            rationale=rec.get("rationale", ""),
            undo_token=rec.get("undo_token", ""),
            success=True,
        )

    def undo_action(self, action_id: str) -> ActionResult:
        """Rollback an executed action using its snapshot."""
        return self._actuator.undo_action(action_id)
