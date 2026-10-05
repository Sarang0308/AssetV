"""Multi-agent orchestration on Gemini: a Supervisor delegates to specialist agents.

                       Supervisor Agent  (conversation, planning, final answer + chart picks)
                              │  delegate_to_* function calls (run in parallel)
          ┌───────────────────┼────────────────────┐
   Financial Agent      Anomaly Agent       Recommendation Agent
   cashflow, spending,  outliers, spikes,   health score, actions,
   assets, debt, NW,    duplicates/quality, what-if scenarios
   forecast             trends

Token budget per user turn (the key key-saving design choices):
  * supervisor: 2 calls — plan/delegate, then answer. Charts are requested inline in
    the answer as [[chart r3 line]] markers, so there is no extra render round trip;
  * specialists: 1 call each — a forced function call (thinking off) picks the tools;
    their compact results go straight back to the supervisor without a summary call;
  * tool declarations and results are compacted (see tools.py);
  * conversation history is trimmed to the last few exchanges.
Numbers still never pass through the LLM for charts: markers reference cached results.

`run_turn` yields UI events:
  {"type": "agent", "agent", "status": "start"|"done", "task"?}
  {"type": "tool_call", "agent", "name", "input"}
  {"type": "tool_result", "agent", "name", "ok", "summary"}
  {"type": "chart", "spec"}
  {"type": "text", "text"}
  {"type": "usage", "calls", "input_tokens", "output_tokens", "total_tokens"}
  {"type": "error", "message"}
  {"type": "done", "mode"}
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from google import genai
from google.genai import errors, types

from backend.agent.tools import CATEGORIES, TOOLS, ToolContext, run_tool

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
WORKER_MODEL = os.getenv("GEMINI_WORKER_MODEL", MODEL)
# Thinking tokens are billed as output; keep them small. Only applied to 2.5-series models.
SUPERVISOR_THINKING = int(os.getenv("GEMINI_SUPERVISOR_THINKING_BUDGET", "512"))
WORKER_THINKING = int(os.getenv("GEMINI_WORKER_THINKING_BUDGET", "0"))
MAX_STEPS = 4                 # supervisor model calls per user turn
HISTORY_TURNS = int(os.getenv("GEMINI_HISTORY_TURNS", "4"))  # past user exchanges kept in context

DATA_CONTEXT = ("Household finances, INR. Transactions Oct 2024-Sep 2026 (cleaned; bad rows quarantined); "
                "8 assets, 3 loans as of 01-Oct-2026. Now = Oct 2026, last month = Sep 2026. "
                "'Spending' excludes investments and EMIs.")
PERIODS = ("Periods: all, last_N_months, latest_month, previous_month, ytd, YYYY, FY2025-26 (Apr-Mar), "
           "Q1-2026, YYYY-MM, YYYY-MM:YYYY-MM.")

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}

SPECIALISTS = {
    "financial": {
        "label": "Financial Agent",
        "tools": ["get_financial_summary", "get_cashflow", "get_spending_breakdown", "compare_periods",
                  "get_category_trend", "get_spending_heatmap", "get_assets", "get_liabilities", "get_net_worth",
                  "forecast", "search_transactions"],
        "brief": "cash flow, spending, period comparisons, trends, assets, debt, net worth, forecast, transactions",
    },
    "anomaly": {
        "label": "Anomaly Agent",
        "tools": ["detect_anomalies", "get_data_quality_report", "search_transactions", "get_category_trend",
                  "compare_periods"],
        "brief": "outliers, spending spikes, duplicates/data quality, missed payments, what changed",
    },
    "recommendation": {
        "label": "Recommendation Agent",
        "tools": ["get_health_score", "get_recommendations", "simulate_scenario", "get_financial_summary",
                  "get_liabilities"],
        "brief": "health score, prioritised actions, what-if scenarios",
    },
}

SPECIALIST_PROMPT = ("You are the {label}. Call the tool(s) that answer the task, applying its periods/filters "
                     "as arguments. Call several tools at once if needed. {data} {periods} Categories: {cats}.")

SUPERVISOR_PROMPT = """You are Vantage, supervisor of financial-analysis agents for one Indian household. {data}

Agents (tasks must be self-contained: include periods, filters and context from earlier turns):
{team}

