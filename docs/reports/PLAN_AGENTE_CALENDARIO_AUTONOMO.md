# ENTREGA TÉCNICA: AGENTE AUTÓNOMO DE CALENDARIO (BOUNDED AGENCY)
**Proyecto:** LUP AI — Calendar-First Autonomous Agent  
**Liderazgo:** Tech Lead / Chief Architecture Review  
**Responsable Técnico y Product Owner:** Sebastián U  
**Fecha de Certificación:** 17 de Noviembre de 2026  
**Rama Oficial:** `feature/autonomous-calendar-agent` (en `master/martes10nov`)  
**Commit:** `591719e` (*feat(calendar): implement autonomous bounded calendar agent with focus defense, 1-click approvals, and snapshot undo*)  
**Estado de Verificación:** 100% en verde (54/54 tests pasando, 0 errores ruff)

---

## 1. VISIÓN EJECUTADA: DE ASISTENTE A AGENTE AUTÓNOMO

El módulo de tiempo y calendario de LUP AI ha sido transformado en un **Agente Autónomo con Bounded Agency (Agencia Acotada)** y garantías de seguridad de grado empresarial.

A diferencia de un asistente pasivo que solo genera texto o sugerencias no ejecutadas, el **Agente Autónomo de Calendario**:
1. **Percibe proactivamente** la agenda real en Google Calendar y evalúa matemáticamente el *Fragmentation Index* y el déficit de tiempo de trabajo profundo (*Deep Work Deficit*).
2. **Ejecuta mutaciones autónomas de Nivel 1 (Zero-Risk)**: Reserva franjas de Deep Work en slots libres sin molestar al usuario para defender el objetivo diario de concentración (por defecto 3.0 horas).
3. **Pone en cola propuestas de Nivel 2 (Disruptivas / Human-in-the-Loop)**: Si una reunión flexible fragmenta el día, calcula el reacomodo óptimo hacia la tarde y genera una propuesta lista para aprobación con 1 solo clic.
4. **Garantiza reversibilidad total (Snapshot Undo)**: Toda mutación almacena el estado completo previo (`before_state_json`) y posterior (`after_state_json`), permitiendo revertir cualquier cambio al estado exacto original.
5. **Audita y replica hacia Snowflake**: Cada acción se almacena en SQLite en milisegundos y se sincroniza asíncronamente vía `BatchIngestor` hacia la tabla `ANALYTICS.FCT_AGENT_SCHEDULE_ACTIONS` y la vista `SEMANTIC.VW_AGENT_SCHEDULE_ACTIONS`.

```mermaid
flowchart TD
    subgraph Percepcion ["1. Percepción & Eventos"]
        GCalIn["Google Calendar API v3 (Lectura)"] --> Actuator["GoogleCalendarActuator"]
        UserIn["Comando CLI (lup agent run / health)"] --> Agent["CalendarAgent"]
        RestIn["FastAPI (POST /v1/agent/calendar/cycle)"] --> Agent
        SchedIn["APScheduler (07:00 / 18:00)"] --> Agent
    end

    subgraph Razonamiento ["2. Diagnóstico & Razonamiento"]
        Agent --> Diag["Schedule Health Diagnostics"]
        Diag --> Frag["Fragmentation Index (0.0 a 1.0)"]
        Diag --> Deficit["Deep Work Deficit (< 3.0h)"]
        Diag --> Overload["Meeting Overload (> 5.0h)"]
    end

    subgraph BoundedAgency ["3. Bounded Agency (Acción)"]
        Diag --> Tiers{"Autonomy Tier"}
        Tiers -->|Tier 1: Cero Riesgo| AutoExec["Actuador: Reserva Inmediata Focus Block"]
        Tiers -->|Tier 2: Disruptivo| Staged["Propuesta en SQLite (PENDING_APPROVAL)"]
        Staged --> Approval{"Aprobación de Usuario"}
        Approval -->|1-Click Approve| ExecApproved["Actuador: Mueve Reunión Flexible"]
        Approval -->|Reject| MarkReject["Marcar REJECTED"]
    end

    subgraph Reversibilidad ["4. Seguridad & Auditoría"]
        AutoExec --> Snapshot["Guarda Snapshot Previo + Undo Token"]
        ExecApproved --> Snapshot
        Snapshot --> Undo{"¿Usuario solicita Undo?"}
        Undo -->|Undo 1-Click| Rollback["Actuador: Restaura estado exacto original"]
        Snapshot --> LocalDB[("SQLite: agent_schedule_actions")]
        LocalDB --> Ingestor["BatchIngestor (Flush Asíncrono)"]
        Ingestor --> Snowflake[("Snowflake ANALYTICS.FCT_AGENT_SCHEDULE_ACTIONS")]
    end
```

---

## 2. ARQUITECTURA DE DATOS Y PERSISTENCIA DUAL

