"""Google Calendar Actuator executing real and simulated mutations with OAuth 2.0 and Undo."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from lup_ai.actuators.base import (
    ActionResult,
    ActionType,
    ApprovalStatus,
    AutonomyTier,
    CalendarActuatorProtocol,
    CalendarEvent,
)
from lup_ai.config.settings import Settings, get_settings
from lup_ai.data.local_store import LocalStore

_LOGGER = logging.getLogger(__name__)


class GoogleCalendarAuthError(RuntimeError):
    """Raised when Google Calendar credentials or tokens are invalid or missing."""


class GoogleCalendarActuator(CalendarActuatorProtocol):
    """Execute bounded calendar mutations on Google Calendar API v3."""

    def __init__(
        self,
        store: LocalStore,
        settings: Settings | None = None,
        google_service: Any | None = None,
    ) -> None:
        self._store = store
        self._settings = settings or get_settings()
        self._injected_service = google_service
        self._events_mock: dict[str, dict[str, Any]] = {}

    def mock_add_event(self, event: CalendarEvent) -> None:
        """Seed an in-memory mock event for testing purposes."""
        self._events_mock[event.event_id] = {
            "id": event.event_id,
            "summary": event.summary,
            "start": {"dateTime": event.start_ts.isoformat()},
            "end": {"dateTime": event.end_ts.isoformat()},
            "attendees": [
                {"email": f"attendee_{i}@example.com"} for i in range(event.attendees_count)
            ],
            "is_flexible": event.is_flexible,
        }

    def mock_get_event(self, event_id: str) -> CalendarEvent | None:
        """Retrieve an in-memory mock event for test verification."""
        it = self._events_mock.get(event_id)
        if not it:
            return None
        summary = it.get("summary", "")
        return CalendarEvent(
            event_id=it["id"],
            summary=summary,
            start_ts=datetime.fromisoformat(it["start"]["dateTime"]),
            end_ts=datetime.fromisoformat(it["end"]["dateTime"]),
            is_deep_work="DEEP WORK" in summary.upper() or "FOCUS" in summary.upper(),
            is_flexible=it.get("is_flexible", False),
            attendees_count=len(it.get("attendees", [])),
            raw_payload=it,
        )

    def is_authenticated(self) -> bool:
        """Return True when valid Google OAuth credentials exist in store or file."""
        if self._injected_service is not None:
            return True
        token = self._store.load_oauth_token("google_calendar", self._settings.lup_secret_key)
        return bool(token and token.get("token"))

    def _get_service(self) -> Any:
        if self._injected_service is not None:
            return self._injected_service

        token_data = self._store.load_oauth_token("google_calendar", self._settings.lup_secret_key)
        if not token_data:
            return None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            creds = Credentials(
                token=token_data.get("token"),
                refresh_token=token_data.get("refresh_token"),
                token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=token_data.get("client_id"),
                client_secret=token_data.get("client_secret"),
                scopes=token_data.get(
                    "scopes", ["https://www.googleapis.com/auth/calendar.events"]
                ),
            )
            return build("calendar", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            _LOGGER.warning("Could not build Google Calendar client: %s", exc)
            return None

    def list_events(
        self, start_dt: datetime, end_dt: datetime, calendar_id: str = "primary"
    ) -> list[CalendarEvent]:
        """Fetch real events or in-memory mock events within the time window."""
        service = self._get_service()
        events: list[CalendarEvent] = []

        if service is not None and hasattr(service, "events"):
            try:
                res = (
                    service.events()
                    .list(
                        calendarId=calendar_id,
                        timeMin=start_dt.isoformat(),
                        timeMax=end_dt.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                items = res.get("items", [])
                for it in items:
                    s_raw = it.get("start", {}).get("dateTime") or it.get("start", {}).get("date")
                    e_raw = it.get("end", {}).get("dateTime") or it.get("end", {}).get("date")
                    if not s_raw or not e_raw:
                        continue
                    s_dt = datetime.fromisoformat(s_raw.replace("Z", "+00:00"))
                    e_dt = datetime.fromisoformat(e_raw.replace("Z", "+00:00"))
                    summary = it.get("summary", "Evento")
                    is_dw = "DEEP WORK" in summary.upper() or "FOCUS" in summary.upper()
                    events.append(
                        CalendarEvent(
                            event_id=it["id"],
                            summary=summary,
                            start_ts=s_dt,
                            end_ts=e_dt,
                            is_deep_work=is_dw,
                            is_flexible=bool("SYNC" in summary.upper() or "1:1" in summary.upper()),
                            attendees_count=len(it.get("attendees", [])),
                            raw_payload=it,
                        )
                    )
                return events
            except Exception as exc:
                _LOGGER.warning("Google Calendar API call failed, using mock state: %s", exc)

        # Fallback to local memory mock state
        for ev_id, it in self._events_mock.items():
            s_dt = datetime.fromisoformat(it["start"]["dateTime"])
            e_dt = datetime.fromisoformat(it["end"]["dateTime"])
            if (
                (s_dt >= start_dt and s_dt <= end_dt)
                or (e_dt >= start_dt and e_dt <= end_dt)
                or (s_dt <= start_dt and e_dt >= end_dt)
            ):
                summary = it.get("summary", "Evento")
                events.append(
                    CalendarEvent(
                        event_id=ev_id,
                        summary=summary,
                        start_ts=s_dt,
                        end_ts=e_dt,
                        is_deep_work="DEEP WORK" in summary.upper() or "FOCUS" in summary.upper(),
                        is_flexible=it.get("is_flexible", False),
                        attendees_count=len(it.get("attendees", [])),
                        raw_payload=it,
                    )
                )
        return events

    def find_free_slots(
        self,
        start_dt: datetime | None = None,
        end_dt: datetime | None = None,
        min_duration_minutes: int = 90,
        work_start_hour: int | None = None,
        work_end_hour: int | None = None,
        calendar_id: str = "primary",
        start_date: datetime | None = None,
        days: int = 1,
    ) -> list[tuple[datetime, datetime]]:
        """Identify open contiguous slots within standard working hours."""
        eff_start = start_dt or start_date or datetime.now(UTC)
        eff_end = end_dt or (eff_start + timedelta(days=days))
        start_h = (
            work_start_hour
            if work_start_hour is not None
            else self._settings.calendar_agent_work_start_hour
        )
        end_h = (
            work_end_hour
            if work_end_hour is not None
            else self._settings.calendar_agent_work_end_hour
        )

        existing = sorted(
            self.list_events(eff_start, eff_end, calendar_id),
            key=lambda e: e.start_ts,
        )

        free_slots: list[tuple[datetime, datetime]] = []
        if end_dt is not None:
            days_count = max(
                1,
                (eff_end.date() - eff_start.date()).days
                + (0 if eff_end.time() <= time(start_h, 0) else 1),
            )
        else:
            days_count = max(1, days)

        for d_idx in range(days_count):
            current_day = eff_start.date() + timedelta(days=d_idx)
            day_start = datetime.combine(
                current_day, time(start_h, 0), tzinfo=eff_start.tzinfo or UTC
            )
            day_end = datetime.combine(current_day, time(end_h, 0), tzinfo=eff_start.tzinfo or UTC)

            cursor = day_start
            day_events = [e for e in existing if e.start_ts.date() == current_day]

            for ev in day_events:
                # Ensure tz consistency
                ev_start = ev.start_ts if ev.start_ts.tzinfo else ev.start_ts.replace(tzinfo=UTC)
                ev_end = ev.end_ts if ev.end_ts.tzinfo else ev.end_ts.replace(tzinfo=UTC)

                if ev_start > cursor:
                    gap_minutes = (ev_start - cursor).total_seconds() / 60
                    if gap_minutes >= min_duration_minutes:
                        free_slots.append((cursor, ev_start))
                cursor = max(cursor, ev_end)

            if cursor < day_end:
                gap_minutes = (day_end - cursor).total_seconds() / 60
                if gap_minutes >= min_duration_minutes:
                    free_slots.append((cursor, day_end))

        return free_slots

    def defend_focus_block(
        self,
        start_dt: datetime,
        end_dt: datetime,
        title: str = "Deep Work - Focus Defense [LUP AI]",
        rationale: str = "Automatic focus protection based on active goals",
        calendar_id: str = "primary",
    ) -> ActionResult:
        """Autonomously book an unoccupied slot to protect deep work (Tier 1)."""
        action_id = f"act_defend_{uuid.uuid4().hex[:10]}"
        event_id = f"gcal_focus_{uuid.uuid4().hex[:12]}"
        undo_token = f"undo_{action_id}"

        event_payload = {
            "id": event_id,
            "summary": title,
            "description": (
                f"Autonomously defended by LUP AI. Rationale: {rationale}. UndoToken: {undo_token}"
            ),
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
            "transparency": "opaque",  # Marks user as Busy in Google Calendar
            "colorId": "11",  # Bold Red / Tomato in Google Calendar
        }

        service = self._get_service()
        dispatched = False

        if service is not None and hasattr(service, "events"):
            try:
                created = (
                    service.events().insert(calendarId=calendar_id, body=event_payload).execute()
                )
                event_id = created.get("id", event_id)
                dispatched = True
            except Exception as exc:
                _LOGGER.warning("Could not insert event into Google Calendar: %s", exc)

        if not dispatched:
            self._events_mock[event_id] = event_payload

        # Record action in SQLite
        now_iso = datetime.now(UTC).isoformat()
        action_record = {
            "action_id": action_id,
            "created_at": now_iso,
            "executed_at": now_iso,
            "action_type": ActionType.DEFEND_FOCUS.value,
            "calendar_id": calendar_id,
            "target_event_id": event_id,
            "title": title,
            "scheduled_start": start_dt.isoformat(),
            "scheduled_end": end_dt.isoformat(),
            "before_state_json": {},
            "after_state_json": event_payload,
            "rationale": rationale,
            "autonomy_tier": AutonomyTier.TIER_1_AUTONOMOUS.value,
            "approval_status": ApprovalStatus.AUTO_APPROVED.value,
            "undo_token": undo_token,
        }
        self._store.record_agent_action(action_record)

        return ActionResult(
            action_id=action_id,
            action_type=ActionType.DEFEND_FOCUS,
            autonomy_tier=AutonomyTier.TIER_1_AUTONOMOUS,
            approval_status=ApprovalStatus.AUTO_APPROVED,
            calendar_id=calendar_id,
            target_event_id=event_id,
            title=title,
            scheduled_start=start_dt.isoformat(),
            scheduled_end=end_dt.isoformat(),
            before_state={},
            after_state=event_payload,
            rationale=rationale,
            undo_token=undo_token,
            success=True,
        )

    def propose_reschedule(
        self,
        event_id: str,
        new_start_dt: datetime | None = None,
        new_end_dt: datetime | None = None,
        rationale: str = "",
        calendar_id: str = "primary",
        proposed_start: datetime | None = None,
        proposed_end: datetime | None = None,
    ) -> ActionResult:
        """Stage a disruptive reschedule action requiring human approval (Tier 2)."""
        eff_start = new_start_dt or proposed_start
        eff_end = new_end_dt or proposed_end
        if eff_start is None or eff_end is None:
            raise ValueError("Must provide start and end datetimes for reschedule")

        action_id = f"act_resched_{uuid.uuid4().hex[:10]}"
        undo_token = f"undo_{action_id}"

        # Fetch current snapshot before mutation
        before_state: dict[str, Any] = {}
        service = self._get_service()
        if service is not None and hasattr(service, "events"):
            try:
                before_state = (
                    service.events().get(calendarId=calendar_id, eventId=event_id).execute()
                )
            except Exception:
                before_state = self._events_mock.get(event_id, {})
        else:
            before_state = self._events_mock.get(event_id, {})

        summary = before_state.get("summary", "Reunión")
        after_state = {
            **before_state,
            "start": {"dateTime": eff_start.isoformat()},
            "end": {"dateTime": eff_end.isoformat()},
        }

        now_iso = datetime.now(UTC).isoformat()
        action_record = {
            "action_id": action_id,
            "created_at": now_iso,
            "executed_at": None,
            "action_type": ActionType.RESCHEDULE_MEETING.value,
            "calendar_id": calendar_id,
            "target_event_id": event_id,
            "title": f"Reagendar '{summary}'",
            "scheduled_start": eff_start.isoformat(),
            "scheduled_end": eff_end.isoformat(),
            "before_state_json": before_state,
            "after_state_json": after_state,
            "rationale": rationale,
            "autonomy_tier": AutonomyTier.TIER_2_APPROVAL_REQUIRED.value,
            "approval_status": ApprovalStatus.PENDING_APPROVAL.value,
            "undo_token": undo_token,
        }
        self._store.record_agent_action(action_record)

        return ActionResult(
            action_id=action_id,
            action_type=ActionType.RESCHEDULE_MEETING,
            autonomy_tier=AutonomyTier.TIER_2_APPROVAL_REQUIRED,
            approval_status=ApprovalStatus.PENDING_APPROVAL,
            calendar_id=calendar_id,
            target_event_id=event_id,
            title=f"Reagendar '{summary}'",
            scheduled_start=eff_start.isoformat(),
            scheduled_end=eff_end.isoformat(),
            before_state=before_state,
            after_state=after_state,
            rationale=rationale,
            undo_token=undo_token,
            success=True,
        )

    def execute_approved_action(self, action_id: str) -> ActionResult:
        """Apply an approved Tier 2 action on Google Calendar."""
        action = self._store.get_agent_action(action_id)
        if action is None:
            raise ValueError(f"Action '{action_id}' not found.")

        if action["approval_status"] not in {
            ApprovalStatus.PENDING_APPROVAL.value,
            ApprovalStatus.APPROVED.value,
        }:
            raise ValueError(f"Action '{action_id}' is not in a payable pending state.")

        event_id = action["target_event_id"]
        calendar_id = action.get("calendar_id", "primary")
        after_state = (
            json.loads(action["after_state_json"])
            if isinstance(action["after_state_json"], str)
            else action["after_state_json"]
        )

        service = self._get_service()
        if service is not None and hasattr(service, "events"):
            try:
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=event_id,
                    body=after_state,
                ).execute()
            except Exception as exc:
                _LOGGER.warning("Could not patch Google Calendar event: %s", exc)

        # Update in-memory mock if present
        if event_id in self._events_mock:
            self._events_mock[event_id] = after_state

        now_iso = datetime.now(UTC).isoformat()
        self._store.update_agent_action_status(action_id, ApprovalStatus.APPROVED.value, now_iso)

        return ActionResult(
            action_id=action_id,
            action_type=ActionType(action["action_type"]),
            autonomy_tier=AutonomyTier(action["autonomy_tier"]),
            approval_status=ApprovalStatus.APPROVED,
            calendar_id=calendar_id,
            target_event_id=event_id,
            title=action.get("title", ""),
            scheduled_start=action.get("scheduled_start"),
            scheduled_end=action.get("scheduled_end"),
            before_state=json.loads(action["before_state_json"]),
            after_state=after_state,
            rationale=action["rationale"],
            undo_token=action["undo_token"],
            success=True,
        )

    def undo_action(self, action_id: str) -> ActionResult:
        """Roll back an executed action using its stored before-state snapshot."""
        action = self._store.get_agent_action(action_id)
        if action is None:
            raise ValueError(f"Action '{action_id}' not found.")

        action_type = ActionType(action["action_type"])
        calendar_id = action.get("calendar_id", "primary")
        target_event_id = action.get("target_event_id")
        service = self._get_service()

        if action_type == ActionType.DEFEND_FOCUS:
            # Delete the inserted focus block
            if service is not None and hasattr(service, "events") and target_event_id:
                try:
                    service.events().delete(
                        calendarId=calendar_id, eventId=target_event_id
                    ).execute()
                except Exception as exc:
                    _LOGGER.warning("Could not delete focus event on undo: %s", exc)
            if target_event_id in self._events_mock:
                del self._events_mock[target_event_id]

        elif action_type == ActionType.RESCHEDULE_MEETING:
            # Revert to before_state
            before_state = (
                json.loads(action["before_state_json"])
                if isinstance(action["before_state_json"], str)
                else action["before_state_json"]
            )
            if service is not None and hasattr(service, "events") and target_event_id:
                try:
                    service.events().patch(
                        calendarId=calendar_id,
                        eventId=target_event_id,
                        body=before_state,
                    ).execute()
                except Exception as exc:
                    _LOGGER.warning("Could not revert event on undo: %s", exc)
            if target_event_id in self._events_mock:
                self._events_mock[target_event_id] = before_state

        now_iso = datetime.now(UTC).isoformat()
        self._store.update_agent_action_status(action_id, ApprovalStatus.ROLLED_BACK.value, now_iso)

        return ActionResult(
            action_id=action_id,
            action_type=action_type,
            autonomy_tier=AutonomyTier(action["autonomy_tier"]),
            approval_status=ApprovalStatus.ROLLED_BACK,
            calendar_id=calendar_id,
            target_event_id=target_event_id,
            title=f"UNDO: {action.get('title')}",
            scheduled_start=action.get("scheduled_start"),
            scheduled_end=action.get("scheduled_end"),
            before_state=json.loads(action["after_state_json"]),
            after_state=json.loads(action["before_state_json"]),
            rationale=f"Compensating rollback of action {action_id}",
            undo_token=action["undo_token"],
            success=True,
        )
