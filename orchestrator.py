"""
LangGraph Orchestrator — central state machine for the OmniDBA multi-agent system.

Nodes
-----
  router      : LLM-based intent classifier (diagnostic vs backup)
  diagnostic  : NL2SQL query execution (3-tier pipeline in diagnostic_agent)
  backup      : RMAN script generation + HITL interrupt for human approval

Runtime backend switching (v3 UI)
---------------------------------
  `build_app(provider, engine)` compiles a graph bound to a specific LLM provider
  (ollama|vertex) and DB engine (oracle|postgres), CACHED by that pair, so the
  Streamlit UI flips backends live without a restart. The router LLM and the
  diagnostic pipeline are constructed for that provider/engine; audit rows are
  tagged with the actual backend (not the env). Module-level `app` (the env
  default) is preserved for api.py.

Checkpointer
------------
  Development : MemorySaver (per compiled graph). Production : PostgresSaver.
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


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    query:                str
    user:                 str        # authenticated caller (for the audit trail)
    user_intent:          str
    blocked:              bool       # Model Armor blocked the prompt
    guard_findings:       list       # which Model Armor filters matched
    rman_params:          dict
    proposed_rman_script: str
    final_result:         str
    generated_sql:        str
    query_columns:        list
    query_rows:           list
    query_error:          bool


# ---------------------------------------------------------------------------
# Graph factory — one compiled graph per (provider, engine), cached
# ---------------------------------------------------------------------------

_APP_CACHE: dict[tuple, object] = {}


def build_app(provider: str | None = None, engine: str | None = None):
    """Compile (or return cached) a LangGraph app bound to provider+engine."""
    provider = (provider or os.getenv("LLM_PROVIDER", "vertex")).lower()
    engine   = (engine   or os.getenv("DB_ENGINE",    "oracle")).lower()
    key = (provider, engine)
    if key in _APP_CACHE:
        return _APP_CACHE[key]

    router_chain = _ROUTER_PROMPT | get_chat_llm(provider=provider, json_mode=True)

    def guard_node(state: AgentState) -> AgentState:
        """Model Armor screen BEFORE the prompt reaches the router/LLM/DB.
        Skipped on the air-gapped Ollama path (no external calls)."""
        import model_armor
        import gcp_audit

        if provider == "ollama" or not model_armor.enabled():
            return {**state, "blocked": False, "guard_findings": []}

        verdict = model_armor.screen_prompt(state["query"])
        if verdict["blocked"]:
            gcp_audit.log_turn(
                query=state["query"], engine=engine, provider=provider,
                intent="blocked", generated_sql="", row_count=0, latency_ms=0,
                error=False, error_msg=verdict["reason"], user=state.get("user", "agent"),
            )
            return {
                **state,
                "blocked":       True,
                "guard_findings": verdict["findings"],
                "user_intent":   "blocked",
                "final_result":  verdict["reason"],
                "generated_sql": "",
                "query_columns": [],
                "query_rows":    [],
                "query_error":   False,
            }
        # Not blocked (may still be flagged in monitor mode) — carry findings through.
        return {**state, "blocked": False, "guard_findings": verdict["findings"]}

    def _after_guard(state: AgentState) -> Literal["blocked", "ok"]:
        return "blocked" if state.get("blocked") else "ok"

    def intent_router(state: AgentState) -> AgentState:
        import json
        response = router_chain.invoke({"query": state["query"]})
        try:
            data = json.loads(response.content)
            intent = data.get("intent", "diagnostic")
        except (json.JSONDecodeError, AttributeError):
            intent = "diagnostic"
        if intent not in ("diagnostic", "backup"):
            intent = "diagnostic"
        return {**state, "user_intent": intent}

    def diagnostic_node(state: AgentState) -> AgentState:
        from diagnostic_agent import run_diagnostic_query
        import gcp_audit

        user   = state.get("user", "agent")
        intent = state.get("user_intent", "diagnostic")
        t0 = time.perf_counter()
        try:
            sql, cols, data = run_diagnostic_query(
                state["query"], provider=provider, engine=engine,
            )
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
        return {
            **state,
            "final_result":   f"Returned {len(data)} row(s).",
            "generated_sql":  sql,
            "query_columns":  cols,
            "query_rows":     [list(r) for r in data],
            "query_error":    False,
        }

    def backup_node(state: AgentState, config: RunnableConfig | None = None) -> AgentState:
        from rman_agent import parse_backup_intent, generate_rman_script, execute_rman_backup
        import gcp_audit

        thread_id = (config or {}).get("configurable", {}).get("thread_id", "unknown")

        params = parse_backup_intent(state["query"])
        script = generate_rman_script(params)

        gcp_audit.enqueue_hitl_approval(
            thread_id=thread_id, rman_script=script, backup_params=params.__dict__,
        )

        human_feedback: str = interrupt({
            "message":       "Approve the following RMAN execution plan:",
            "rman_script":   script,
            "backup_params": params.__dict__,
        })

        if human_feedback.strip().lower() == "approve":
            result = execute_rman_backup(script)
        else:
            revised_query  = f"{state['query']}\n\nDBA revision feedback: {human_feedback}"
            revised_params = parse_backup_intent(revised_query)
            revised_script = generate_rman_script(revised_params)

            gcp_audit.enqueue_hitl_approval(
                thread_id=thread_id, rman_script=revised_script,
                backup_params=revised_params.__dict__, revision=True,
            )

            second_feedback: str = interrupt({
                "message":       "Revised RMAN plan based on your feedback. Approve?",
                "rman_script":   revised_script,
                "backup_params": revised_params.__dict__,
            })

            if second_feedback.strip().lower() == "approve":
                result = execute_rman_backup(revised_script)
                script = revised_script
            else:
                result = "Operation aborted by database administrator."

        return {**state, "proposed_rman_script": script, "final_result": result}

    def _route_intent(state: AgentState) -> Literal["diagnostic", "backup"]:
        return state["user_intent"]  # type: ignore[return-value]

    builder = StateGraph(AgentState)
    builder.add_node("guard",      guard_node)
    builder.add_node("router",     intent_router)
    builder.add_node("diagnostic", diagnostic_node)
    builder.add_node("backup",     backup_node)
    builder.add_edge(START, "guard")
    builder.add_conditional_edges("guard", _after_guard,
                                  {"blocked": END, "ok": "router"})
    builder.add_conditional_edges("router", _route_intent,
                                  {"diagnostic": "diagnostic", "backup": "backup"})
    builder.add_edge("diagnostic", END)
    builder.add_edge("backup",     END)

    compiled = builder.compile(checkpointer=MemorySaver())
    _APP_CACHE[key] = compiled
    return compiled


# Module-level default app (env provider/engine) — preserved for api.py.
app = build_app()
