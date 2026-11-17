"""Unit tests for GoogleCalendarActuator bounded mutations and snapshot rollback."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from lup_ai.actuators.base import (
    ActionType,
    ApprovalStatus,
    AutonomyTier,
    CalendarEvent,
)
from lup_ai.actuators.google_calendar import GoogleCalendarActuator
from lup_ai.config.settings import Settings
from lup_ai.data.local_store import LocalStore


def test_actuator_token_encryption_and_loading(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    secret_key = "test-secret-key-32-chars-length!!"
    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        lup_secret_key=secret_key,
    )

    actuator = GoogleCalendarActuator(store=store, settings=settings)
    assert actuator.is_authenticated() is False

    # Store encrypted credentials
    dummy_token = {"access_token": "ya29.mock_token_12345", "refresh_token": "1//mock_refresh"}
    store.save_oauth_token("google_calendar", dummy_token, secret_key=secret_key)

    # Verify encrypted in raw sqlite
    raw_row = store.load_oauth_token("google_calendar", secret_key=secret_key)
    assert raw_row == dummy_token


def test_find_free_slots_in_working_hours(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        calendar_agent_work_start_hour=9,
        calendar_agent_work_end_hour=17,
    )
    actuator = GoogleCalendarActuator(store=store, settings=settings)

    base_date = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
    # Add a mock meeting from 10:00 to 11:00
    m_start = datetime.combine(base_date.date(), time(10, 0), tzinfo=UTC)
    m_end = datetime.combine(base_date.date(), time(11, 0), tzinfo=UTC)
    actuator.mock_add_event(
        CalendarEvent(
            event_id="m1",
            summary="Team Sync",
            start_ts=m_start,
            end_ts=m_end,
        )
    )

    free_slots = actuator.find_free_slots(
        start_date=base_date,
        days=1,
        min_duration_minutes=60,
    )

    # Expected slots: 09:00-10:00 (1h) and 11:00-17:00 (6h)
    assert len(free_slots) == 2
    s1_start, s1_end = free_slots[0]
    s2_start, s2_end = free_slots[1]
    assert s1_start.time() == time(9, 0)
    assert s1_end.time() == time(10, 0)
    assert s2_start.time() == time(11, 0)
    assert s2_end.time() == time(17, 0)


def test_tier_1_defend_focus_block(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(_env_file=None, lup_sqlite_path=db_path)
    actuator = GoogleCalendarActuator(store=store, settings=settings)

    start_dt = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
    end_dt = datetime(2026, 11, 18, 11, 0, tzinfo=UTC)

    result = actuator.defend_focus_block(
        start_dt=start_dt,
        end_dt=end_dt,
        title="Deep Work: System Architecture",
        rationale="Preserve minimum daily deep work requirement",
    )

    assert result.autonomy_tier == AutonomyTier.TIER_1_AUTONOMOUS
    assert result.action_type == ActionType.DEFEND_FOCUS
    assert result.approval_status == ApprovalStatus.AUTO_APPROVED
    assert result.success is True
    assert result.target_event_id is not None

    # Check that event was stored in mock/actuator
    events = actuator.list_events(start_dt - timedelta(hours=1), end_dt + timedelta(hours=1))
    assert any(e.summary == "Deep Work: System Architecture" for e in events)

    # Check that action was recorded in SQLite
    stored_action = store.get_agent_action(result.action_id)
    assert stored_action is not None
    assert stored_action["approval_status"] == ApprovalStatus.AUTO_APPROVED.value


def test_tier_2_propose_reschedule_and_approve(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(_env_file=None, lup_sqlite_path=db_path)
    actuator = GoogleCalendarActuator(store=store, settings=settings)

    orig_start = datetime(2026, 11, 18, 14, 0, tzinfo=UTC)
    orig_end = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
    new_start = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)
    new_end = datetime(2026, 11, 18, 17, 0, tzinfo=UTC)

    # Seed initial event in calendar
    actuator.mock_add_event(
        CalendarEvent(
            event_id="evt_flexible_1",
            summary="1-on-1 Catchup",
            start_ts=orig_start,
            end_ts=orig_end,
            is_flexible=True,
        )
    )

    # Propose reschedule (Tier 2)
    prop = actuator.propose_reschedule(
        event_id="evt_flexible_1",
        proposed_start=new_start,
        proposed_end=new_end,
        rationale="Clear uninterrupted 3h focus block in afternoon",
    )

    assert prop.autonomy_tier == AutonomyTier.TIER_2_APPROVAL_REQUIRED
    assert prop.action_type == ActionType.RESCHEDULE_MEETING
    assert prop.approval_status == ApprovalStatus.PENDING_APPROVAL
    assert prop.success is True
    assert prop.before_state["summary"] == "1-on-1 Catchup"

    # Execute approved action
    exec_res = actuator.execute_approved_action(prop.action_id)
    assert exec_res.success is True
    assert exec_res.approval_status == ApprovalStatus.APPROVED

    # Check updated event times
    updated = actuator.mock_get_event("evt_flexible_1")
    assert updated is not None
    assert updated.start_ts == new_start
    assert updated.end_ts == new_end


def test_undo_action_rollback(tmp_path: Path) -> None:
    db_path = tmp_path / "lup.db"
    store = LocalStore(db_path)
    settings = Settings(_env_file=None, lup_sqlite_path=db_path)
    actuator = GoogleCalendarActuator(store=store, settings=settings)

    orig_start = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
    orig_end = datetime(2026, 11, 18, 11, 0, tzinfo=UTC)
    new_start = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
    new_end = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)

    actuator.mock_add_event(
        CalendarEvent(
            event_id="evt_undo_target",
            summary="Product Review",
            start_ts=orig_start,
            end_ts=orig_end,
            is_flexible=True,
        )
    )

    prop = actuator.propose_reschedule(
        event_id="evt_undo_target",
        proposed_start=new_start,
        proposed_end=new_end,
        rationale="Conflict resolution",
    )
    actuator.execute_approved_action(prop.action_id)

    # Verify event moved to 15:00
    moved = actuator.mock_get_event("evt_undo_target")
    assert moved is not None
    assert moved.start_ts == new_start

    # Now Undo!
    undo_res = actuator.undo_action(prop.action_id)
    assert undo_res.success is True
    assert undo_res.approval_status == ApprovalStatus.ROLLED_BACK

    # Event must be back at original 10:00 time
    restored = actuator.mock_get_event("evt_undo_target")
    assert restored is not None
    assert restored.start_ts == orig_start
    assert restored.end_ts == orig_end
