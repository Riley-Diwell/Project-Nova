"""
intent_surface/loop.py - Section 5.3: Intent Surface  (Georgia)

STATUS: wip

WHAT THIS FILE IS
brief description
    1. 

WHO USES THIS
- Georgia: main.py's /event handler will call run(user_state) 
"""

# import necessary libraries
import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

# import nova libraries
from schemas.user_state import UserState
from schemas.event import Event
from schemas.signals import Signals

load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-haiku-4-5"
MAX_ITERATIONS = 5

SYSTEM_PROMPT = (
    "You are NOVA, an ambient assistant. You receive the user's current "
    "state as a JSON blob. Decide whether to say anything (short, natural "
    "speech — empty string if nothing warrants saying aloud) and whether "
    "to invoke any tools. Never fabricate context; if you are unsure, stay "
    "quiet. Please always call the echo_context tool."
)


# --- hardcoded local tool ---------------------------------------------------
# this is only for testing!

ECHO_TOOL: dict[str, Any] = {
    "name": "echo_context",
    "description": (
        "Echoes back a message. Reference tool only - proves the tool-"
        "calling loop is wired end-to-end. Do not rely on this for real "
        "behaviour."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
}

TOOLS: list[dict[str, Any]] = [ECHO_TOOL]


def _run_local_tool(name: str, tool_input: dict[str, Any]) -> Any:
    if name == "echo_context":
        return {"echoed": tool_input.get("message", "")}
    return {"error": f"unknown tool: {name}"}


# --- loop return type -------------------------------------------------------

class IntentResult(BaseModel):
    speech: str
    actions: list[str]


# --- the loop ---------------------------------------------------------------

def run(user_state: UserState, event:Event, signals:Signals) -> IntentResult:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_state.model_dump_json()},
    ]
    # iterate until an appropriate answer is reached
    for _ in range(MAX_ITERATIONS):
        # call model
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        # finished reasoning
        if response.stop_reason == "end_turn":
            speech = "".join(
                b.text for b in response.content if b.type == "text"
            )
            return IntentResult(speech=speech, actions=[])

        # if a tool is called
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _run_local_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # unexpected stop_reason (max_tokens, refusal, pause_turn, ...)
        break

    return IntentResult(speech="", actions=[])