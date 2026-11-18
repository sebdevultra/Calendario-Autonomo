# INFORME TÉCNICO Y ROADMAP DE PRODUCTO: ESTADO ACTUAL, FEATURES FALTANTES PARA MVP Y MEJORAS FUTURAS

**Proyecto:** LUP AI — Autonomous Decision & Calendar Intelligence Platform  
**Rol:** Tech Lead / Chief Architecture Review  
**Responsable Técnico / Product Owner:** Sebastián U  
**Fecha de Emisión:** 17 de Noviembre de 2026  
**Rama Activa:** `feature/autonomous-calendar-agent` (en `master/martes10nov`)  
**Commit Certificado:** `591719e` (*feat(calendar): implement autonomous bounded calendar agent with focus defense, 1-click approvals, and snapshot undo*)  
**Estado de Verificación:** **54/54 tests pasando (100%)** | **0 advertencias Ruff** | **0 deuda técnica bloqueante**

---

## 1. RESUMEN EJECUTIVO

El proyecto **LUP AI** ha alcanzado una etapa de madurez técnica de alto nivel tras su reciente evolución:
1. Se superó la fase de un *asistente conversacional pasivo*, implementando un **Agente Autónomo en el dominio de Calendario** dotado de **Bounded Agency (Agencia Acotada)**, capacidad de mutación directa sobre Google Calendar API v3, cola de aprobación humana y reversibilidad garantizada por snapshots (*Snapshot Undo*).
2. La arquitectura conserva la separación estricta entre la **capa transaccional/local (SQLite con cifrado Fernet)** y la **capa analítica inmutable (Snowflake OLAP)** con ingesta desacoplada vía `BatchIngestor` para eliminar costos y latencias de transacciones OLTP en Snowflake.
3. El código base en `master/martes10nov` cuenta con **54 pruebas automatizadas** que cubren el 100% de los flujos críticos (seguridad SQLGuard, anonimización PII, deduplicación de ingesta, cálculo de fragmentación, defensa de foco y rollback).

El objetivo de este informe es presentar una radiografía transparente de **lo que ya está construido**, definir con precisión de ingeniería **qué falta para lanzar el MVP (Producto Mínimo Viable)** y trazar el **Roadmap Estratégico (versión 2.0 y posteriores)**.

---

## 2. RADIOGRAFÍA DEL SISTEMA TAL Y COMO ESTÁ HOY

```mermaid
flowchart TD
    subgraph Core ["1. Núcleo de Inteligencia & Guardrails"]
        Gemini["Gemini 3.8 / Flash (LLM)"]
        Anon["TextAnonymizer (Masked Cloud PII)"]
        Guard["SQLGuard (Whitelist AST)"]
        Decision["DecisionTracker (Closed Loop)"]
    end

    subgraph CalendarAgent ["2. Agente Autónomo de Calendario"]
        AgentCore["CalendarAgent"]
        Actuator["GoogleCalendarActuator"]
        Diag["Schedule Health Engine (Fragmentation & Deep Work)"]
        Tier1["Tier 1: Focus Defense (Autónomo)"]
        Tier2["Tier 2: Reschedule Staging (Aprobación 1-Click)"]
        Undo["Snapshot Rollback Engine"]
    end

    subgraph DataLayer ["3. Persistencia Dual"]
        SQLite[("SQLite LocalStore (Fernet Encrypted)")]
        Batch["BatchIngestor (Idempotent MERGE)"]
        Snowflake[("Snowflake LUP_AI_DB (RAW / ANALYTICS / SEMANTIC)")]
    end

    subgraph Interfaces ["4. Interfaces & Scheduler"]
        API["FastAPI REST (/v1/...)"]
        CLI["Typer CLI (lup & lup agent)"]
        Scheduler["LupScheduler (APScheduler Background)"]
    end

    Gemini --> Anon
    Anon --> Guard
    Guard --> SQLite
    AgentCore --> Diag
    Diag --> Tier1
    Diag --> Tier2
    Tier1 --> Actuator
    Tier2 --> Actuator
    Actuator --> Undo
    Actuator --> SQLite
    SQLite --> Batch
    Batch --> Snowflake
    API --> AgentCore
    CLI --> AgentCore
    Scheduler --> AgentCore
```

### Componentes Construidos y Operativos

