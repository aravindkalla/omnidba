"""
LLM provider seam — the single switch between the local-first (Ollama) and
cloud (Vertex AI / Gemini) backends for OmniDBA v3.

Why this exists
---------------
v1 hard-wired `ChatOllama` in orchestrator.py and diagnostic_agent.py. v3 routes
every LLM construction through `get_chat_llm(...)` so the SAME LangGraph graph
runs on either backend by flipping one env var:

    LLM_PROVIDER=ollama   → fully local, zero external calls  (innovation #4: air-gapped)
    LLM_PROVIDER=vertex   → Gemini 2.5 (Flash for routing, Pro for SQL generation)

Model split (Vertex)
--------------------
    json_mode=True   → GEMINI_ROUTER_MODEL (default gemini-2.5-flash)  — intent routing, cheap/fast
    json_mode=False  → GEMINI_SQL_MODEL    (default gemini-2.5-pro)    — SQL generation / correction

Latency budget: reserve Pro for SQL only so diagnostic queries stay < 3s p95.

Usage
-----
    from llm_provider import get_chat_llm
    router_llm     = get_chat_llm(json_mode=True)          # returns JSON-formatted output
    correction_llm = get_chat_llm(temperature=0)           # deterministic SQL rewrite
"""

import os

# "ollama" | "vertex". Default vertex for the GCP build; set ollama for the local demo.
PROVIDER = os.getenv("LLM_PROVIDER", "vertex").lower()

_OLLAMA_HOST  = os.getenv("OLLAMA_HOST",  "http://localhost:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

_GCP_PROJECT  = os.getenv("GCP_PROJECT")
# GCP_REGION is where our project RESOURCES live (BigQuery dataset, Cloud Tasks
# queue) — asia-south1. It is NOT necessarily a Gemini serving region. Vertex
# Gemini calls must target VERTEX_LOCATION (us-central1), which has full 2.5
# model coverage; falling back to GCP_REGION here would 404 in asia-south1.
_VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
_ROUTER_MODEL = os.getenv("GEMINI_ROUTER_MODEL", "gemini-2.5-flash")
_SQL_MODEL    = os.getenv("GEMINI_SQL_MODEL",    "gemini-2.5-pro")


def get_chat_llm(*, json_mode: bool = False, temperature: float = 0):
    """
    Return a LangChain chat model for the configured provider.

    Parameters
    ----------
    json_mode : bool
        True  → constrain output to JSON AND (on Vertex) route to the cheaper
                Flash model. Use for the intent router.
        False → free-text output on the Pro model. Use for SQL generation/correction.
    temperature : float
        Sampling temperature. Keep 0 for SQL work (determinism).

    The returned object exposes the standard LangChain `.invoke(...)` interface,
    so orchestrator/diagnostic code is identical across providers.
    """
    if PROVIDER == "ollama":
        return _make_ollama(json_mode=json_mode, temperature=temperature)
    if PROVIDER == "vertex":
        return _make_vertex(json_mode=json_mode, temperature=temperature)
    raise ValueError(
        f"Unknown LLM_PROVIDER={PROVIDER!r}. Expected 'ollama' or 'vertex'."
    )


def _make_ollama(*, json_mode: bool, temperature: float):
    """Local-first backend — reproduces v1 behaviour exactly."""
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=_OLLAMA_MODEL,
        base_url=_OLLAMA_HOST,
        format="json" if json_mode else None,
        temperature=temperature,
    )


def _make_vertex(*, json_mode: bool, temperature: float):
    """Cloud backend — Gemini via Vertex AI. Auth via Application Default
    Credentials or GOOGLE_APPLICATION_CREDENTIALS service-account key."""
    if not _GCP_PROJECT:
        raise RuntimeError(
            "GCP_PROJECT is not set — required when LLM_PROVIDER=vertex. "
            "Set it in .env (see docs/GCP_REBUILD_RUNBOOK.md §6)."
        )
    from langchain_google_vertexai import ChatVertexAI
    model = _ROUTER_MODEL if json_mode else _SQL_MODEL
    return ChatVertexAI(
        model_name=model,
        project=_GCP_PROJECT,
        location=_VERTEX_LOCATION,
        temperature=temperature,
        # Gemini native JSON mode; harmless to omit for free-text calls.
        response_mime_type="application/json" if json_mode else None,
    )


def active_provider() -> dict:
    """Small introspection helper for /health endpoints and demo banners."""
    if PROVIDER == "ollama":
        return {"provider": "ollama", "host": _OLLAMA_HOST, "model": _OLLAMA_MODEL}
    return {
        "provider": "vertex",
        "project": _GCP_PROJECT,
        "location": _VERTEX_LOCATION,
        "router_model": _ROUTER_MODEL,
        "sql_model": _SQL_MODEL,
    }


if __name__ == "__main__":
    # Quick sanity check: python llm_provider.py
    print("Active LLM provider:", active_provider())
