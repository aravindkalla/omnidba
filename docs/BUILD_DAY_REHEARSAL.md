# OmniDBA — Build-Day Rehearsal Checklist

**A time-boxed playbook to rebuild OmniDBA LIVE on a fresh VM — compliant with the
hackathon rules.** Rehearse this on a scratch VM before the event so the real run
is muscle memory.

> ## ⚖️ Compliance (organizer-clarified 2026-07-17)
> - **Rebuild live using your knowledge + these design runbooks.** ✅ allowed.
> - **Do NOT `git clone` the repo or `scp`/copy source files** from GitHub, your
>   laptop, or another VM. That is disqualification.
> - Author each file **on the event VM**, guided by the design here + the deep-dive
>   runbooks (`HACKATHON_RUNBOOK.md`, `BUILD_FROM_SCRATCH.md`). Runbooks = reference.
> - Vertex AI Gemini + Model Armor are **in scope**. GCP service account is created
>   **during** the event. Do not try to smuggle files (encoded/clipboard) — same risk.

---

## Strategy: MVP-first, then layer

Get something **demoable early**, then add differentiators. If you run out of time,
you still have a working demo. Order of value:

1. **Core loop** (Ollama + Oracle, one diagnostic query end-to-end) → demoable.
2. **Omni-channel** (add the LLM + DB seams → Vertex + Postgres toggle).
3. **Governance** (BigQuery audit) + **Security** (Model Armor guard).
4. **HITL** (RMAN backup approval) + **UI polish** (Governance page, badges).

---

## Phase 0 — Access & OS  ·  ~15 min
- [ ] SSH into the provisioned VM; confirm `sudo`.
- [ ] `sudo apt-get update` and install `python3.12 python3.12-venv python3-pip git curl`.
- [ ] Install Docker (`curl -fsSL https://get.docker.com | sudo sh`; `usermod -aG docker $USER`; re-login).
- [ ] (Optional, air-gapped path) install Ollama + `ollama pull llama3.1`.
- [ ] Confirm the VM can reach GCP (metadata) and pull images.

## Phase 1 — Databases (start Oracle FIRST, it's slow) · ~20 min (mostly waiting)
- [ ] `docker run -d --name oracle-26ai --restart unless-stopped -p 1521:1521 -e ORACLE_PASSWORD=password -v oracle-data:/opt/oracle/oradata container-registry.oracle.com/database/free:latest`
- [ ] `docker run -d --name omnidba-pg --restart unless-stopped -p 5433:5432 -e POSTGRES_DB=omnidba -e POSTGRES_USER=omnidba -e POSTGRES_PASSWORD=<pw> postgres:16 -c shared_preload_libraries=pg_stat_statements`
- [ ] While Oracle inits (`docker logs -f oracle-26ai` → "DATABASE IS READY"): create the `admin` user + grants, `CREATE EXTENSION pg_stat_statements` in PG. (See BUILD_FROM_SCRATCH §5–6.)

## Phase 2 — Python env · ~10 min
- [ ] `python3.12 -m venv .venv-gcp && .venv-gcp/bin/pip install -U pip`
- [ ] Author `requirements-gcp.txt` (langgraph, langchain-core, langchain-ollama, langchain-google-vertexai, google-cloud-{aiplatform,tasks,bigquery,logging,secret-manager}, vanna, chromadb, oracledb, psycopg[binary], streamlit, fastapi, uvicorn, python-jose[cryptography], python-dotenv) → `pip install -r`.

## Phase 3 — GCP setup · ~15 min
- [ ] Auth: `gcloud auth application-default login` (Editor acct) + `set-quota-project`; OR create the `omnidba-sa` service account now (ADMIN_GCP_SETUP.md).
- [ ] Enable APIs: aiplatform, cloudtasks, bigquery, logging, secretmanager, modelarmor.
- [ ] BigQuery: dataset `omnidba_audit` + table `turns` (11-col schema).
- [ ] Cloud Tasks: queue `omnidba-hitl` (asia-south1).
- [ ] Model Armor: template `omnidba-guard` (us-central1) — pi_and_jailbreak + sdp + malicious_uris.
- [ ] Author `.env` (see BUILD_FROM_SCRATCH §7 — incl. `RMAN_DOCKER_CONTAINER=oracle-26ai`).

## Phase 4 — Author the code (the bulk) · ~2–4 hrs
Build in dependency order. Each is small and single-purpose — you know the design.

