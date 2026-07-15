"""
LangGraph Orchestrator — central state machine for the Oracle DBA multi-agent system.

Nodes
-----
  router      : LLM-based intent classifier (diagnostic vs backup)
  diagnostic  : NL2SQL query execution (3-tier pipeline in diagnostic_agent)
  backup      : RMAN script generation + HITL interrupt for human approval

LLM backend
-----------
  All LLM construction routes through llm_provider.get_chat_llm(), so the SAME
  graph runs on Ollama (LLM_PROVIDER=ollama, local/air-gapped) or Vertex/Gemini
  (LLM_PROVIDER=vertex). The router uses json_mode → Gemini Flash on Vertex.

Checkpointer
------------
  Development  : MemorySaver (in-process, resets on restart)
  Production   : swap to PostgresSaver from langgraph-checkpoint-postgres
                 and replace MemorySaver() with:
                     from langgraph.checkpoint.postgres import PostgresSaver
                     checkpointer = PostgresSaver.from_conn_string(os.getenv("PG_CONN"))
"""

import os
import time

from typing import TypedDict, Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from llm_provider import get_chat_llm

# Intent routing is a cheap JSON-classification task → the LLM seam routes this
# to Ollama (local) or Gemini Flash (Vertex). json_mode=True constrains output
# to JSON on both backends.
_router_llm = get_chat_llm(json_mode=True)

_ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are an Oracle DBA intent classifier.
Classify the user's request into exactly one category and return ONLY a JSON
object with a single key "intent" whose value is one of:
  "diagnostic"  — health checks, performance queries, AWR/ASH reports, capacity,
                  AND listing/showing/querying backup history or backup job status
  "backup"      — initiating, running, scheduling, or executing RMAN backups,
                  restores, archivelog operations, or recovery tasks

IMPORTANT: "list rman backups", "show backup history", "last backup status",
"when was the last backup" are ALL "diagnostic" — they are read-only queries.
Only classify as "backup" when the user wants to RUN or EXECUTE a backup/restore.

Return only the JSON object.""",
    ),
    ("human", "{query}"),
])

_router_chain = _ROUTER_PROMPT | _router_llm


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    query:                str
    user:                 str        # authenticated caller (for the audit trail)
    user_intent:          str
    rman_params:          dict        # structured params extracted from NL
    proposed_rman_script: str
    final_result:         str
    generated_sql:        str        # raw SQL that was executed
    query_columns:        list       # column names from cursor.description
    query_rows:           list       # rows as list-of-lists (JSON-serialisable)
    query_error:          bool       # True when Oracle rejected the generated SQL


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def intent_router(state: AgentState) -> AgentState:
    """LLM-based intent classification — replaces brittle keyword matching."""
    import json
    response = _router_chain.invoke({"query": state["query"]})
    try:
        data = json.loads(response.content)
        intent: str = data.get("intent", "diagnostic")
    except (json.JSONDecodeError, AttributeError):
        intent = "diagnostic"

    # Normalize to known values; default to diagnostic (read-only is safer)
    if intent not in ("diagnostic", "backup"):
        intent = "diagnostic"

    return {**state, "user_intent": intent}


def diagnostic_node(state: AgentState) -> AgentState:
    from diagnostic_agent import run_diagnostic_query
    import gcp_audit

    # Governance context for the audit trail. engine/provider come from the same
    # env vars that drive the seams, so the logged row reflects the live backend.
    engine   = os.getenv("DB_ENGINE",     "oracle").lower()
    provider = os.getenv("LLM_PROVIDER",  "vertex").lower()
    user     = state.get("user", "agent")
    intent   = state.get("user_intent", "diagnostic")
    t0 = time.perf_counter()

    try:
        sql, cols, data = run_diagnostic_query(state["query"])
    except RuntimeError as e:
        gcp_audit.log_turn(
            query=state["query"], engine=engine, provider=provider, intent=intent,
            generated_sql="", row_count=0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            error=True, error_msg=str(e), user=user,
        )
        return {
            **state,
            "final_result":  str(e),
            "generated_sql": "",
            "query_columns": [],
            "query_rows":    [],
            "query_error":   True,
        }

    gcp_audit.log_turn(
        query=state["query"], engine=engine, provider=provider, intent=intent,
        generated_sql=sql, row_count=len(data),
        latency_ms=(time.perf_counter() - t0) * 1000,
        error=False, user=user,
    )
    summary = f"Returned {len(data)} row(s)."
    return {
        **state,
        "final_result":   summary,
        "generated_sql":  sql,
        "query_columns":  cols,
        "query_rows":     [list(r) for r in data],
        "query_error":    False,
    }


def backup_node(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
    from rman_agent import parse_backup_intent, generate_rman_script, execute_rman_backup
    import gcp_audit

    thread_id = (config or {}).get("configurable", {}).get("thread_id", "unknown")

    # 1. Use Ollama to extract structured backup parameters from natural language
    params = parse_backup_intent(state["query"])

    # 2. Generate the deterministic RMAN script from those parameters
    script = generate_rman_script(params)

    # 2b. Enqueue the pending approval onto the Cloud Tasks HITL queue so the
    #     write operation is auditable/traceable off-process (best-effort).
    gcp_audit.enqueue_hitl_approval(
        thread_id=thread_id, rman_script=script, backup_params=params.__dict__,
    )

    # 3. Pause the entire graph; persist state to checkpointer.
    #    The Python process is freed while waiting for human response.
    #    The UI sends back either "approve" or free-text feedback for revision.
    human_feedback: str = interrupt(
        {
            "message":       "Approve the following RMAN execution plan:",
            "rman_script":   script,
            "backup_params": params.__dict__,
        }
    )

    # 4. Graph resumes here with the human's response
    if human_feedback.strip().lower() == "approve":
        result = execute_rman_backup(script)
    else:
        # Human provided revision feedback — re-generate with the feedback
        # appended to the original query so context is preserved
        revised_query  = f"{state['query']}\n\nDBA revision feedback: {human_feedback}"
        revised_params = parse_backup_intent(revised_query)
        revised_script = generate_rman_script(revised_params)

        gcp_audit.enqueue_hitl_approval(
            thread_id=thread_id, rman_script=revised_script,
            backup_params=revised_params.__dict__, revision=True,
        )

        # Ask for approval again with the revised script
        second_feedback: str = interrupt(
            {
                "message":       "Revised RMAN plan based on your feedback. Approve?",
                "rman_script":   revised_script,
                "backup_params": revised_params.__dict__,
            }
        )

        if second_feedback.strip().lower() == "approve":
            result = execute_rman_backup(revised_script)
            script = revised_script
        else:
            result = "Operation aborted by database administrator."

    return {**state, "proposed_rman_script": script, "final_result": result}


def _route_intent(state: AgentState) -> Literal["diagnostic", "backup"]:
    return state["user_intent"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

builder = StateGraph(AgentState)
builder.add_node("router",     intent_router)
builder.add_node("diagnostic", diagnostic_node)
builder.add_node("backup",     backup_node)

builder.add_edge(START, "router")
builder.add_conditional_edges("router", _route_intent, {"diagnostic": "diagnostic", "backup": "backup"})
builder.add_edge("diagnostic", END)
builder.add_edge("backup",     END)

# MemorySaver is suitable for single-process development and demos.
# For production (distributed nodes, process restarts), replace with
# PostgresSaver so checkpointed state survives crashes and restarts.
checkpointer = MemorySaver()
app = builder.compile(checkpointer=checkpointer)