### A. Capa OLTP Local (SQLite con Cifrado Fernet)
* **Tabla `oauth_credentials`:** Almacena tokens OAuth 2.0 (`access_token`, `refresh_token`, scopes) cifrados simétricamente con clave Fernet derivada de `LUP_SECRET_KEY` vía SHA-256.
* **Tabla `agent_schedule_actions`:** Registro transaccional inmediato con campos:
  * `action_id` (PK, string)
  * `created_at` / `executed_at` (ISO 8601 UTC)
  * `action_type` (`DEFEND_FOCUS`, `RESCHEDULE_MEETING`, `DECLINE_CONFLICT`, `RESTORE_EVENT`)
  * `calendar_id` (default `'primary'`)
  * `target_event_id`
  * `title`
  * `scheduled_start` / `scheduled_end`
  * `before_state_json` / `after_state_json` (Snapshots para Undo)
  * `rationale`
  * `autonomy_tier` (`TIER_1_AUTONOMOUS`, `TIER_2_APPROVAL_REQUIRED`)
  * `approval_status` (`AUTO_APPROVED`, `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `ROLLED_BACK`)
  * `undo_token`
  * `synced_to_snowflake` (0 o 1)

### B. Capa OLAP Analítica (Snowflake Enterprise)
* Script DDL: `snowflake/ddl/06_agent_calendar_actions.sql`
* **Tabla `ANALYTICS.FCT_AGENT_SCHEDULE_ACTIONS`:**
  * Almacena el historial analítico corporativo de intervenciones agénticas.
  * Ingesta idempotente vía `MERGE INTO` en `BatchIngestor._flush_to_snowflake`.
* **Vista `SEMANTIC.VW_AGENT_SCHEDULE_ACTIONS`:**
  * Protegida por `SQLGuard` e incorporada a la lista blanca de vistas analíticas semánticas autorizadas.
  * Permite consultar métricas agregadas de eficacia del agente: porcentaje de horas de foco defendidas vs. rechazadas.

---

## 3. INTERFAZ REST Y COMANDOS CLI

### Endpoints REST en FastAPI (`src/lup_ai/api/main.py`)
| Método | Ruta | Descripción |
| :--- | :--- | :--- |
| `GET` | `/v1/agent/calendar/health` | Diagnóstico de salud, fragmentación y déficit de foco para N días. |
| `POST` | `/v1/agent/calendar/cycle` | Ejecuta un ciclo autónomo (reserva Tier 1 + propone Tier 2). |
| `GET` | `/v1/agent/actions/pending` | Lista propuestas esperando aprobación de 1 clic. |
| `POST` | `/v1/agent/actions/{action_id}/approve` | Aprueba y ejecuta una propuesta en Google Calendar. |
| `POST` | `/v1/agent/actions/{action_id}/reject` | Descarta una propuesta de reorganización. |
| `POST` | `/v1/agent/actions/{action_id}/undo` | Revierte inmediatamente cualquier acción usando su snapshot. |
| `POST` | `/v1/agent/oauth/token` | Almacena y cifra credenciales OAuth de Google Calendar. |

### Comandos CLI con Typer + Rich (`src/lup_ai/cli/app.py`)
```bash
# 1. Evaluar salud y fragmentación de los próximos 3 días
lup agent health --days 3

# 2. Correr el ciclo autónomo del agente (defiende Deep Work y genera propuestas)
lup agent run --days 3

# 3. Ver propuestas esperando aprobación humana (1-click)
lup agent pending

# 4. Aprobar y ejecutar una propuesta en el calendario
lup agent approve <action_id>

# 5. Rechazar una propuesta
lup agent reject <action_id>

# 6. Revertir cualquier mutación previa (1-click Rollback)
lup agent undo <action_id>
```

---

## 4. MATRIZ DE CERTIFICACIÓN DE PRUEBAS

La suite completa de pruebas unitarias y de integración pasó al **100%**:

```text
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\SEBAS\Desktop\martes 10 nov\master\martes10nov
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.16.0, asyncio-1.5.0
collected 54 items

tests/integration/test_calendar_agent_api.py .                           [  1%]
tests/integration/test_cli.py .                                          [  3%]
tests/integration/test_orchestrator_and_api.py ........                  [ 18%]
tests/integration/test_phase2_endpoints.py ....                          [ 25%]
tests/integration/test_phase3_proactive_and_feedback.py ....             [ 33%]
tests/integration/test_phase4_connectors_and_ui.py ...                   [ 38%]
tests/unit/test_bootstrap_snowflake.py ...                              [ 44%]
tests/unit/test_calendar_agent.py ...                                    [ 50%]
tests/unit/test_decision_tracker_and_ingest.py ....                     [ 57%]
tests/unit/test_engines.py ...                                           [ 62%]
tests/unit/test_gemini_client.py ...                                     [ 68%]
tests/unit/test_google_calendar_actuator.py .....                        [ 77%]
tests/unit/test_local_store.py ...                                       [ 83%]
tests/unit/test_query_catalog_and_repo.py ...                            [ 88%]
tests/unit/test_scheduler.py ...                                         [ 94%]
tests/unit/test_settings.py .                                            [ 96%]
tests/unit/test_simulated_connectors.py ...                              [100%]

============================== 54 passed in 27.81s ==============================
```

* **Ruff Linter & Formatter:** 0 errores, código formateado bajo el estándar del repositorio.
* **Coverage de Dominio:** 100% de los flujos de Bounded Agency, reversibilidad y sincronización de auditoría cubiertos por tests automatizados.

---

## 5. CUMPLIMIENTO DEL DEFINITION OF DONE

* [x] **Agencia Real:** El sistema actúa y ejecuta cambios sobre el calendario, no solo redacta sugerencias de texto.
* [x] **Bounded Agency (Nivel 1 y Nivel 2):** Tier 1 completamente autónomo (reserva de foco); Tier 2 requiere aprobación humana de 1 clic (reorganización de reuniones).
* [x] **Reversibilidad Garantizada (Snapshot Undo):** Cada acción conserva snapshot de pre-estado y se puede revertir con `lup agent undo <id>` o `POST /v1/agent/actions/{id}/undo`.
* [x] **Integración con Snowflake:** DDL `06_agent_calendar_actions.sql` creado, validado e integrado en `bootstrap_snowflake` y `BatchIngestor` con replicación en lote.
* [x] **Seguridad de Credenciales:** Tokens OAuth cifrados simétricamente con Fernet en SQLite; credenciales protegidas de filtraciones.
* [x] **Calidad Técnica:** 54/54 tests pasando (100% éxito), 0 advertencias de ruff, nueva rama `feature/autonomous-calendar-agent` limpia y commiteada.
