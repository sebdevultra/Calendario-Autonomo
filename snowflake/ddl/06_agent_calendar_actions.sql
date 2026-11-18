-- Create ANALYTICS action log table for autonomous calendar agent actions and semantic view.
-- Enables immutable auditability, rollbacks, and historical reasoning for LUP AI.

USE ROLE LUP_INGEST_RW;
USE WAREHOUSE LUP_WH_XS;
USE DATABASE LUP_AI_DB;
USE SCHEMA ANALYTICS;

CREATE TABLE IF NOT EXISTS FCT_AGENT_SCHEDULE_ACTIONS (
    action_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    executed_at TIMESTAMP_NTZ,
    action_type VARCHAR(32) NOT NULL, -- DEFEND_FOCUS, RESCHEDULE_MEETING, DECLINE_CONFLICT, RESTORE_EVENT
    calendar_id VARCHAR(128) DEFAULT 'primary',
    target_event_id VARCHAR(128),
    title VARCHAR(256),
    scheduled_start TIMESTAMP_NTZ,
    scheduled_end TIMESTAMP_NTZ,
    before_state_json VARIANT, -- Snapshot before mutation (allows 1-click Undo)
    after_state_json VARIANT,  -- Actual payload dispatched to Google Calendar
    rationale VARCHAR(512) NOT NULL,
    autonomy_tier VARCHAR(16) NOT NULL, -- TIER_1_AUTONOMOUS, TIER_2_APPROVAL_REQUIRED
    approval_status VARCHAR(24) DEFAULT 'AUTO_APPROVED', -- AUTO_APPROVED, PENDING_APPROVAL, APPROVED, REJECTED, ROLLED_BACK
    undo_token VARCHAR(64),
    ingested_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT pk_fct_agent_schedule_actions PRIMARY KEY (action_id)
);

-- Semantic view for LUP_AGENT_RO read-only inspection
USE SCHEMA SEMANTIC;

CREATE OR REPLACE VIEW VW_AGENT_SCHEDULE_ACTIONS AS
SELECT
    action_id,
    created_at,
    executed_at,
    DATE_TRUNC('DAY', created_at) AS action_date,
    action_type,
    calendar_id,
    target_event_id,
    title,
    scheduled_start,
    scheduled_end,
    rationale,
    autonomy_tier,
    approval_status,
    undo_token
FROM LUP_AI_DB.ANALYTICS.FCT_AGENT_SCHEDULE_ACTIONS;