| Módulo | Archivos Clave | Estado | Capacidades Actuales |
| :--- | :--- | :--- | :--- |
| **Agente de Calendario Autónomo** | `src/lup_ai/agent/calendar_agent.py`<br>`src/lup_ai/actuators/google_calendar.py`<br>`src/lup_ai/actuators/base.py` | **100% Funcional** | • Diagnóstico de *Fragmentation Index* (0.0 a 1.0) y *Deep Work Deficit*.<br>• Tier 1: Reserva autónoma de bloques de foco de 2h en slots vacíos.<br>• Tier 2: Detección de reuniones flexibles colisionadas y estadificación de propuestas.<br>• Reversibilidad inmediata (*Undo*) usando el pre-estado JSON original. |
| **Capa de Almacenamiento Dual** | `src/lup_ai/data/local_store.py`<br>`src/lup_ai/data/batch_ingestor.py`<br>`snowflake/ddl/06_agent_calendar_actions.sql` | **100% Funcional** | • Cifrado simétrico Fernet para credenciales OAuth en SQLite (`oauth_credentials`).<br>• Registro transaccional de auditoría en SQLite (`agent_schedule_actions`).<br>• Sincronización desacoplada por lotes hacia Snowflake `ANALYTICS.FCT_AGENT_SCHEDULE_ACTIONS` y vista semántica `SEMANTIC.VW_AGENT_SCHEDULE_ACTIONS`.<br>• Fallback automático a archivo CSV local si Snowflake no está configurado. |
| **Motor de Decisión e Inferencia** | `src/lup_ai/agent/orchestrator.py`<br>`src/lup_ai/privacy/text_anonymizer.py`<br>`src/lup_ai/data/sql_guard.py` | **100% Funcional** | • Arquitectura *Masked Cloud*: Anonimización de PII antes de enviar prompts a Gemini.<br>• `SQLGuard`: Validación sintáctica de SQL mediante AST contra vistas semánticas autorizadas.<br>• Análisis de correlación de tendencias y escaneo proactivo de anomalías. |
| **Loop de Decisión y Seguimiento** | `src/lup_ai/memory/decision_tracker.py` | **100% Funcional** | • Registro de decisiones aceptadas, pospuestas o completadas.<br>• Evaluación de impacto contrafactual (*adherence & outcome status*: improved / stagnant / deteriorated). |
| **Automatización y Tareas de Fondo** | `src/lup_ai/jobs/scheduler.py`<br>`src/lup_ai/jobs/proactive_scan.py` | **100% Funcional** | • Motor APScheduler en segundo plano configurado para escaneo proactivo, flush por lotes y ciclo agéntico matutino (07:00) y vespertino (18:00). |
| **Interfaces de Usuario** | `src/lup_ai/api/main.py`<br>`src/lup_ai/cli/app.py` | **100% Funcional** | • Endpoints REST completos en FastAPI para consultas, simulaciones y control agéntico.<br>• CLI completo en Typer + Rich con el grupo de comandos `lup agent` (`health`, `run`, `pending`, `approve`, `reject`, `undo`). |

---

## 3. FEATURES FALTANTES PARA EL MVP (GAP ANALYSIS)

Para que el sistema sea entregable a un usuario real en producción como **Producto Mínimo Viable (MVP)**, se identifican **5 brechas técnicas** que deben cerrarse:

```mermaid
flowchart LR
    G1["1. OAuth Dance Interactivo\n(Flujo Web/CLI Login)"] --> G2["2. Canal Push 1-Click\n(Bot Telegram / Slack)"]
    G2 --> G3["3. UI Web Interactiva\n(Dashboard Streamlit)"]
    G3 --> G4["4. Webhook en Tiempo Real\n(GCal Watch API)"]
    G4 --> G5["5. Empaquetado Producción\n(Docker & CI/CD)"]
```

### Brecha 1: Flujo de Autenticación Interactivo de Google Calendar (OAuth Dance)
* **Situación Actual:** El actuador lee tokens de la tabla SQLite `oauth_credentials` cifrada con Fernet o de `credentials.json`. Sin embargo, la captura inicial del token requiere inyectarlo vía API (`POST /v1/agent/oauth/token`) o un script manual.
* **Lo que falta para el MVP:**
  * Comando interactivo CLI: `lup auth login google` que levante un servidor web local efímero (`http://localhost:8085/oauth2callback`), abra el navegador, capture el código de autorización de Google y guarde el token cifrado automáticamente.
  * Endpoint REST de redirección OAuth: `GET /v1/auth/google/authorize` y `GET /v1/auth/google/callback` para que cualquier cliente web pueda iniciar sesión con 1 clic.

