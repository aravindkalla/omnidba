# OmniDBA v3 — GCP Privileged Setup (hand this to a project admin)

**Goal:** stand up the GCP-side resources OmniDBA v3 needs, and hand back **one
service-account key file** so the app can authenticate with least privilege — no
interactive login on the VM, no VM downtime.

**Who runs this:** a person with **Owner** or **Editor** (or the specific admin
roles listed in §0) on project `in-26301-bell-poc`.

**What you hand back:** the generated `omnidba-sa.json` (delivered securely — see §4).

---

## 0. What permissions the admin needs

Owner/Editor covers all of it. If using granular roles, you need:

| Action | Role |
|---|---|
| Enable APIs | `roles/serviceusage.serviceUsageAdmin` |
| Create SA + key | `roles/iam.serviceAccountAdmin`, `roles/iam.serviceAccountKeyAdmin` |
| Grant IAM roles | `roles/resourcemanager.projectIamAdmin` |
| Create BigQuery dataset/table | `roles/bigquery.admin` |
| Create Cloud Tasks queue | `roles/cloudtasks.admin` |

---

## Option A — One gcloud script (recommended)

Run in Cloud Shell or any machine with `gcloud`/`bq` authenticated as the admin.
Copy-paste the whole block.

```bash
set -euo pipefail

# ---- fixed project values (match /opt/omnidba-gcp/.env) ----
export PROJECT=in-26301-bell-poc
export REGION=asia-south1            # BigQuery + Cloud Tasks resource location
export VERTEX_LOCATION=us-central1   # Gemini 2.5 serving region (APIs are global; FYI)
export SA_NAME=omnidba-sa
export SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
export BQ_DATASET=omnidba_audit
export HITL_QUEUE=omnidba-hitl
export KEY_OUT=omnidba-sa.json

gcloud config set project "$PROJECT"

# 1) Enable APIs
gcloud services enable \
  aiplatform.googleapis.com \
  cloudtasks.googleapis.com \
  bigquery.googleapis.com \
  logging.googleapis.com \
  secretmanager.googleapis.com
# Optional prompt-injection guard (skip if the API isn't available in the org):
gcloud services enable modelarmor.googleapis.com || echo "modelarmor optional — skipped"

# 2) Create the runtime service account (idempotent)
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="OmniDBA v3 runtime" || echo "SA already exists — continuing"

# 3) Grant least-privilege RUNTIME roles
for role in roles/aiplatform.user \
            roles/bigquery.dataEditor \
            roles/bigquery.jobUser \
            roles/cloudtasks.enqueuer \
            roles/logging.logWriter \
            roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="$role" --condition=None
done

# 4) BigQuery audit sink: dataset + `turns` table (one row per NL->SQL turn)
bq --location="$REGION" mk --dataset "${PROJECT}:${BQ_DATASET}" || echo "dataset exists"
bq mk --table "${PROJECT}:${BQ_DATASET}.turns" \
  'ts:TIMESTAMP,user:STRING,engine:STRING,provider:STRING,intent:STRING,query:STRING,generated_sql:STRING,row_count:INTEGER,latency_ms:INTEGER,error:BOOL,error_msg:STRING' \
  || echo "table exists"

# 5) Cloud Tasks queue backing the RMAN human-in-the-loop approval flow
gcloud tasks queues create "$HITL_QUEUE" --location="$REGION" || echo "queue exists"

# 6) Generate the runtime SA key — THIS is the file to hand back
gcloud iam service-accounts keys create "$KEY_OUT" --iam-account="$SA"
echo
echo ">>> Deliver '$KEY_OUT' securely to the OmniDBA VM at:"
echo ">>>   /opt/omnidba-gcp/omnidba-sa.json"
```

### Verify (admin, before handing back)

```bash
gcloud services list --enabled --project="$PROJECT" \
  | grep -E 'aiplatform|cloudtasks|bigquery|logging|secretmanager'
bq ls "${PROJECT}:${BQ_DATASET}"
gcloud tasks queues describe "$HITL_QUEUE" --location="$REGION" --format='value(name,state)'
```

