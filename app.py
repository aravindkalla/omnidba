"""
Streamlit UI — OmniDBA (Production)

Layout
------
  Sidebar   : connection info, model, conversation history controls
  Main area : chat-style message history + smart result rendering
  HITL panel: approval workflow rendered inline when graph is paused
"""

import os
import uuid
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from langgraph.types import Command
from orchestrator import app as lg_app

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Oracle AI DBA",
    page_icon="🗄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state bootstrap
# ---------------------------------------------------------------------------
if "thread_id"  not in st.session_state:
    st.session_state.thread_id  = str(uuid.uuid4())
if "messages"   not in st.session_state:
    st.session_state.messages   = []   # list of {role, content, result}
if "last_result" not in st.session_state:
    st.session_state.last_result = None

config = {"configurable": {"thread_id": st.session_state.thread_id}}


# ---------------------------------------------------------------------------
# Architecture HTML loader
# ---------------------------------------------------------------------------

@st.cache_data
def _load_html(filename: str) -> str:
    """Read an HTML architecture file once and cache it for the session.

    The cross-file button in index.html links to the flow diagram by filename,
    which won't resolve inside a Streamlit iframe.  We replace the href with a
    postMessage call so clicking it switches to the Architecture Flow tab.
    """
    path = Path(__file__).parent / filename
    if not path.exists():
        return f"<p style='color:red'>File not found: {filename}</p>"
    html = path.read_text(encoding="utf-8")
    # Replace the file-link with a postMessage that the tab-switcher listener picks up.
    html = html.replace(
        'href="OmniDBA_Architecture_Flow.html"',
        'href="#" onclick="window.parent.postMessage(\'switchToArchFlow\', \'*\'); return false;"',
    )
    return html


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🗄️ Oracle AI DBA")
    page = st.radio(
        "Navigate",
        ["💬 Chat", "🏗️ Architecture"],
        label_visibility="collapsed",
    )
    st.divider()

    # Connection badge
    oracle_dsn  = os.getenv("ORACLE_DSN",  "localhost:1521/FREEPDB1")
    oracle_user = os.getenv("ORACLE_USER", "system")
    st.markdown("**Database Connection**")
    st.success(f"🟢 {oracle_user}@{oracle_dsn}")

    # Model badge
    model = os.getenv("OLLAMA_MODEL", "llama3.1")
    st.markdown("**LLM / Embedding**")
    st.info(f"⚡ Ollama · {model}\n\n🔍 all-MiniLM-L6-v2 (ONNX)")

    st.divider()

    # Conversation history controls
    st.markdown("**Conversation**")
    st.caption(f"Thread: `{st.session_state.thread_id[:8]}…`")

    if st.button("🗑️ New Conversation", use_container_width=True):
        st.session_state.thread_id   = str(uuid.uuid4())
        st.session_state.messages    = []
        st.session_state.last_result = None
        st.rerun()

    if st.session_state.messages:
        st.markdown(f"**{len(st.session_state.messages)} message(s)**")
        for i, msg in enumerate(st.session_state.messages):
            role_icon = "🧑" if msg["role"] == "user" else "🤖"
            label = msg["content"][:40] + ("…" if len(msg["content"]) > 40 else "")
            st.caption(f"{role_icon} {label}")

    st.divider()
    st.caption("Stack: LangGraph · Vanna · ChromaDB · Ollama · Oracle 26ai")


# ---------------------------------------------------------------------------
# Smart result renderer
# ---------------------------------------------------------------------------

def _bytes_to_gb(df: pd.DataFrame) -> pd.DataFrame:
    """Convert raw-byte columns to GB in-place, rename header."""
    for col in list(df.columns):
        if any(kw in col.upper() for kw in ("BYTES", "_SPACE")) and df[col].dtype in ("int64", "float64"):
            df[col] = (df[col] / 1_073_741_824).round(2)
            df.rename(columns={col: col.replace("_SPACE", "_GB").replace("_BYTES", "_GB")}, inplace=True)
    return df


def _usage_chart(df: pd.DataFrame, pct_col: str, name_col: str) -> go.Figure:
    """Horizontal bar chart with traffic-light colouring."""
    colors = [
        "#e74c3c" if v >= 90 else "#f39c12" if v >= 75 else "#2ecc71"
        for v in df[pct_col]
    ]
    fig = go.Figure(go.Bar(
        x=df[pct_col],
        y=df[name_col],
        orientation="h",
        marker_color=colors,
        text=[f"{v:.1f}%" for v in df[pct_col]],
        textposition="outside",
        hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
    ))
    fig.add_vline(x=90, line_dash="dash", line_color="#e74c3c",
                  annotation_text="90% critical", annotation_position="top right")
    fig.add_vline(x=75, line_dash="dot", line_color="#f39c12",
                  annotation_text="75% warning", annotation_position="top right")
    fig.update_layout(
        xaxis=dict(range=[0, 115], title="Used %", ticksuffix="%"),
        yaxis=dict(title="", autorange="reversed"),
        height=max(220, len(df) * 56),
        margin=dict(l=10, r=40, t=20, b=30),
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font_color="#fafafa",
    )
    return fig


