"""The multi-agent system, built as a LangGraph graph.

One user question flows through this graph:

        START
          │
       [plan]            Supervisor (Gemini) splits the question into tasks:
          │              [{"agent": "financial", "task": "..."}, {"agent": "anomaly", ...}]
    ┌─────┼──────┐       One "specialist" node runs per task, all in parallel (LangGraph "Send").
    ▼     ▼      ▼
 [specialist] ...        Each specialist (Gemini) picks analytics functions; Python runs them.
    └─────┼──────┘
          ▼
       [answer]          Supervisor (Gemini) writes the reply from the results + picks charts.
          │
         END

Cost per question: 1 plan call + 1 call per specialist + 1 answer call.
The AI never does maths: numbers come from backend/analytics/engine.py, and charts are drawn
from cached results that the answer only *refers to* with markers like [[chart r3 line]].
"""
from __future__ import annotations

import json
import operator
import re
import uuid
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from google.genai import errors
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from backend.agent import gemini
from backend.agent.prompts import ANSWER_PROMPT, PLANNER_PROMPT, SPECIALIST_PROMPT, SPECIALISTS
from backend.agent.tools import CATEGORIES, TOOLS, ToolContext, run_tool

HISTORY_TURNS = 4  # how many earlier questions the AI remembers (fewer = cheaper)
TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}
CHART_MARKER = re.compile(r"\[\[\s*chart\s+(r\d+)(?:\s+([a-z_]+))?\s*\]\]", re.IGNORECASE)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"tasks": {"type": "array", "items": {
        "type": "object",
        "properties": {"agent": {"type": "string", "enum": list(SPECIALISTS)}, "task": {"type": "string"}},
        "required": ["agent", "task"]}}},
    "required": ["tasks"],
}


# ---------------------------------------------------------------- state & context
class TurnState(TypedDict, total=False):
    """Data that flows between the nodes for one question."""
    question: str
    history: str                                   # short text summary of earlier turns
    tasks: list[dict]                              # filled by `plan`
    results: Annotated[list[dict], operator.add]   # every specialist APPENDS its results here
    answer: str                                    # filled by `answer`


class SpecialistInput(TypedDict):
    """What `plan` sends to each parallel `specialist` node."""
    agent: str
    task: str


@dataclass
class TurnContext:
    """Things the nodes need that are not data: the Gemini client, the result cache, the token counter."""
    client: object
    tool_cache: ToolContext
    tokens: gemini.TokenCounter