1. **`llm_provider.py`** — `get_chat_llm(provider=, json_mode=, temperature=)` → ChatOllama | ChatVertexAI (model=Flash if json_mode else Pro; `location=VERTEX_LOCATION` us-central1). `active_provider()`.
2. **`db_adapter.py`** — `DBAdapter` ABC (`connect`, `templates`, `schema_context`, `error_types`, `normalize_row`, `curated_queries`, `supports_backup`) + `OracleAdapter`, `PostgresAdapter`; `get_adapter(engine=)`.
3. **`templates_oracle.py` / `templates_postgres.py`** — `TEMPLATES = {key: (sql, keyword_groups)}` + `CURATED_QUERIES`. Keys: tablespace_usage, top_sql_elapsed, blocking_sessions, invalid_objects, redo_log_status, active_sessions, index_status, db_users, db_list, fra_usage (Oracle also rman_backup_history). ⚠️ rman_backup_history must REQUIRE a backup/rman keyword; keep user/users OUT of active_sessions.
4. **`diagnostic_agent.py`** — cached `_get_resources(engine)` (adapter/conn/templates/Vanna) + `_get_correction_chain(provider,engine)`; `_match_template`; `run_diagnostic_query(q, provider=, engine=)` 3-tier (template → Vanna → self-correct). ⚠️ guard `cursor.description is None`. Keep module `connection` for api.py.
5. **`gcp_audit.py`** — `log_turn(...)` → BigQuery (async threadpool, best-effort); `enqueue_hitl_approval(...)` → Cloud Tasks; no-op when off/no project.
6. **`model_armor.py`** — `screen_prompt()` / `sanitize_response()` via ADC AuthorizedSession; `MODEL_ARMOR_MODE` enforce|monitor|off; recursive matchState scan + PII infoTypes; fail-open.
7. **`orchestrator.py`** — `build_app(provider, engine)` cached per pair: guard node (Model Armor, skip on ollama) → router (Gemini Flash/Ollama JSON) → diagnostic / backup. backup_node guards `engine != oracle`; enqueues HITL; `interrupt()`. Keep module `app`.
8. **`rman_agent.py`** — parse → `RMANParams`; `generate_rman_script`; `execute_rman_backup` via `docker exec -i $RMAN_DOCKER_CONTAINER rman target /`.
9. **`app.py`** — Streamlit: sidebar LLM+DB selectors (build_app), header badge, chat, diagnostic renderer (unique element keys via a counter — NOT id(df)), HITL panel, Governance page (reads BigQuery), Architecture tab (index.html + flow HTML). `api.py` optional.

## Phase 5 — Seed, smoke-test, run · ~30 min
- [ ] `train_schema('oracle')` / `('postgres')` to seed Chroma (or lazy).
- [ ] Smoke test per HACKATHON_RUNBOOK §9 / BUILD_FROM_SCRATCH §9: imports, Oracle connect, all 4 provider×engine combos return rows, injection blocked, PII blocked, audit row in BQ.
- [ ] `streamlit run app.py --server.port 8610` (author `startup-gcp.sh` if time).
- [ ] Install the archivelog cleanup cron (prevents ORA-00257).

## Phase 6 — Demo dry-run · ~15 min
Run the 8-step demo script (HACKATHON_RUNBOOK §9): tablespace → who's connected → injection blocked → PII blocked → flip to Ollama (injection passes) → flip to Postgres → RMAN HITL → Governance page.

---

## Time budget (≈6 hr day)
| Phase | Est |
|---|---|
| 0 Access & OS | 15m |
| 1 Databases | 20m (Oracle inits in background) |
| 2 Python env | 10m |
| 3 GCP setup | 15m |
| 4 Author code | 2.5–4h ← the real work |
| 5 Seed + smoke | 30m |
| 6 Demo dry-run | 15m |
| Buffer | 45m+ |

## Gotchas to pre-empt (from `HACKATHON_RUNBOOK.md §12`)
- VM scope / ADC credential-split → drive admin via ADC token if gcloud CLI is scope-capped.
- Vertex region = us-central1 (not asia-south1) or 404.
- `RMAN_DOCKER_CONTAINER` set, else backups no-op.
- `ORACLE_PASSWORD` not blank; containers `--restart unless-stopped`.
- Unique Streamlit keys (counter, not id(df)); guard `cursor.description is None`.

## If time runs short — cut in this order (keep the pitch intact)
1. Skip `api.py` (Streamlit drives the graph directly).
2. Governance page → show BigQuery console instead.
3. HITL live-exec → show the approval panel + script (don't run a full backup).
4. Keep: omni-channel toggle + Model Armor block + one audited query. That IS the pitch.