def _elapsed_chart(df: pd.DataFrame, elapsed_col: str, id_col: str) -> go.Figure:
    """Horizontal bar for SQL elapsed-time ranking."""
    top = df.nlargest(10, elapsed_col)
    fig = go.Figure(go.Bar(
        x=top[elapsed_col],
        y=top[id_col].astype(str),
        orientation="h",
        marker_color="#3498db",
        hovertemplate="%{y}<br>Elapsed: %{x:,}<extra></extra>",
    ))
    fig.update_layout(
        xaxis=dict(title="Elapsed Time (µs)"),
        yaxis=dict(title="SQL ID", autorange="reversed"),
        height=max(220, len(top) * 50),
        margin=dict(l=10, r=10, t=20, b=30),
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font_color="#fafafa",
    )
    return fig


def render_diagnostic_result(result: dict) -> None:
    """
    Full production result renderer.

    Decision tree
    -------------
      1. Show SQL in collapsible code block
      2. Render DataFrame (with GB conversion for byte columns)
      3. Detect result type and add chart + health badges
    """
    sql        = result.get("generated_sql", "")
    cols       = result.get("query_columns", [])
    rows       = result.get("query_rows", [])
    final      = result.get("final_result", "")
    has_error  = result.get("query_error", False)

    # ── Oracle / generation error ────────────────────────────────────────────
    if has_error:
        st.error("SQL generation or execution failed.")
        parts = final.split("Generated SQL:\n", 1)
        st.write(parts[0].strip())
        if len(parts) == 2:
            with st.expander("❌ Failed SQL", expanded=True):
                st.code(parts[1].strip(), language="sql")
        st.caption("Tip: rephrase your question or check that the referenced views exist.")
        return

    # ── SQL ─────────────────────────────────────────────────────────────────
    if sql:
        with st.expander("📋 Generated SQL", expanded=False):
            st.code(sql, language="sql")

    if not cols or not rows:
        st.info(final or "Query returned no rows.")
        return

    df = pd.DataFrame(rows, columns=cols)
    df = _bytes_to_gb(df)

    row_label = f"{'1 row' if len(df) == 1 else f'{len(df)} rows'} returned"

    # ── Detect column patterns ───────────────────────────────────────────────
    upper_cols = [c.upper() for c in df.columns]

    pct_col  = next((df.columns[i] for i, c in enumerate(upper_cols)
                     if "PCT" in c or ("USED" in c and "%" in c)), None)
    if pct_col is None:
        pct_col = next((df.columns[i] for i, c in enumerate(upper_cols) if "PCT" in c), None)

    elapsed_col = next((df.columns[i] for i, c in enumerate(upper_cols)
                        if "ELAPSED" in c), None)

    name_col = next((df.columns[i] for i, c in enumerate(upper_cols)
                     if any(kw in c for kw in ("NAME", "TABLESPACE", "OWNER", "SQL_ID"))),
                    df.columns[0])

    # ── Tablespace / usage view ──────────────────────────────────────────────
    if pct_col:
        st.markdown(f"### Tablespace Usage — *{row_label}*")

        # Health badges
        critical = df[df[pct_col] >= 90][name_col].tolist()
        warning  = df[(df[pct_col] >= 75) & (df[pct_col] < 90)][name_col].tolist()
        healthy  = df[df[pct_col] < 75]

        badge_cols = st.columns(3)
        badge_cols[0].metric("🔴 Critical (≥90%)", len(critical),
                              delta=f"{', '.join(map(str, critical))}" if critical else "None",
                              delta_color="inverse")
        badge_cols[1].metric("🟡 Warning (75–90%)", len(warning),
                              delta=f"{', '.join(map(str, warning))}" if warning else "None",
                              delta_color="inverse")
        badge_cols[2].metric("🟢 Healthy (<75%)", len(healthy))

        # Bar chart
        st.plotly_chart(_usage_chart(df, pct_col, name_col), use_container_width=True, key=f"usage_chart_{id(df)}")

        # Alerts
        if critical:
            st.error(f"🚨 **Action required** — tablespace(s) above 90%: `{'`, `'.join(map(str, critical))}`")
        if warning:
            st.warning(f"⚠️ **Monitor** — tablespace(s) between 75–90%: `{'`, `'.join(map(str, warning))}`")

        # Styled dataframe
        gb_cols = [c for c in df.columns if c.endswith("_GB")]
        format_dict = {c: "{:.2f} GB" for c in gb_cols}
        if pct_col in df.columns:
            format_dict[pct_col] = "{:.2f}%"

        def _pct_bar(val):
            if pct_col not in df.columns:
                return ""
            color = "#e74c3c" if val >= 90 else "#f39c12" if val >= 75 else "#2ecc71"
            return f"background-color: {color}22; color: {color}; font-weight: bold"

        st.dataframe(
            df.style.format(format_dict).map(_pct_bar, subset=[pct_col]),
            use_container_width=True,
        )

    # ── SQL performance view ─────────────────────────────────────────────────
    elif elapsed_col:
        st.markdown(f"### Top SQL by Elapsed Time — *{row_label}*")
        st.plotly_chart(_elapsed_chart(df, elapsed_col, name_col), use_container_width=True, key=f"elapsed_chart_{id(df)}")
        st.dataframe(df, use_container_width=True)

    # ── Generic table (blocking sessions, invalid objects, etc.) ────────────
    else:
        st.markdown(f"### Query Results — *{row_label}*")

        # Highlight INVALID / BLOCKED rows if status column present
        status_col = next((c for c in df.columns if "STATUS" in c.upper()
                           or "WAIT_CLASS" in c.upper()), None)
        if status_col:
            def _row_style(row):
                val = str(row.get(status_col, "")).upper()
                if val in ("INVALID", "BLOCKED", "WAITING"):
                    return ["background-color: #5c1a1a"] * len(row)
                return [""] * len(row)
            st.dataframe(df.style.apply(_row_style, axis=1), use_container_width=True)
        else:
            st.dataframe(df, use_container_width=True)

    # ── CSV export ───────────────────────────────────────────────────────────
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Export CSV",
        data=csv,
        file_name="oracle_query_result.csv",
        mime="text/csv",
        key=f"csv_export_{id(df)}",
    )


