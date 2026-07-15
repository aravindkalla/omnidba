# OmniDBA v3 — Build From Scratch on a Fresh VM (Hackathon Runbook)

End-to-end, copy-pasteable steps to stand up OmniDBA v3 (GCP/Vertex, multi-DB) on a
brand-new GCP VM. Written so a teammate can reproduce the full stack — Oracle + Postgres
diagnostics, LangGraph orchestrator, Ollama (air-gapped) **and** Vertex/Gemini paths — with
no tribal knowledge.

> Companion doc: the original design rationale lives in
> `/opt/oracle-dba-agent/docs/GCP_REBUILD_RUNBOOK.md`. This doc is the *operational*
> build sequence. Where they disagree, this doc wins (it reflects the actually-built stack).

---

## 0. Architecture at a glance

| Layer | Choice | Notes |
|---|---|---|
| Orchestrator | LangGraph `StateGraph` (`orchestrator.py`) | router → diagnostic / backup nodes |
| LLM seam | `llm_provider.get_chat_llm()` | `LLM_PROVIDER=ollama` (local/air-gapped) or `vertex` (Gemini 2.5) |
| DB seam | `db_adapter.get_adapter()` | `DB_ENGINE=oracle` (full + RMAN) or `postgres` (diag-only) |
| NL2SQL | 3-tier: template → Vanna/Chroma RAG → LLM self-correct | per-engine templates in `templates_oracle.py` / `templates_postgres.py` |
| HITL | LangGraph `interrupt()` for RMAN backups | Oracle write path only |

