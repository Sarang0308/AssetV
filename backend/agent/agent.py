"""Multi-agent orchestration on Gemini: a Supervisor delegates to specialist agents.

                       Supervisor Agent  (conversation, planning, charts, final answer)
                              │  delegate_to_* function calls (run in parallel)
          ┌───────────────────┼────────────────────┐
   Financial Agent      Anomaly Agent       Recommendation Agent
   cashflow, spending,  outliers, spikes,   health score, actions,
   assets, debt, NW,    duplicates/quality, what-if scenarios
   forecast             trends

Each specialist runs its own Gemini function-calling loop over a scoped tool
subset of the deterministic analytics engine. All agents share one ToolContext,
so the supervisor can chart any result a specialist produced (by result_id)
without numbers ever passing through the LLM.

`run_turn` yields UI events:
  {"type": "agent", "agent", "status": "start"|"done", "task"?}
  {"type": "tool_call", "agent", "name", "input"}
  {"type": "tool_result", "agent", "name", "ok", "summary"}
  {"type": "chart", "spec"}
  {"type": "text", "text"}
  {"type": "error", "message"}
  {"type": "done", "mode"}
"""
from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from google import genai
from google.genai import errors, types

from backend.agent.tools import TOOLS, ToolContext, run_tool

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
WORKER_MODEL = os.getenv("GEMINI_WORKER_MODEL", MODEL)
MAX_STEPS = 8

DATA_CONTEXT = """Data: 24 months of household transactions (Oct 2024 – Sep 2026, INR), 8 assets and 3 liabilities \
as of 01-Oct-2026. "Now" is early October 2026; "last month" means Sep 2026. Raw data had intentional defects \
(duplicates, missing fields, a malformed date, extreme outliers) that were cleaned or quarantined before analysis. \
"Spending" excludes investments and loan repayments; investments count as savings."""

TOOL_BY_NAME = {t["name"]: t for t in TOOLS}

SPECIALISTS = {
    "financial": {
        "label": "Financial Agent",
        "tools": ["get_financial_summary", "get_cashflow", "get_spending_breakdown", "compare_periods",
                  "get_category_trend", "get_spending_heatmap", "get_assets", "get_liabilities", "get_net_worth",
                  "forecast", "search_transactions"],
        "brief": "cash flow, income, spending breakdowns, period comparisons, category trends, assets, "
                 "liabilities/debt, net worth, forecasts, transaction lookups",
    },
    "anomaly": {
        "label": "Anomaly Agent",
        "tools": ["detect_anomalies", "get_data_quality_report", "search_transactions", "get_category_trend",
                  "compare_periods"],
        "brief": "unusual transactions/outliers, category spikes, duplicates and data-quality issues, "
                 "missed or irregular payments, what changed and why",
    },
    "recommendation": {
        "label": "Recommendation Agent",
        "tools": ["get_health_score", "get_recommendations", "simulate_scenario", "get_financial_summary",
                  "get_liabilities"],
        "brief": "0-100 health score and its drivers, prioritised actions with ₹ impact, what-if scenarios",
    },
}

SPECIALIST_PROMPT = """You are the {label}, a specialist inside a financial-analysis team. The supervisor gives you \
a task; investigate it with your tools and report findings back to the supervisor (not to the end user).

{data}

Rules:
- Every number must come from a tool result; do not do your own arithmetic beyond trivial differences.
- Apply any filters, periods or exclusions stated in the task as tool arguments. Call independent tools together.
- Be efficient: usually 1-3 tool calls are enough.
- Reply with a compact findings brief: key numbers, drivers, caveats. No preamble, no charts (the supervisor \
renders charts from your results)."""

SUPERVISOR_PROMPT = """You are Vantage, the supervisor of a team of financial-analysis agents serving one Indian \
household. You talk to the user; your specialists do the analysis.

{data}

Your team (delegate with the matching function; tasks must be self-contained — include periods, filters, \
exclusions and any context from earlier in the conversation, because specialists do not see the chat):
{team}

How to work:
- Plan first: split the user's question into specialist tasks. Delegate independent tasks together in one turn \
(several delegate calls at once). Simple follow-ups may need only one specialist.
- Each delegate result lists `results` (result_id, tool, chartable_as). Show, don't just tell: call render_chart \
for each visual that helps (usually 1-3). If the user names a chart type, use exactly that type when it's allowed \
for the source tool; otherwise say which types are possible. Do not chart trivially simple answers.
- Explicit instructions are filters ("exclude property", "only above ₹1 lakh", "2025 vs 2026") — pass them on.
- Follow-ups ("why?", "exclude one-time expenses", "show that monthly") refer to the previous answer. \
"One-time" spending means excluding anomalies and usually Travel.
- Never invent numbers; use only what specialists reported.

Answer style:
- Lead with the direct answer in one sentence, then 2-4 short bullets of supporting facts or drivers.
- Indian formatting: ₹ with lakh/crore where natural (₹36.87 L, ₹68,000).
- Mention data-quality caveats only when they affect the answer.
- Advice must be concrete and quantified. You are not a licensed advisor; say so briefly only when asked for \
specific investment products.
- Keep it tight: the charts carry the detail."""


def _declaration(tool: dict) -> types.FunctionDeclaration:
    schema = tool["input_schema"]
    if not schema.get("properties"):
        return types.FunctionDeclaration(name=tool["name"], description=tool["description"])
    return types.FunctionDeclaration(name=tool["name"], description=tool["description"], parameters_json_schema=schema)


def _delegate_tool(key: str, spec: dict) -> dict:
    return {
        "name": f"delegate_to_{key}_agent",
        "description": f"Ask the {spec['label']} to investigate a task. Specialises in: {spec['brief']}.",
        "input_schema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "Self-contained task with periods, filters and what to find out"}},
            "required": ["task"]},
    }