# ---------------------------------------------------------------------------
# HITL approval panel
# ---------------------------------------------------------------------------

def render_hitl_panel() -> None:
    state_snapshot  = lg_app.get_state(config)
    active_interrupts = [
        intr
        for task in state_snapshot.tasks
        for intr in task.interrupts
    ]
    if not active_interrupts:
        return

    payload = active_interrupts[0].value

    st.divider()
    st.subheader("⚠️ RMAN Backup — DBA Approval Required")
    st.write(payload.get("message", "Review the proposed operation:"))

    tab_script, tab_params = st.tabs(["RMAN Script", "Backup Parameters"])

    with tab_script:
        st.code(payload.get("rman_script", ""), language="sql")

    with tab_params:
        params = payload.get("backup_params", {})
        p_df = pd.DataFrame([
            {"Parameter": k, "Value": str(v)}
            for k, v in params.items()
        ])
        st.dataframe(p_df, use_container_width=True, hide_index=True)

    st.divider()
    col_approve, col_reject, col_revise = st.columns([1, 1, 2])

    with col_approve:
        if st.button("✅ Approve Execution", type="primary", use_container_width=True):
            with st.spinner("Executing approved backup…"):
                res = lg_app.invoke(Command(resume="approve"), config=config)
            st.session_state.last_result = res
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Backup approved and executed.",
                "result": res,
            })
            st.rerun()

    with col_reject:
        if st.button("❌ Reject / Abort", use_container_width=True):
            with st.spinner("Aborting…"):
                res = lg_app.invoke(Command(resume="reject"), config=config)
            st.session_state.last_result = res
            st.session_state.messages.append({
                "role": "assistant",
                "content": "Backup aborted.",
                "result": res,
            })
            st.rerun()

    with col_revise:
        revision = st.text_input(
            "Revision instructions:",
            placeholder="e.g. Use 4 channels and delay by 2 hours",
            key="revision_input",
        )
        if st.button("🔄 Submit Revision", use_container_width=True) and revision:
            with st.spinner("Revising plan…"):
                res = lg_app.invoke(Command(resume=revision), config=config)
            st.session_state.last_result = res
            st.session_state.messages.append({
                "role": "assistant",
                "content": f'Revised per: "{revision}"',
                "result": res,
            })
            st.rerun()


