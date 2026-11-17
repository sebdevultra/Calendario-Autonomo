"""Integration tests for Autonomous Calendar Agent REST API and batch sync."""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path

from fastapi.testclient import TestClient

from lup_ai.actuators.base import CalendarEvent
from lup_ai.actuators.google_calendar import GoogleCalendarActuator
from lup_ai.agent.calendar_agent import CalendarAgent
from lup_ai.api.main import create_app
from lup_ai.config.settings import Settings
from lup_ai.data.batch_ingestor import BatchIngestor
from lup_ai.data.local_csv_repo import LocalCsvAnalyticsRepository
from lup_ai.data.local_store import LocalStore
from lup_ai.seeds.generate_synthetic_data import generate_dataset


def test_calendar_agent_api_full_lifecycle(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = tmp_path / "lup.db"
    generate_dataset(output_dir=data_dir, days=30, sqlite_path=db_path)

    settings = Settings(
        _env_file=None,
        lup_sqlite_path=db_path,
        calendar_agent_work_start_hour=9,
        calendar_agent_work_end_hour=17,
        calendar_agent_min_deep_work_hours=3.0,
    )
    store = LocalStore(db_path)
    repo = LocalCsvAnalyticsRepository(data_dir=data_dir)
    actuator = GoogleCalendarActuator(store=store, settings=settings)
    agent = CalendarAgent(store=store, actuator=actuator, settings=settings)

    # Seed an event
    base_date = datetime.now(UTC)
    s_time = datetime.combine(base_date.date(), time(13, 0), tzinfo=UTC)
    e_time = datetime.combine(base_date.date(), time(14, 0), tzinfo=UTC)
    actuator.mock_add_event(
        CalendarEvent(
            event_id="evt_test_api_1",
            summary="Midday Discussion",
            start_ts=s_time,
            end_ts=e_time,
            is_flexible=True,
        )
    )

    app = create_app(
        settings=settings,
        store=store,
        repo=repo,
        calendar_actuator=actuator,
        calendar_agent=agent,
        enable_scheduler=False,
    )

    with TestClient(app) as client:
        # 1. GET /v1/agent/calendar/health
        resp = client.get("/v1/agent/calendar/health?days=3")
        assert resp.status_code == 200
        health = resp.json()
        assert len(health) == 3
        assert "fragmentation_index" in health[0]
        assert "deep_work_deficit" in health[0]

        # 2. POST /v1/agent/calendar/cycle
        cycle_resp = client.post("/v1/agent/calendar/cycle", json={"lookahead_days": 2})
        assert cycle_resp.status_code == 200
        cycle_data = cycle_resp.json()
        assert cycle_data["lookahead_days"] == 2
        assert len(cycle_data["health_reports"]) == 2
        assert len(cycle_data["actions_executed"]) >= 1

        executed_id = cycle_data["actions_executed"][0]["action_id"]

        # 3. GET /v1/agent/actions/pending
        pending_resp = client.get("/v1/agent/actions/pending")
        assert pending_resp.status_code == 200
        pending_data = pending_resp.json()

        # If proposals were staged, test approve / reject
        if pending_data:
            prop_id = pending_data[0]["action_id"]
            appr_resp = client.post(
                f"/v1/agent/actions/{prop_id}/approve",
                json={"reviewer_notes": "Looks good from API"},
            )
            assert appr_resp.status_code == 200
            appr_data = appr_resp.json()
            assert appr_data["approval_status"] == "APPROVED"
            assert appr_data["success"] is True

        # 4. POST /v1/agent/actions/{action_id}/undo
        undo_resp = client.post(f"/v1/agent/actions/{executed_id}/undo")
        assert undo_resp.status_code == 200
        undo_data = undo_resp.json()
        assert undo_data["approval_status"] == "ROLLED_BACK"

        # 5. POST /v1/agent/oauth/token
        oauth_resp = client.post(
            "/v1/agent/oauth/token",
            json={
                "provider": "google_calendar",
                "token_payload": {"access_token": "ya29.integration_test"},
            },
        )
        assert oauth_resp.status_code == 200
        assert oauth_resp.json()["status"] == "saved"

    # 6. Verify BatchIngestor flushes agent actions to Snowflake / marks synced
    ingestor = BatchIngestor(settings=settings, store=store, local_csv_repo=repo)
    unsynced = store.get_unsynced_agent_actions()
    assert len(unsynced) >= 1

    flush_res = ingestor.flush_batch()
    assert flush_res.agent_actions_synced >= 1
    assert store.get_unsynced_agent_actions() == []