---

## Option B — Console click-path (no CLI)

1. **Enable APIs** — Console → *APIs & Services → Enable APIs & Services*, enable each:
   Vertex AI API, Cloud Tasks API, BigQuery API, Cloud Logging API,
   Secret Manager API (and optionally Model Armor API).
2. **Service account** — *IAM & Admin → Service Accounts → Create*.
   Name `omnidba-sa`, display "OmniDBA v3 runtime". Create.
3. **Grant roles** — *IAM & Admin → IAM → Grant Access*, principal
   `omnidba-sa@in-26301-bell-poc.iam.gserviceaccount.com`, add roles:
   Vertex AI User, BigQuery Data Editor, BigQuery Job User,
   Cloud Tasks Enqueuer, Logs Writer, Secret Manager Secret Accessor.
4. **BigQuery** — *BigQuery* → project `in-26301-bell-poc` → *Create dataset*
   `omnidba_audit`, location `asia-south1`. Then *Create table* `turns` with schema:
   `ts:TIMESTAMP, user:STRING, engine:STRING, provider:STRING, intent:STRING,
   query:STRING, generated_sql:STRING, row_count:INTEGER, latency_ms:INTEGER,
   error:BOOL, error_msg:STRING`.
5. **Cloud Tasks** — *Cloud Tasks → Create queue*, name `omnidba-hitl`,
   region `asia-south1`.
6. **Key** — back in *Service Accounts → omnidba-sa → Keys → Add key → Create new
   key → JSON*. Download it, rename to `omnidba-sa.json`, deliver to the VM.

---

## 1. Fixed values (do not change — the app reads these)

| Setting | Value |
|---|---|
| Project | `in-26301-bell-poc` |
| Runtime SA | `omnidba-sa@in-26301-bell-poc.iam.gserviceaccount.com` |
| BQ dataset.table | `omnidba_audit.turns` (location `asia-south1`) |
| Cloud Tasks queue | `omnidba-hitl` (region `asia-south1`) |
| Vertex serving region | `us-central1` (Gemini 2.5 Flash + Pro) |

---

## 2. Roles granted and why (least privilege)

| Role | Why |
|---|---|
| `roles/aiplatform.user` | Call Gemini 2.5 (routing + SQL) via Vertex AI |
| `roles/bigquery.dataEditor` | Insert audit rows into `omnidba_audit.turns` |
| `roles/bigquery.jobUser` | Run insert/query jobs in the project |
| `roles/cloudtasks.enqueuer` | Enqueue RMAN approval tasks (HITL) |
| `roles/logging.logWriter` | Structured audit logs to Cloud Logging |
| `roles/secretmanager.secretAccessor` | Read DB creds from Secret Manager (future) |

The runtime SA deliberately **cannot** create datasets, queues, or enable APIs —
that's all done once, by the admin, above.

---

## 3. If your org blocks SA key creation

Some orgs enforce `iam.disableServiceAccountKeyCreation`. If §A step 6 fails,
skip the key and instead grant the **same six runtime roles to Aravind's user
account** (`engg.aravindkalla@gmail.com` or the project login), plus
`roles/serviceusage.serviceUsageConsumer`. He'll then authenticate on the VM with
user ADC (`gcloud auth application-default login`) — no key file needed.

---

## 4. Secure delivery of the key

Do **not** email or paste the JSON in chat. Prefer one of:
- Cloud Storage signed URL (short TTL), or
- `gcloud compute scp omnidba-sa.json omnidba-vm:/opt/omnidba-gcp/omnidba-sa.json`, or
- a secrets manager / password vault the team already uses.

The file grants the six roles above until the key is deleted — rotate/delete it
after the hackathon (`gcloud iam service-accounts keys delete <KEY_ID> --iam-account=$SA`).