def _config(system: str, tool_dicts: list[dict]) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=[_declaration(t) for t in tool_dicts])],
        # we run the loop ourselves so every call is logged and streamed to the UI
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )


SUPERVISOR_CONFIG = _config(
    SUPERVISOR_PROMPT.format(
        data=DATA_CONTEXT,
        team="\n".join(f"- {v['label']} (delegate_to_{k}_agent): {v['brief']}" for k, v in SPECIALISTS.items())),
    [_delegate_tool(k, v) for k, v in SPECIALISTS.items()] + [TOOL_BY_NAME["render_chart"]],
)
SPECIALIST_CONFIGS = {
    k: _config(SPECIALIST_PROMPT.format(label=v["label"], data=DATA_CONTEXT), [TOOL_BY_NAME[n] for n in v["tools"]])
    for k, v in SPECIALISTS.items()
}


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


def _summarize(content: str) -> str:
    try:
        d = json.loads(content)
        data = d.get("data", d)
        keys = list(data)[:4] if isinstance(data, dict) else []
        return f"{d.get('result_id', '')} · {', '.join(keys)}"
    except (json.JSONDecodeError, AttributeError):
        return content[:160]


class TurnBlocked(Exception):
    pass


def _generate(client: genai.Client, model: str, config, history: list[types.Content]) -> types.Content:
    resp = client.models.generate_content(model=model, contents=history, config=config)
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
def run_specialist(client: genai.Client, key: str, task: str, ctx: ToolContext, emit) -> str:
    spec = SPECIALISTS[key]
    label = spec["label"]
    history = [types.Content(role="user", parts=[types.Part(text=task)])]
    produced: list[dict] = []
    findings = ""
    emit({"type": "agent", "agent": label, "status": "start", "task": task})
    try:
        for _ in range(MAX_STEPS):
            content = _generate(client, WORKER_MODEL, SPECIALIST_CONFIGS[key], history)
            history.append(content)
            if text := _texts(content):
                findings = text
            calls = _calls(content)
            if not calls:
                break
            parts = []
            for call in calls:
                args = dict(call.args or {})
                emit({"type": "tool_call", "agent": label, "name": call.name, "input": args})
                if call.name not in spec["tools"]:
                    result, is_error = f"Tool {call.name} is not available to {label}", True
                else:
                    result, _, is_error = run_tool(ctx, call.name, args)
                emit({"type": "tool_result", "agent": label, "name": call.name, "ok": not is_error,
                      "summary": result[:200] if is_error else _summarize(result)})
                if not is_error:
                    d = json.loads(result)
                    produced.append({"result_id": d["result_id"], "tool": call.name, "args": args,
                                     "chartable_as": d["chartable_as"], "default_chart": d["default_chart"]})
                parts.append(_response_part(call, result, is_error))
            history.append(types.Content(role="user", parts=parts))
    except TurnBlocked as e:
        findings = findings or str(e)
    emit({"type": "agent", "agent": label, "status": "done"})
    return json.dumps({"agent": label, "findings": findings or "(no findings)", "results": produced}, ensure_ascii=False)


# ---------------------------------------------------------------- supervisor
def _supervise(session: Session, user_text: str, emit):
    client = genai.Client(api_key=api_key())
    session.messages.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    for _ in range(MAX_STEPS):
        content = _generate(client, MODEL, SUPERVISOR_CONFIG, session.messages)
        session.messages.append(content)  # kept intact: Gemini needs its thought signatures back
        if text := _texts(content):
            emit({"type": "text", "text": text})
        calls = _calls(content)
        if not calls:
            return

        def handle(call: types.FunctionCall) -> tuple[str, bool]:
            args = dict(call.args or {})
            if call.name.startswith("delegate_to_"):
                key = call.name.removeprefix("delegate_to_").removesuffix("_agent")
                if key not in SPECIALISTS:
                    return f"Unknown agent {key}", True
                return run_specialist(client, key, args.get("task", ""), session.ctx, emit), False
            result, spec, is_error = run_tool(session.ctx, call.name, args)
            if spec:
                emit({"type": "chart", "spec": spec})
            elif is_error:
                emit({"type": "tool_result", "agent": "Supervisor", "name": call.name, "ok": False, "summary": result[:200]})
            return result, is_error

        # delegations run in parallel; charts run afterwards, in order
        delegations = [i for i, c in enumerate(calls) if c.name.startswith("delegate_to_")]
        outcomes: dict[int, tuple[str, bool]] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(delegations))) as pool:
            futures = {i: pool.submit(handle, calls[i]) for i in delegations}
            for i, fut in futures.items():
                try:
                    outcomes[i] = fut.result()
                except errors.APIError as e:
                    outcomes[i] = (f"Specialist failed: {e.message}", True)
        for i, call in enumerate(calls):
            if i not in outcomes:
                outcomes[i] = handle(call)
        session.messages.append(types.Content(
            role="user", parts=[_response_part(c, *outcomes[i]) for i, c in enumerate(calls)]))
    emit({"type": "text", "text": "_(stopped after too many steps)_"})


def run_turn(session: Session, user_text: str):
    if not llm_available():
        yield {"type": "error", "message": "No Gemini API key configured. Set GEMINI_API_KEY in .env and restart the server."}
        yield {"type": "done", "mode": "unconfigured"}
        return

    q: queue.Queue = queue.Queue()

    def worker():
        try:
            _supervise(session, user_text, q.put)
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
    yield {"type": "done", "mode": "llm", "model": MODEL}


def _repair_history(session: Session):
    """Keep history valid after a failed turn: the last entry must be a model turn without pending calls."""
    while session.messages:
        last = session.messages[-1]
        if last.role == "model" and not _calls(last):
            break
        session.messages.pop()
