"""Unit tests for CalendarAgent autonomous cycle and schedule health calculations."""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path

from lup_ai.actuators.base import (
    ApprovalStatus,
    AutonomyTier,
    CalendarEvent,
)
from lup_ai.actuators.google_calendar import GoogleCalendarActuator
from lup_ai.agent.calendar_agent import CalendarAgent
from lup_ai.config.settings import Settings
from lup_ai.data.local_store import LocalStore


def test_evaluate_schedule_health_clean_and_fragmented(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        calendar_agent_work_start_hour=8,
        calendar_agent_work_end_hour=18,
        calendar_agent_min_deep_work_hours=3.0,
    )
    actuator = GoogleCalendarActuator(store=store, settings=settings)
    agent = CalendarAgent(store=store, actuator=actuator, settings=settings)

    base_date = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)

    # 1. Clean empty day: 0 meetings, 0 deep work, 10h free, deficit = True
    health = agent.evaluate_schedule_health(lookahead_days=1, anchor_date=base_date)[0]
    assert health.meeting_hours == 0.0
    assert health.deep_work_hours == 0.0
    assert health.free_hours == 10.0
    assert health.deep_work_deficit is True
    assert health.meeting_overload is False
    assert health.fragmentation_index == 0.0

    # 2. Add 3 fragmented 30-min meetings throughout the day
    m1_s = datetime.combine(base_date.date(), time(9, 0), tzinfo=UTC)
    m1_e = datetime.combine(base_date.date(), time(9, 30), tzinfo=UTC)
    m2_s = datetime.combine(base_date.date(), time(12, 0), tzinfo=UTC)
    m2_e = datetime.combine(base_date.date(), time(12, 30), tzinfo=UTC)
    m3_s = datetime.combine(base_date.date(), time(15, 0), tzinfo=UTC)
    m3_e = datetime.combine(base_date.date(), time(15, 30), tzinfo=UTC)

    actuator.mock_add_event(CalendarEvent("m1", "Standup", m1_s, m1_e))
    actuator.mock_add_event(CalendarEvent("m2", "Sync", m2_s, m2_e))
    actuator.mock_add_event(CalendarEvent("m3", "Status", m3_s, m3_e))

    health_frag = agent.evaluate_schedule_health(lookahead_days=1, anchor_date=base_date)[0]
    assert health_frag.meeting_hours == 1.5
    assert health_frag.fragmentation_index > 0.0
    assert health_frag.events_count == 3


def test_calendar_agent_run_cycle_autonomous_defense(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        calendar_agent_work_start_hour=9,
        calendar_agent_work_end_hour=17,
        calendar_agent_min_deep_work_hours=2.0,
    )
    actuator = GoogleCalendarActuator(store=store, settings=settings)
    agent = CalendarAgent(store=store, actuator=actuator, settings=settings)

    base_date = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)

    # Add a morning meeting: 09:00 - 10:00
    m_s = datetime.combine(base_date.date(), time(9, 0), tzinfo=UTC)
    m_e = datetime.combine(base_date.date(), time(10, 0), tzinfo=UTC)
    actuator.mock_add_event(CalendarEvent("m1", "Sprint Planning", m_s, m_e))

    # Run agent cycle: should detect deep work deficit (< 2.0h) and book a focus block
    cycle_res = agent.run_cycle(lookahead_days=1, anchor_date=base_date, auto_execute_tier1=True)

    assert len(cycle_res.health_reports) == 1
    assert len(cycle_res.actions_executed) >= 1

    action = cycle_res.actions_executed[0]
    assert action.autonomy_tier == AutonomyTier.TIER_1_AUTONOMOUS
    assert action.approval_status == ApprovalStatus.AUTO_APPROVED
    assert action.success is True
    assert "Focus" in action.title

    # Re-evaluate health: deep work deficit should now be resolved!
    health_after = agent.evaluate_schedule_health(lookahead_days=1, anchor_date=base_date)[0]
    assert health_after.deep_work_deficit is False
    assert health_after.deep_work_hours >= 2.0


def test_calendar_agent_stage_reschedule_and_approval_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        calendar_agent_work_start_hour=9,
        calendar_agent_work_end_hour=17,
        calendar_agent_min_deep_work_hours=4.0,
    )
    actuator = GoogleCalendarActuator(store=store, settings=settings)
    agent = CalendarAgent(store=store, actuator=actuator, settings=settings)

    base_date = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)

    # Place a flexible 1-on-1 right in the middle of the day (12:00 - 13:00)
    flex_s = datetime.combine(base_date.date(), time(12, 0), tzinfo=UTC)
    flex_e = datetime.combine(base_date.date(), time(13, 0), tzinfo=UTC)
    actuator.mock_add_event(
        CalendarEvent(
            event_id="evt_flex_middle",
            summary="Flexible 1-on-1",
            start_ts=flex_s,
            end_ts=flex_e,
            is_flexible=True,
        )
    )

    # Run cycle
    cycle_res = agent.run_cycle(lookahead_days=1, anchor_date=base_date)
    assert len(cycle_res.actions_proposed) >= 1

    prop = cycle_res.actions_proposed[0]
    assert prop.autonomy_tier == AutonomyTier.TIER_2_APPROVAL_REQUIRED
    assert prop.approval_status == ApprovalStatus.PENDING_APPROVAL

    # Approve action via agent
    approved = agent.approve_action(prop.action_id, reviewer_notes="Approved via 1-click")
    assert approved is not None
    assert approved.approval_status == ApprovalStatus.APPROVED
    assert approved.success is True

    # Now test undo via agent
    undone = agent.undo_action(prop.action_id)
    assert undone is not None
    assert undone.approval_status == ApprovalStatus.ROLLED_BACK
    assert undone.success is True