### Brecha 2: Canal de Notificación Push para Aprobaciones de Nivel 2 (Human-in-the-Loop)
* **Situación Actual:** Las propuestas de Tier 2 (reagendar reuniones) se almacenan en SQLite con estado `PENDING_APPROVAL` y se visualizan con `lup agent pending` o `GET /v1/agent/actions/pending`. Requiere que el usuario consulte activamente.
* **Lo que falta para el MVP:**
  * **Conector de Notificación Push (p.ej. Bot de Telegram o Slack Webhook):**
    * Cuando el agente detecte una reunión que deba reagendarse, envía un mensaje interactivo al móvil del usuario:
      > *"⚠️ LUP AI: Tu reunión '1-on-1' a las 12:00 rompe tu bloque de concentración de la tarde. Sugiero moverla a las 15:30.*  
      > `[✅ Aprobar]` `[❌ Rechazar]` `[⏳ Posponer]`"
    * Al pulsar `[Aprobar]`, un webhook llama directamente a `POST /v1/agent/actions/{id}/approve` y actualiza Google Calendar en tiempo real.

### Brecha 3: Interfaz Gráfica de Usuario Web (Dashboard Streamlit)
* **Situación Actual:** El sistema cuenta con API REST y CLI de terminal.
* **Lo que falta para el MVP:**
  * Un panel web visual y amigable en Streamlit (`src/lup_ai/ui/dashboard.py`):
    * **Pestaña Agenda Inteligente:** Visualización tipo *timeline* de bloques de foco defendidos vs. reuniones, semáforo del *Fragmentation Index* diario, y lista de propuestas pendientes con botón de aprobación/deshacer.
    * **Pestaña Salud y Finanzas:** Visualización de horas de sueño vs. gasto semanal con correlaciones calculadas por `TrendAnalyzer`.
    * **Pestaña Chat con el Agente:** Interfaz conversacional conectada a `/v1/ask` para consultas en lenguaje natural ("*¿Cómo afectará mi sueño si tomo un vuelo el jueves?*").

### Brecha 4: Escucha de Calendario en Tiempo Real (Push Notifications / Webhooks)
* **Situación Actual:** El agente opera en modo híbrido (disparadores programados a las 07:00/18:00 o bajo demanda vía CLI/API).
* **Lo que falta para el MVP:**
  * Integración con **Google Calendar Watch API (`events().watch`)**:
    * Registro de un canal de notificación push para que, cuando un tercero invite al usuario a una reunión imprevista, Google Calendar notifique al endpoint `POST /v1/agent/calendar/webhook`.
    * El agente reacciona inmediatamente (en segundos), evalúa si hay colisión con un bloque de foco y dispara la alerta antes de que el usuario acepte el conflicto.

### Brecha 5: Empaquetado, Contenedorización y Despliegue
* **Situación Actual:** La ejecución depende de `uv` local en la máquina de desarrollo.
* **Lo que falta para el MVP:**
  * `Dockerfile` multi-stage optimizado (base Python 3.12-slim con `uv`).
  * `docker-compose.yml` que orqueste:
    1. Contenedor de la API FastAPI + Scheduler.
    2. Contenedor de la UI Streamlit.
    3. Volumen persistente para SQLite (`/data/lup.db`).
  * Archivo `.env.example` consolidado y documentado con variables para Snowflake, Gemini, Google OAuth y Telegram.

---

## 4. FUTURAS MEJORAS Y ROADMAP POST-MVP (LUP AI 2.0+)

Una vez validado el MVP con usuarios reales, la plataforma puede evolucionar hacia un ecosistema de toma de decisiones autónoma integral:

### 1. Negociación Activa de Agenda por Lenguaje Natural
* **Concepto:** Cuando alguien solicita una reunión en horario protegido de Deep Work, el agente no solo alerta al usuario, sino que puede generar automáticamente un borrador de respuesta por correo o mensaje de Slack:
  > *"Hola Carlos, Sebastián tiene reservado ese bloque para trabajo profundo de alta concentración. Sus espacios libres disponibles son hoy a las 16:00 o mañana a las 10:30. ¿Te sirve alguno?"*
* **Impacto:** Elimina la fricción humana de coordinar horarios y protege proactivamente el tiempo del usuario.

### 2. Soporte Multi-Calendario y Multi-Cuenta (Personal + Corporativo)
* **Concepto:** Sincronización cruzada de múltiples calendarios (Google Calendar de trabajo, Google Calendar personal, Microsoft Outlook/Office 365).
* **Mecanismo:** Un bloque personal (e.g. cita médica o gimnasio) bloquea de forma anónima ("Ocupado / Focus") la agenda corporativa sin revelar detalles privados al equipo de trabajo.

