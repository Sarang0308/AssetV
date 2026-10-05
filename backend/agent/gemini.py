"""Small helpers around the Gemini API (google-genai SDK).

Three kinds of calls are used:
  ask_json(...)        -> the planner: returns a Python dict that matches a JSON schema
  ask_for_tools(...)   -> a specialist: returns the function calls Gemini wants to make
  ask_text(...)        -> the final answer: returns plain text
Every call adds its token counts to a TokenCounter so the UI can show the cost.
"""
from __future__ import annotations

import json
import os
import threading

from google import genai
from google.genai import types

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
WORKER_MODEL = os.getenv("GEMINI_WORKER_MODEL", MODEL)
# "Thinking" tokens are billed. 0 switches thinking off (only supported on gemini-2.5 models).
SUPERVISOR_THINKING = int(os.getenv("GEMINI_SUPERVISOR_THINKING_BUDGET", "512"))
WORKER_THINKING = int(os.getenv("GEMINI_WORKER_THINKING_BUDGET", "0"))


def api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def make_client() -> genai.Client:
    return genai.Client(api_key=api_key())


class TokenCounter:
    """Adds up tokens across all AI calls in one question (specialists run in parallel, hence the lock)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def add(self, usage):
        if usage is None:
            return
        with self._lock:
            self.calls += 1
            self.input_tokens += usage.prompt_token_count or 0
            self.output_tokens += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)

    def as_event(self) -> dict:
        return {"type": "usage", "calls": self.calls, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "total_tokens": self.input_tokens + self.output_tokens}


class EmptyResponse(Exception):
    """Gemini returned nothing (e.g. blocked by a safety filter)."""


def _thinking(model: str, budget: int):
    return types.ThinkingConfig(thinking_budget=budget) if "2.5" in model else None


def _call(client, model, prompt, config, counter: TokenCounter):
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    counter.add(response.usage_metadata)
    if not response.candidates or response.candidates[0].content is None:
        raise EmptyResponse("Gemini returned no answer (it may have been blocked). Try rephrasing.")
    return response


def ask_json(client, system: str, prompt: str, schema: dict, counter: TokenCounter) -> dict:
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_json_schema=schema,
        thinking_config=_thinking(MODEL, SUPERVISOR_THINKING),
    )
    response = _call(client, MODEL, prompt, config, counter)
    return json.loads(response.text)


def ask_for_tools(client, system: str, prompt: str, tool_list: list[dict], counter: TokenCounter):
    """Force Gemini to answer with function calls (mode="ANY") and return them as (name, args) pairs."""
    declarations = []
    for tool in tool_list:
        schema = tool["input_schema"]
        if schema.get("properties"):
            declarations.append(types.FunctionDeclaration(
                name=tool["name"], description=tool["description"], parameters_json_schema=schema))
        else:
            declarations.append(types.FunctionDeclaration(name=tool["name"], description=tool["description"]))

    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=declarations)],
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),  # we run the tools
        thinking_config=_thinking(WORKER_MODEL, WORKER_THINKING),
    )
    response = _call(client, WORKER_MODEL, prompt, config, counter)
    return [(call.name, dict(call.args or {})) for call in (response.function_calls or [])]


def ask_text(client, system: str, prompt: str, counter: TokenCounter) -> str:
    config = types.GenerateContentConfig(
        system_instruction=system,
        thinking_config=_thinking(MODEL, SUPERVISOR_THINKING),
    )
    response = _call(client, MODEL, prompt, config, counter)
    return response.text or ""