1. Delegate: call the agent(s) needed, in parallel when independent.
2. Answer from their results only — never invent numbers. Lead with a one-sentence answer, then 2-4 short bullets. \
Use ₹ with lakh/crore (₹36.9 L). Be brief.
3. Charts: add one line per chart, [[chart <result_id> <type>]], choosing a type from that result's "charts" list \
(first = default). Use the user's requested type when allowed. Usually 1-2 charts; none for trivial answers.
Follow-ups refer to the previous answer. "One-time" spending = exclude anomalies and Travel."""


def _declaration(tool: dict) -> types.FunctionDeclaration:
    schema = tool["input_schema"]
    if not schema.get("properties"):
        return types.FunctionDeclaration(name=tool["name"], description=tool["description"])
    return types.FunctionDeclaration(name=tool["name"], description=tool["description"], parameters_json_schema=schema)


def _thinking(model: str, budget: int) -> types.ThinkingConfig | None:
    return types.ThinkingConfig(thinking_budget=budget) if "2.5" in model else None


def _config(system: str, tool_dicts: list[dict], model: str, budget: int, force_call: bool = False):
    return types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=[_declaration(t) for t in tool_dicts])],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")) if force_call else None,
        thinking_config=_thinking(model, budget),
    )


DELEGATE_TOOLS = [{
    "name": f"delegate_to_{k}_agent",
    "description": f"{v['label']}: {v['brief']}.",
    "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
} for k, v in SPECIALISTS.items()]

SUPERVISOR_CONFIG = _config(
    SUPERVISOR_PROMPT.format(data=DATA_CONTEXT, team="\n".join(
        f"- delegate_to_{k}_agent: {v['brief']}" for k, v in SPECIALISTS.items())),
    DELEGATE_TOOLS, MODEL, SUPERVISOR_THINKING)
SPECIALIST_CONFIGS = {
    k: _config(SPECIALIST_PROMPT.format(label=v["label"], data=DATA_CONTEXT, periods=PERIODS, cats=", ".join(CATEGORIES)),
               [TOOL_BY_NAME[n] for n in v["tools"]], WORKER_MODEL, WORKER_THINKING, force_call=True)
    for k, v in SPECIALISTS.items()
}

CHART_MARKER = re.compile(r"\[\[\s*chart\s+(r\d+)(?:\s+([a-z_]+))?\s*\]\]", re.I)


@dataclass
class Session:
    id: str
    messages: list[types.Content] = field(default_factory=list)
    ctx: ToolContext = field(default_factory=ToolContext)


SESSIONS: dict[str, Session] = {}


def get_session(session_id: str | None) -> Session:
    if session_id and session_id in SESSIONS:
        return SESSIONS[session_id]
    s = Session(id=session_id or uuid.uuid4().hex)
    SESSIONS[s.id] = s
    return s


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def llm_available() -> bool:
    return bool(api_key())


class TurnBlocked(Exception):
    pass


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = self.input = self.output = 0

    def add(self, meta):
        if not meta:
            return
        with self.lock:
            self.calls += 1
            self.input += meta.prompt_token_count or 0
            self.output += (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)

    def event(self):
        return {"type": "usage", "calls": self.calls, "input_tokens": self.input, "output_tokens": self.output,
                "total_tokens": self.input + self.output}


def _generate(client, model, config, history, usage: Usage) -> types.Content:
    resp = client.models.generate_content(model=model, contents=history, config=config)
    usage.add(resp.usage_metadata)
    if not resp.candidates or resp.candidates[0].content is None:
        reason = getattr(resp.prompt_feedback, "block_reason", None) or (
            resp.candidates[0].finish_reason if resp.candidates else "no candidates")
        raise TurnBlocked(f"Gemini returned no content ({reason}).")
    content = resp.candidates[0].content
    content.parts = content.parts or []
    return content


def _texts(content: types.Content) -> str:
    return "\n".join(p.text for p in content.parts if p.text and not p.thought).strip()


def _calls(content: types.Content) -> list[types.FunctionCall]:
    return [p.function_call for p in content.parts if p.function_call]


def _response_part(call: types.FunctionCall, content: str, is_error: bool) -> types.Part:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = content
    body = {"error": payload} if is_error else {"result": payload}
    return types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=body))


# ---------------------------------------------------------------- specialists
def run_specialist(client, key: str, task: str, ctx: ToolContext, emit, usage: Usage) -> str:
    """One forced function call; tool results go straight back to the supervisor (no summary call)."""
    spec = SPECIALISTS[key]
    label = spec["label"]
    emit({"type": "agent", "agent": label, "status": "start", "task": task})
    results, note = [], None
    try:
        content = _generate(client, WORKER_MODEL, SPECIALIST_CONFIGS[key],
                            [types.Content(role="user", parts=[types.Part(text=task)])], usage)
        for call in _calls(content):
            args = dict(call.args or {})
            emit({"type": "tool_call", "agent": label, "name": call.name, "input": args})
            if call.name not in spec["tools"]:
                out, is_error = f"Tool {call.name} is not available to {label}", True
            else:
                out, _, is_error = run_tool(ctx, call.name, args)
            emit({"type": "tool_result", "agent": label, "name": call.name, "ok": not is_error,
                  "summary": out[:160] if is_error else json.loads(out)["result_id"]})
            results.append({"tool": call.name, "args": args, **({"error": out} if is_error else json.loads(out))})
        if not results:
            note = _texts(content) or "No tool selected."
    except TurnBlocked as e:
        note = str(e)
    emit({"type": "agent", "agent": label, "status": "done"})
    return json.dumps({"agent": label, "results": results, **({"note": note} if note else {})},
                      ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- supervisor
def _trimmed(messages: list[types.Content]) -> list[types.Content]:
    """Last HISTORY_TURNS exchanges, cut at a plain user message so function call/response pairs stay intact."""
    starts = [i for i, c in enumerate(messages) if c.role == "user" and any(p.text for p in c.parts)]
    return messages[starts[-HISTORY_TURNS]:] if len(starts) > HISTORY_TURNS else messages


def _emit_answer(text: str, ctx: ToolContext, emit):
    for rid, chart_type in CHART_MARKER.findall(text):
        _, spec, err = run_tool(ctx, "render_chart", {"result_id": rid, "chart_type": (chart_type or "").lower() or None})
        if err:  # type not allowed for this result -> its default chart
            _, spec, err = run_tool(ctx, "render_chart", {"result_id": rid})
        if spec:
            emit({"type": "chart", "spec": spec})
    clean = re.sub(r"\n{3,}", "\n\n", CHART_MARKER.sub("", text)).strip()
    if clean:
        emit({"type": "text", "text": clean})


def _supervise(session: Session, user_text: str, emit, usage: Usage):
    client = genai.Client(api_key=api_key())
    session.messages.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    for _ in range(MAX_STEPS):
        content = _generate(client, MODEL, SUPERVISOR_CONFIG, _trimmed(session.messages), usage)
        session.messages.append(content)  # kept intact: Gemini needs its thought signatures back
        calls = _calls(content)
        if not calls:
            _emit_answer(_texts(content), session.ctx, emit)
            return
        if text := _texts(content):
            emit({"type": "text", "text": CHART_MARKER.sub("", text).strip()})

        def handle(call: types.FunctionCall) -> tuple[str, bool]:
            key = call.name.removeprefix("delegate_to_").removesuffix("_agent")
            if key not in SPECIALISTS:
                return f"Unknown function {call.name}", True
            return run_specialist(client, key, dict(call.args or {}).get("task", ""), session.ctx, emit, usage), False

        outcomes: list[tuple[str, bool]] = []
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            for fut in [pool.submit(handle, c) for c in calls]:
                try:
                    outcomes.append(fut.result())
                except errors.APIError as e:
                    outcomes.append((f"Specialist failed: {e.message}", True))
        session.messages.append(types.Content(
            role="user", parts=[_response_part(c, *o) for c, o in zip(calls, outcomes)]))
    emit({"type": "text", "text": "_(stopped after too many steps)_"})


def run_turn(session: Session, user_text: str):
    if not llm_available():
        yield {"type": "error", "message": "No Gemini API key configured. Set GEMINI_API_KEY in .env and restart the server."}
        yield {"type": "done", "mode": "unconfigured"}
        return

    q: queue.Queue = queue.Queue()
    usage = Usage()

    def worker():
        try:
            _supervise(session, user_text, q.put, usage)
        except TurnBlocked as e:
            q.put({"type": "error", "message": str(e)})
        except errors.ClientError as e:
            hint = {400: "bad request", 401: "API key rejected", 403: "API key not permitted for this model",
                    404: f"model '{MODEL}' not found", 429: "rate limit / quota exceeded — retry shortly"}.get(e.code, "")
            q.put({"type": "error", "message": f"Gemini error {e.code}: {hint or e.message}"})
        except errors.ServerError as e:
            q.put({"type": "error", "message": f"Gemini server error {e.code} — please retry."})
        except Exception as e:  # network failures etc. — surface instead of hanging the stream
            q.put({"type": "error", "message": f"Agent failed: {e}"})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while (event := q.get()) is not None:
        yield event
    _repair_history(session)
    yield usage.event()
    yield {"type": "done", "mode": "llm", "model": MODEL}


def _repair_history(session: Session):
    """Keep history valid after a failed turn: the last entry must be a model turn without pending calls."""
    while session.messages:
        last = session.messages[-1]
        if last.role == "model" and not _calls(last):
            break
        session.messages.pop()