### 3. Expansión de la Bounded Agency a Finanzas y Hábitos
* Extender el modelo de dos niveles a los otros dos dominios del sistema:
  * **Finanzas Autónomas (Tier 1 & Tier 2):**
    * *Tier 1 (Autónomo):* Congelar categorías de gasto discrecional en la app bancaria cuando se alcance el 90% del presupuesto semanal.
    * *Tier 2 (Aprobación 1-Click):* Transferencias automáticas hacia fondos de micro-ahorro tras semanas con superávit de gasto.
  * **Sueño y Ritmo Circadiano:**
    * *Tier 1 (Autónomo):* Activar modo "No Molestar" o atenuar luces inteligentes (IoT) 45 minutos antes de la hora óptima calculada de sueño.

### 4. Aprendizaje de Preferencias Implícitas (Adaptive Policy Engine)
* **Concepto:** Un modelo de aprendizaje por refuerzo contextual (Contextual Bandits o fine-tuning ligero de preferencias).
* **Mecanismo:** Si el usuario rechaza 3 veces consecutivas mover las reuniones de los viernes por la tarde, el agente aprende esa restricción blanda automáticamente sin que el usuario tenga que programarla manualmente en la configuración.

### 5. Arquitectura Multi-Agente / Swarm Especializado
* Reorganizar los dominios en agentes especialistas independientes coordinados:
  * `CalendarAgent` (defensa de agenda y ritmo circadiano).
  * `FinancialAgent` (optimización de liquidez y presupuesto).
  * `WellnessAgent` (hábitos, ejercicio y recuperación cognitiva).
  * `MasterOrchestrator` (resuelve compensaciones entre dominios, e.g., no recortar presupuesto de alimentación saludable para ahorrar dinero).

---

## 5. TABLA DE PRIORIZACIÓN DE DESARROLLO (MÉTODO MoSCoW)

Para planificar los siguientes sprints hacia el MVP, se establece la siguiente matriz de prioridades:

| Prioridad | Feature / Tarea | Complejidad | Impacto | Justificación Técnica |
| :--- | :--- | :---: | :---: | :--- |
| **MUST HAVE** (Crítico MVP) | **Flujo Interactivo OAuth 2.0 (CLI + Web)** | Baja (1-2 días) | Muy Alto | Sin esto, incorporar nuevos usuarios o configurar cuentas requiere manipulación manual de base de datos. |
| **MUST HAVE** (Crítico MVP) | **Canal Push de Aprobación 1-Click (Telegram/Slack)** | Media (2-3 días) | Muy Alto | Da sentido práctico a la autonomía acotada (Tier 2); el usuario debe poder aprobar en 2 segundos desde su teléfono. |
| **MUST HAVE** (Crítico MVP) | **Dashboard Web Streamlit Integrado** | Media (3-4 días) | Alto | Hace tangible la propuesta de valor para usuarios que no usan la terminal. |
| **SHOULD HAVE** (Deseable MVP) | **Google Calendar Webhook / Watch API** | Media (2-3 días) | Alto | Permite reaccionar en tiempo real a invitaciones entrantes sin esperar al próximo escaneo programado. |
| **SHOULD HAVE** (Deseable MVP) | **Dockerfile & Docker Compose** | Baja (1 día) | Medio | Garantiza portabilidad y simplifica el despliegue en cualquier servidor cloud. |
| **COULD HAVE** (Post-MVP) | **Borradores de Negociación por Correo/Slack** | Media (3-4 días) | Medio | Automatiza la respuesta de contrapropuestas horarias. |
| **COULD HAVE** (Post-MVP) | **Soporte Multi-Calendario (GCal + Outlook)** | Alta (5-7 días) | Alto | Relevante para profesionales que tienen cuentas separadas corporativa y personal. |
| **WON'T HAVE** (Futuro v2.0) | **Acciones de ejecución financiera bancaria real** | Muy Alta | Crítico | Requiere certificaciones bancarias PSD2/OpenBanking y cumplimiento normativo estricto. |

---

## 6. CONCLUSIONES Y RECOMENDACIÓN DEL TECH LEAD

1. **Estado de Ingeniería Excepcional:** La base de código de `master/martes10nov` está limpia, robusta y 100% testeada. No hay deuda técnica oculta ni dependencias rotas.
2. **El Agente ya es una Realidad:** El sistema dejó de ser un simple bot conversacional; cuenta con un bucle completo de **percepción, diagnóstico, toma de decisiones acotada, mutación real en calendario y compensación (undo)**.
3. **Ruta Crítica hacia el MVP:** Para tener un producto listo para usuarios finales beta, solo se necesita construir la capa de interacción (**Login OAuth interactivo + Notificaciones Push en Telegram + Dashboard Streamlit**). Todo el motor agéntico, matemático y de base de datos ya está operando y verificado.