# ---------------------------------------------------------------- nodes
def plan(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    prompt = f"{state['history']}\n\nUser question: {state['question']}"
    plan_json = gemini.ask_json(runtime.context.client, PLANNER_PROMPT, prompt, PLAN_SCHEMA, runtime.context.tokens)
    tasks = [t for t in plan_json.get("tasks", []) if t.get("agent") in SPECIALISTS]
    return {"tasks": tasks}


def route_to_specialists(state: TurnState):
    """Start one `specialist` node per task (they run in parallel). No tasks -> go straight to `answer`."""
    if not state["tasks"]:
        return "answer"
    return [Send("specialist", {"agent": t["agent"], "task": t["task"]}) for t in state["tasks"]]


def specialist(task: SpecialistInput, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    send_to_ui = runtime.stream_writer
    info = SPECIALISTS[task["agent"]]
    label = info["label"]
    send_to_ui({"type": "agent", "agent": label, "status": "start", "task": task["task"]})

    # 1) Gemini chooses which analytics functions to call (and with which arguments).
    system = SPECIALIST_PROMPT.format(label=label, categories=", ".join(CATEGORIES))
    allowed_tools = [TOOLS_BY_NAME[name] for name in info["tools"]]
    calls = gemini.ask_for_tools(ctx.client, system, task["task"], allowed_tools, ctx.tokens)

    # 2) Python runs them. Results are cached (for charts) and summarised compactly for the AI.
    results = []
    for name, args in calls:
        send_to_ui({"type": "tool_call", "agent": label, "name": name, "input": args})
        if name not in info["tools"]:
            results.append({"agent": label, "tool": name, "error": "not allowed for this agent"})
            continue
        output, _, is_error = run_tool(ctx.tool_cache, name, args)
        if is_error:
            send_to_ui({"type": "tool_result", "agent": label, "name": name, "ok": False, "summary": output})
            results.append({"agent": label, "tool": name, "args": args, "error": output})
        else:
            results.append({"agent": label, "tool": name, "args": args, **json.loads(output)})

    send_to_ui({"type": "agent", "agent": label, "status": "done"})
    return {"results": results}


def answer(state: TurnState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    send_to_ui = runtime.stream_writer
    results_json = json.dumps(state.get("results", []), ensure_ascii=False, separators=(",", ":"))
    prompt = f"{state['history']}\n\nUser question: {state['question']}\n\nResults: {results_json}"
    text = gemini.ask_text(ctx.client, ANSWER_PROMPT, prompt, ctx.tokens)

    # Turn every [[chart r3 line]] marker into a real chart, then remove the markers from the text.
    for result_id, chart_type in CHART_MARKER.findall(text):
        _, chart, failed = run_tool(ctx.tool_cache, "render_chart", {"result_id": result_id, "chart_type": chart_type.lower() or None})
        if failed:  # that chart type isn't possible for this data -> use its default chart
            _, chart, failed = run_tool(ctx.tool_cache, "render_chart", {"result_id": result_id})
        if chart:
            send_to_ui({"type": "chart", "spec": chart})

    clean_text = re.sub(r"\n{3,}", "\n\n", CHART_MARKER.sub("", text)).strip()
    send_to_ui({"type": "text", "text": clean_text})
    return {"answer": clean_text}


# ---------------------------------------------------------------- build the graph
def build_graph():
    graph = StateGraph(TurnState, context_schema=TurnContext)
    graph.add_node("plan", plan)
    graph.add_node("specialist", specialist)
    graph.add_node("answer", answer)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_to_specialists, ["specialist", "answer"])
    graph.add_edge("specialist", "answer")   # waits for ALL parallel specialists before answering
    graph.add_edge("answer", END)
    return graph.compile()


GRAPH = build_graph()


# ---------------------------------------------------------------- sessions (conversation memory)
@dataclass
class Session:
    id: str
    turns: list[dict] = field(default_factory=list)            # [{question, tasks, answer}, ...]
    ctx: ToolContext = field(default_factory=ToolContext)      # cached results, used to draw/switch charts


SESSIONS: dict[str, Session] = {}


def get_session(session_id: str | None) -> Session:
    if session_id and session_id in SESSIONS:
        return SESSIONS[session_id]
    session = Session(id=session_id or uuid.uuid4().hex)
    SESSIONS[session.id] = session
    return session


def history_text(session: Session) -> str:
    """A short, cheap summary of the last few turns so follow-up questions make sense."""
    lines = []
    for turn in session.turns[-HISTORY_TURNS:]:
        tasks = "; ".join(f"{t['agent']}: {t['task']}" for t in turn["tasks"]) or "none"
        lines.append(f"Earlier question: {turn['question']}\n  tasks: {tasks}\n  answer: {turn['answer'][:300]}")
    return "Conversation so far:\n" + "\n".join(lines) if lines else "This is the first question."


def llm_available() -> bool:
    return bool(gemini.api_key())


MODEL = gemini.MODEL


def run_turn(session: Session, question: str):
    """Run the graph for one question and yield UI events (see frontend/src/components/Chat.jsx)."""
    if not llm_available():
        yield {"type": "error", "message": "No Gemini API key configured. Set GEMINI_API_KEY in .env and restart the server."}
        yield {"type": "done", "mode": "unconfigured"}
        return

    context = TurnContext(client=gemini.make_client(), tool_cache=session.ctx, tokens=gemini.TokenCounter())
    start_state = {"question": question, "history": history_text(session), "results": []}
    final_state = {}
    try:
        # stream_mode "custom" = the events nodes send with runtime.stream_writer;
        # "values" = the graph state after each step (we keep the last one).
        for mode, data in GRAPH.stream(start_state, context=context, stream_mode=["custom", "values"]):
            if mode == "custom":
                yield data
            else:
                final_state = data
        session.turns.append({"question": question, "tasks": final_state.get("tasks", []),
                              "answer": final_state.get("answer", "")})
    except gemini.EmptyResponse as e:
        yield {"type": "error", "message": str(e)}
    except errors.ClientError as e:
        hints = {400: "bad request", 401: "API key rejected", 403: "API key not allowed to use this model",
                 404: f"model '{MODEL}' not found", 429: "rate limit / quota reached — wait a moment and retry"}
        yield {"type": "error", "message": f"Gemini error {e.code}: {hints.get(e.code) or e.message}"}
    except errors.ServerError as e:
        yield {"type": "error", "message": f"Gemini server error {e.code} — please retry."}
    except Exception as e:  # e.g. no internet
        yield {"type": "error", "message": f"Something went wrong: {e}"}

    yield context.tokens.as_event()
    yield {"type": "done", "mode": "llm", "model": MODEL}
