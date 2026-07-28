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
from schemas.default_system_prompt import default_system_prompt
import memory

load_dotenv()

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

MODEL = "claude-haiku-4-5"
MAX_ITERATIONS = 10 # debug i can tune this

SYSTEM_PROMPT = f"""
{default_system_prompt}

You are the intent surface for an applictaion that focusses on provdiing a user experience that prioritises
user attention, autonomy and privacy. 

You will receive a UserState, Event and Signals object. First, you need to get some context. 

Call the filter_episodic_memory_tool to get a list of all of the previous times that an event occured. 
Using this list, try and identify any trends. Eg, on a accelerometer change event, check for similar times,
similar locations, etc. Can this tell you anything useful about the user?

Using this information, suggest a useful service that the user may intend to use now or soon. 
For example,
1. Navigation
2. Note-taking
3. Notification Management
4. Nothing needed (this is a valid answer, sometimes a user just doesn't need any change!)

Provide some useful speech (if needed) to inform the user of your suggestion. 
"""

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

# testing memory
FILTER_EPISODIC_MEMORY_TOOL: dict[str, Any] = {
    "name": "filter_episodic_memory_tool",
    "description": (
        """
        Filters episodic memory by event type. Call this when 
        you want to filter data in order to analyse trends from similar events. 
        """
    ),
    "input_schema": {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
}

TOOLS: list[dict[str, Any]] = [ECHO_TOOL, FILTER_EPISODIC_MEMORY_TOOL]


def _run_local_tool(name: str, tool_input: dict[str, Any]) -> Any:
    if name == "echo_context":
        return {"echoed": tool_input.get("message", "")}
    if name == "filter_episodic_memory_tool":
        rows = memory.read("event_type", event.type)
        return rows
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

    return IntentResult(speech="")