**Ports (must not collide with v1's 8000/8501/11434/443/1521):**

| Service | Port |
|---|---|
| v3 agent API (FastAPI) | 8600 |
| v3 demo UI (Streamlit) | 8610 |
| Oracle listener | 1521 |
| Postgres | 5433 (host) → 5432 (container) |
| Ollama (if local LLM) | 11434 |

---

## 1. Provision the VM  ⚠️ scopes matter

Create the VM **with the `cloud-platform` access scope** and (ideally) a dedicated service
account. This is the single biggest gotcha: a default-scope VM's metadata credentials
**cannot call Vertex AI** (`Request had insufficient authentication scopes`), and you can
only change scopes while the VM is *stopped*.

```bash
# Recommended: dedicated SA (least privilege) — create once per project
gcloud iam service-accounts create omnidba-sa \
  --display-name="OmniDBA runtime"

PROJECT=in-26301-bell-poc
SA="omnidba-sa@${PROJECT}.iam.gserviceaccount.com"
for role in roles/aiplatform.user roles/bigquery.dataEditor \
            roles/cloudtasks.enqueuer roles/logging.logWriter \
            roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="$role"
done

# Create the VM bound to that SA WITH cloud-platform scope
gcloud compute instances create omnidba-vm \
  --zone=asia-south1-b \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=100GB \
  --service-account="${SA}" \
  --scopes=cloud-platform
```

> If you inherit an existing default-scope VM instead (like the current one), you cannot
> change scopes without stopping it. The zero-downtime fallback is **user ADC**:
> `gcloud auth login` + `gcloud auth application-default login` with an Owner account.
> That works for both admin and runtime Vertex calls. See §7.

**Firewall:** open 8600/8610 to your demo source only (don't expose Oracle 1521 / PG 5433 publicly).

---

## 2. OS-level dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3-pip git curl

# Docker (for Oracle + Postgres containers)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"        # log out/in so `docker` works without sudo

# (Optional, air-gapped LLM path) Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1
```

---

## 3. Get the code

The current build lives as a **git worktree** so it can't touch v1:

```bash
# On the original VM this was: git worktree add /opt/omnidba-gcp gcp-vertex
# On a fresh VM, just clone the branch:
sudo mkdir -p /opt/omnidba-gcp && sudo chown "$USER" /opt/omnidba-gcp
git clone -b gcp-vertex <repo-url> /opt/omnidba-gcp
cd /opt/omnidba-gcp
```

---

## 4. Python virtualenv (isolated — never share v1's `.venv`)

```bash
cd /opt/omnidba-gcp
python3.12 -m venv .venv-gcp
.venv-gcp/bin/pip install --upgrade pip
.venv-gcp/bin/pip install -r requirements-gcp.txt
```

`requirements-gcp.txt` pulls: langgraph, langchain-core, langchain-ollama,
langchain-google-vertexai, google-cloud-{aiplatform,tasks,logging,bigquery,secret-manager},
vanna, chromadb, oracledb, psycopg[binary], streamlit, fastapi, uvicorn, python-jose,
python-dotenv. (Python 3.12.)

---

## 5. Oracle container (full path: diagnostics + RMAN)

```bash
docker run -d --name oracle-26ai \
  --restart unless-stopped \
  -p 1521:1521 \
  -e ORACLE_PASSWORD=<sys-pwd> \
  -v oracle-data:/opt/oracle/oradata \
  container-registry.oracle.com/database/free:latest
# wait until healthy: docker logs -f oracle-26ai  → "DATABASE IS READY TO USE!"

# App user the agent connects as (admin/password by default in diagnostic_agent.py):
docker exec -i oracle-26ai sqlplus / as sysdba <<'SQL'
ALTER SESSION SET CONTAINER=FREEPDB1;
CREATE USER admin IDENTIFIED BY password;
GRANT CONNECT, RESOURCE, SELECT ANY DICTIONARY, SELECT_CATALOG_ROLE TO admin;
SQL
```

> ⚠️ **Archive-log growth.** The image runs in ARCHIVELOG mode; logs accumulate in
> `/opt/oracle/oradata/dbconfig/FREE/dbs` and *will* fill the disk (ORA-00257, archiver
> stuck, all non-SYSDBA logins blocked). Install the cleanup cron from §10 immediately.

---

## 6. Postgres container (diagnostics-only, multi-DB proof)

```bash
docker run -d --name omnidba-pg \
  --restart unless-stopped \
  -p 5433:5432 \
  -e POSTGRES_DB=omnidba \
  -e POSTGRES_USER=omnidba \
  -e POSTGRES_PASSWORD=<pg-pwd> \
  -v omnidba-pg-data:/var/lib/postgresql/data \
  postgres:16 \
  -c shared_preload_libraries=pg_stat_statements

# Enable the extension the top-SQL template needs (system views cover the rest):
docker exec -i omnidba-pg psql -U omnidba -d omnidba <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SQL
```

> No user-table seed is required — the Postgres templates read system views
> (`pg_stat_activity`, `pg_stat_statements`, tablespace catalogs).

---

## 7. Configure `.env`

Create `/opt/omnidba-gcp/.env` (gitignored). Template:

```ini
# provider switches
LLM_PROVIDER=ollama          # ollama (air-gapped) | vertex (Gemini)
DB_ENGINE=oracle             # oracle | postgres

# GCP
GCP_PROJECT=in-26301-bell-poc
GCP_REGION=asia-south1
VERTEX_LOCATION=us-central1  # Gemini 2.5 serving region
GEMINI_ROUTER_MODEL=gemini-2.5-flash
GEMINI_SQL_MODEL=gemini-2.5-pro
BQ_AUDIT_DATASET=omnidba_audit
HITL_QUEUE=omnidba-hitl
MODEL_ARMOR_TEMPLATE=omnidba-guard

# Oracle  (admin/password = diagnostic_agent.py defaults; DO NOT leave PASSWORD blank → ORA-01005)
ORACLE_USER=admin
ORACLE_PASSWORD=password
ORACLE_DSN=localhost:1521/FREEPDB1
RMAN_DSN=localhost:1521/FREE

# Postgres
PG_HOST=localhost
PG_PORT=5433
PG_DB=omnidba
PG_USER=omnidba
PG_PASSWORD=<pg-pwd>

# ports + chroma stores
API_PORT=8600
APP_PORT=8610
CHROMA_PATH=/opt/omnidba-gcp/chroma_db_gcp
CHROMA_PATH_PG=/opt/omnidba-gcp/chroma_db_pg

# Ollama
OLLAMA_MODEL=llama3.1
OLLAMA_HOST=http://localhost:11434
```

**Authenticate for GCP** (pick one, per the VM's scope situation):

```bash
# A) Proper: VM created with a dedicated SA + cloud-platform scope → nothing to do,
#    metadata ADC just works.

# B) SA key file (portable):
export GOOGLE_APPLICATION_CREDENTIALS=/opt/omnidba-gcp/omnidba-sa.json

# C) Zero-downtime fallback on a default-scope VM: user ADC (what this build used)
gcloud auth application-default login --no-launch-browser # runtime ADC
gcloud auth application-default set-quota-project $GCP_PROJECT
```

> **⚠️ Credential-split gotcha (learned this build).** `application-default login`
> writes **ADC**, used by *client libraries* (Vertex, BigQuery, Cloud Tasks Python
> SDKs). It does NOT change the **gcloud/bq CLI active account**, which on a
> default-scope VM stays the scope-capped compute SA — so `gcloud services enable`,
> `bq mk`, `gcloud tasks queues create` still fail with
> `ACCESS_TOKEN_SCOPE_INSUFFICIENT`. Two ways to run the §8 admin steps as your
> full-scope ADC identity instead:
>
> ```bash
> # Option 1 — also log the CLI in as your user (simplest):
> gcloud auth login --no-launch-browser
>
> # Option 2 — drive the admin APIs with an ADC-minted token over REST
> # (no CLI login; what this build did). Example — enable an API:
> TOKEN=$(gcloud auth application-default print-access-token)
> curl -X POST -H "Authorization: Bearer $TOKEN" \
>   "https://serviceusage.googleapis.com/v1/projects/$GCP_PROJECT/services/cloudtasks.googleapis.com:enable"
> ```
>
> A project **Editor** account (e.g. `aravind.kalla@cgi.com`) has enough rights for
> every §8 step via ADC — no dedicated SA/key required for the demo.

---

## 8. Enable GCP APIs + create audit/HITL infra

```bash
gcloud config set project "$GCP_PROJECT"

gcloud services enable \
  aiplatform.googleapis.com \
  cloudtasks.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  secretmanager.googleapis.com \
  modelarmor.googleapis.com

# BigQuery audit sink (every NL→SQL turn logged for governance)
bq --location=$GCP_REGION mk --dataset "${GCP_PROJECT}:${BQ_AUDIT_DATASET}"
bq mk --table "${GCP_PROJECT}:${BQ_AUDIT_DATASET}.turns" \
  ts:TIMESTAMP,user:STRING,engine:STRING,query:STRING,generated_sql:STRING,row_count:INTEGER,error:BOOL

# Cloud Tasks queue backing the RMAN HITL approval flow
gcloud tasks queues create "$HITL_QUEUE" --location="$GCP_REGION"
```

> Model Armor template (`$MODEL_ARMOR_TEMPLATE`) guards prompt-injection on the NL input.
> Create via Console or `gcloud model-armor templates create` once the API is enabled;
> it is optional for the core demo.

---

## 9. Smoke tests (run in order — each gates the next)

All commands assume `set -a && . ./.env && set +a` first.

```bash
cd /opt/omnidba-gcp && set -a && . ./.env && set +a

# (1) imports/wiring + live Oracle connect
.venv-gcp/bin/python - <<'PY'
import llm_provider, db_adapter, diagnostic_agent as d, orchestrator as o
print("LLM:", llm_provider.get_chat_llm().__class__.__name__)
cur=d.connection.cursor(); cur.execute("select user from dual"); print("oracle:", cur.fetchone()[0])
print("templates:", len(d._TEMPLATES), "| router:", o._router_llm.__class__.__name__)
PY

# (2) Postgres adapter + templates
DB_ENGINE=postgres .venv-gcp/bin/python - <<'PY'
import db_adapter; a=db_adapter.get_adapter(); t=a.templates(); c=a.connect().cursor()
for k in t: c.execute(t[k][0]); print(k, "rows", len(c.fetchall()))
PY

# (3) end-to-end graph, Oracle
.venv-gcp/bin/python - <<'PY'
from orchestrator import app
out=app.invoke({"query":"Who is connected to the database right now?"},{"configurable":{"thread_id":"s1"}})
print(out["final_result"], "| err:", out["query_error"])
PY

# (4) same question on Postgres (multi-DB proof, zero code change)
DB_ENGINE=postgres .venv-gcp/bin/python - <<'PY'
from orchestrator import app
out=app.invoke({"query":"Who is connected to the database right now?"},{"configurable":{"thread_id":"pg1"}})
print(out["final_result"], "| sql:", out["generated_sql"][:60])
PY

# (5) Vertex path — flip provider, re-run (3)
LLM_PROVIDER=vertex .venv-gcp/bin/python - <<'PY'
import llm_provider; print("provider:", llm_provider.get_chat_llm().__class__.__name__)
from orchestrator import app
out=app.invoke({"query":"Show tablespace usage"},{"configurable":{"thread_id":"vx1"}})
print(out["final_result"], "| err:", out["query_error"])
PY
```

Expected: (1) `oracle: ADMIN`; (2) each template returns rows; (3) `Returned N row(s)`;
(4) Postgres `pg_stat_activity` SQL; (5) Vertex client `ChatVertexAI`, rows returned.

---

## 10. Operational: archive-log cleanup cron (prevents ORA-00257)

Script `ops/archivelog_cleanup.sh` (RMAN crosscheck + delete logs older than
`RETENTION_DAYS`, default 1; re-enables the archive dest; logs to `ops/logs/`). Install:

```bash
chmod +x /opt/omnidba-gcp/ops/archivelog_cleanup.sh
( crontab -l 2>/dev/null; \
  echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"; \
  echo "30 2 * * * /opt/omnidba-gcp/ops/archivelog_cleanup.sh >/dev/null 2>&1" ) | crontab -
systemctl is-active cron   # must be 'active'
```

---

## 11. Run the app

```bash
cd /opt/omnidba-gcp && set -a && . ./.env && set +a
.venv-gcp/bin/uvicorn api:app --host 0.0.0.0 --port 8600 &
.venv-gcp/bin/streamlit run app.py --server.port 8610 --server.address 0.0.0.0 &
```

Demo toggles: flip `LLM_PROVIDER` (ollama↔vertex) and `DB_ENGINE` (oracle↔postgres) in
`.env`, restart — same graph, same questions, different backend. That is the pitch.

---

## 12. Gotchas checklist (learned the hard way)

- [ ] VM has `cloud-platform` scope **or** you're using user/SA-key ADC — else Vertex 403s.
- [ ] `ORACLE_PASSWORD` is **not blank** in `.env` (blank → ORA-01005 null password).
- [ ] Archive-log cron installed (§10) — Oracle wedges at ~disk-full otherwise.
- [ ] Containers use `--restart unless-stopped` (default build had `no` → dead after reboot).
- [ ] v3 ports (8600/8610/5433) never collide with v1 (8000/8501/1521/11434/443).
- [ ] `application-default login` quota project set to `$GCP_PROJECT` (Vertex needs it).
</content>
</invoke>