# ---------------------------------------------------------------------------
# Main — chat interface
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Architecture page — rendered before the chat section; st.stop() prevents
# the chat UI from rendering when this page is selected.
# ---------------------------------------------------------------------------
if page == "🏗️ Architecture":
    st.markdown("## 🏗️ Architecture Reference")
    st.caption(
        "Technical design documents for OmniDBA — Autonomous AI Database Administrator. "
        "Use the tabs below to switch between the white-paper overview and the animated flow diagram."
    )
    # Hidden zero-height iframe that listens for the postMessage sent by the
    # "View Architecture Diagram" button inside the System Overview iframe.
    # Because all iframes share the same localhost origin we can attach the
    # listener directly to window.parent (the Streamlit page).  A guard flag
    # prevents duplicate listeners across Streamlit re-runs.
    components.html("""
<script>
(function () {
  var p = window.parent;
  if (p._archTabListenerAdded) return;
  p._archTabListenerAdded = true;
  p.addEventListener('message', function (e) {
    if (e.data !== 'switchToArchFlow') return;
    // Streamlit renders tab buttons as <button data-baseweb="tab">
    var tabs = p.document.querySelectorAll('[data-baseweb="tab"]');
    for (var i = 0; i < tabs.length; i++) {
      if (tabs[i].textContent.indexOf('Architecture Flow') !== -1) {
        tabs[i].click();
        break;
      }
    }
  });
})();
</script>
""", height=0)

    tab_overview, tab_flow = st.tabs(["📋 System Overview", "🔀 Architecture Flow"])

    with tab_overview:
        # Initial height is a safe minimum; the injected ResizeObserver JS
        # calls window.frameElement.style.height after fonts and layout settle,
        # so the iframe expands to the exact rendered height on every device.
        components.html(
            _load_html("index.html"),
            height=600,
            scrolling=True,
        )

    with tab_flow:
        # Initial height is a safe minimum; the scale-to-fit JS shrinks the
        # 1400 px canvas to the viewport width and sets the iframe height
        # accordingly — ~1000 px on laptop, ~550 px on tablet, ~270 px on mobile.
        components.html(
            _load_html("OmniDBA_Architecture_Flow.html"),
            height=600,
            scrolling=True,
        )

    st.stop()   # do not render the chat UI on this page


# ---------------------------------------------------------------------------
# Chat page
# ---------------------------------------------------------------------------
st.markdown("## 🗄️ OmniDBA")
st.caption("Ask in plain English — diagnostic queries run instantly; backup requests go through DBA approval.")
st.divider()

# Render existing conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "🤖"):
        st.write(msg["content"])
        result = msg.get("result")
        if result and result.get("user_intent") == "diagnostic":
            render_diagnostic_result(result)
        elif result and result.get("final_result") and result.get("user_intent") == "backup":
            status = result["final_result"]
            if "SUCCEEDED" in status:
                st.success(status)
            elif "FAILED" in status:
                st.error(status)
            elif "aborted" in status.lower():
                st.warning(status)
            else:
                st.info(status)

# HITL panel sits above the input if a backup is pending
render_hitl_panel()

# ── Quick-prompt chips ────────────────────────────────────────────────────
QUICK_PROMPTS = [
    ("👥 Active Sessions",
     "Show all active user sessions from v$session with username, machine, program, status and wait event"),
    ("📜 Redo Log Status",
     "Show redo log group status including group number, members, size, status and archived flag from v$log"),
    ("❌ Invalid Objects",
     "List all invalid database objects showing owner, object name, object type and last DDL time from dba_objects"),
]

# Render chips as small buttons in a horizontal row
chip_cols = st.columns(len(QUICK_PROMPTS))
quick_input = None
for col, (label, prompt) in zip(chip_cols, QUICK_PROMPTS):
    with col:
        if st.button(label, use_container_width=True, key=f"chip_{label}"):
            quick_input = prompt

# ── Chat input ────────────────────────────────────────────────────────────
user_input = st.chat_input("e.g.  Show tablespace usage  |  Run a full compressed backup tonight")

# Merge: typed input takes priority; chip click falls back
final_input = user_input or quick_input

if final_input:
    # Show user message immediately
    with st.chat_message("user", avatar="🧑"):
        st.write(final_input)

    st.session_state.messages.append({
        "role": "user",
        "content": final_input,
        "result": None,
    })

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Thinking…"):
            result = lg_app.invoke({"query": final_input}, config=config)
        st.session_state.last_result = result

        intent = result.get("user_intent", "")
        if intent == "diagnostic":
            st.write(result.get("final_result", ""))
            render_diagnostic_result(result)
        elif intent == "backup":
            # Backup may have paused at interrupt — HITL panel handles it
            final = result.get("final_result", "")
            if final and "aborted" not in final.lower():
                st.info(final)
        else:
            st.write(result.get("final_result", "Done."))

    st.session_state.messages.append({
        "role": "assistant",
        "content": result.get("final_result", ""),
        "result": result,
    })

    st.rerun()

