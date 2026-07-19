
# pip install google-genai


# import necessary libraries
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

from pydantic import BaseModel, Field

from fastmcp import Client
import asyncio

# load environment where the API keys are stored
load_dotenv()



# mcp connection
mcp_url = "http://localhost:8000/mcp"

system_prompt =    """You are the AI assistant for a new novel personal computing device that manages attention, autonomy and privacy. Please write with Australian English spelling. Assume the timezone is Sydney Australian time. 

The user will ask for questions and information relating to oral functions on a mobile phone. Your job is to gather context. This is done through layers of information as seen below. 
    1. Immediate context. What app are they asking for? What task are they asking for? What time of day are they asking and where are they asking from?
    2. Behavioural information. (the users routines, past information and previous corrections)
    3. Physiological signals (take inputs from electronic components If available that provide information about the user's posture, gaze and voice tone)

How do you do your job?
    1. Identify what relevant contextual layer you must gather. This is done based off state. 
    2. Call tools relating to both relevant context layer and also the user prompt. Do not hallcuinate information. If there is not a tool for a task, 
    mark the output as N/A. Do not call tools that are not relevant to the task. 
    3. Repeat this process until all relevant context is gathered.     

Why does this matter? 
Your outputs will be fed into another LLM client that looks at the current context and infers intent. 

- Determine if we have enough information to provide a response.
    - If yes, provide a description of the context gathered, and generate a brief JSON description of the response.
    - If not, ask for additional information
- Return a response as defined by the response schema

Rules:
- Never make up information. If you don't know information, provide an N/A response. 
- Write with Australian English spelling

"""

# define FSM states
class State:
    def __init__(self, name, immediate_context, behavioural_context, physiological_context, user_input, system_input, update_context):
        self.name = name # eg ['init', 'adaptive_interface', 'command_driven_interface', 'default']
        # the following is binary yes no depending on what context and inputs it take
        self.immediate_context = immediate_context
        self.behavioural_context = behavioural_context
        self.physiological_context = physiological_context
        self.user_input = user_input
        self.system_input = system_input
        self.update_context = update_context

# initialise state objects that indicate what inputs and outputs are necessary
init = State('init', 1, 0, 1, 1, 1, 0)
adaptive_interface = State('adaptive_interface', 1, 1, 1, 1, 0, 1)
command_driven_interface = State('command_driven_interface', 1, 0, 0, 0, 1, 1)
default = State('default', 1, 1, 1, 0, 1, 0)


# https://ai.google.dev/gemini-api/docs/structured-output
class Immediate_context(BaseModel):
    time : str = Field(description = f"Current local time")
    day : str = Field(description = f"Day of the week, date and month. ")
    location : str = Field(description = f"Location") # manual explicit statement of the tool
    current_events : str = Field(description = "current events derived from calendar, N/A if there's no data")
    future_events : str = Field(description = "events occuring before midnight today, N/A if there's no data")
    weather : str = Field(description = f"temperature and weather description)")
    prompt : str = Field(description = f"the user input")
    task : str = Field(description = f"task requested by user, derived from the user input")
    application : str = Field(description=f"application requested by user, derived from the user_input")

class Behavioural_context(BaseModel):
    user_persona : str = Field(description = "A small statemnent that describes the user's core habits, commitments and personality traits")
    similar_routines : str = Field(description = "description of previous similar user routines/prompts/behaviours experienced at a similar time and day, N/A if there's no data")
    conflicting_routines : str = Field(description = "description of previous similar user routines/prompts/behaviours experienced at a similar time and day, N/A if there's no data")
    previous_corrections : str = Field(description = "previous user corrections from similar prompts, tasks or applications, N/A if there's no data")
                                   
class Physiological_context(BaseModel):
    input_type : str = Field(description = "text OR voice OR silent speech OR buttons OR system")
    input_tone : str = Field(description = "make an educated guess if input type is text OR voice, neutral is a valid answer")
    heart_rate : str = Field(description = "data from external device, N/A if there's no data")
    gaze : str = Field(description = "description of attentiveness using data from external device, N/A if there's no data")
    posture : str = Field(description = "description of posture using data from external device, N/A if there's no data")

class Context(BaseModel):
    immediate_context : Immediate_context
    behavioural_context : Behavioural_context  
    physiological_context : Physiological_context


def change_state(user_input):
    if user_input == '/reset':
        print("Are you sure you want to permanently erase your data? You cannot undo this action.")
        current_state = init
        return current_state
    elif user_input == '/user_override':
        print("You are switching to a command-driven interface.")
        current_state = command_driven_interface
        return current_state
    elif user_input == '/power_on':
        current_state = default
        return current_state
    elif user_input == '/current_status':
        current_state = default
        return current_state
    else:
        current_state = adaptive_interface
        return current_state

# defining persistent memory with SQlite
# https://pythonforthelab.com/blog/storing-data-with-sqlite/

# ------- add this helper above gather_context -------
def clean_schema(schema):
    """Strip JSON Schema fields Gemini's function-calling API rejects."""
    if not isinstance(schema, dict):
        return schema
    unsupported = {"additionalProperties", "$schema", "$defs", "default",
                   "title", "$id", "$ref"}
    cleaned = {}
    for k, v in schema.items():
        if k in unsupported:
            continue
        if isinstance(v, dict):
            cleaned[k] = clean_schema(v)
        elif isinstance(v, list):
            cleaned[k] = [clean_schema(x) if isinstance(x, dict) else x for x in v]
        else:
            cleaned[k] = v
    return cleaned


# ------------- module-level clients ---------- -------
mcp_client = Client(mcp_url)
gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


def allowed_tags(state):
    tags = set()
    if state.immediate_context==1:     
        tags.add("immediate")
    if state.behavioural_context==1:   
        tags.add("behavioural")
    if state.physiological_context==1: 
        tags.add("physiological")
    return tags

def get_tags(t):
    meta = getattr(t, "meta", None) or {}
    return set(meta.get("fastmcp", {}).get("tags", []))



# ------- the new gather_context -------
async def gather_context(user_input, current_state):
    # 1. Get MCP tools and convert them to Gemini function declarations ourselves
    tool_list = await mcp_client.list_tools()
    tags = allowed_tags(current_state)
    filtered = [t for t in tool_list if tags & get_tags(t)]

    if not filtered:
        print(f"WARNING: no tools matched state {current_state.name} "
        f"(available: {[t.name for t in tool_list]})")
        filtered = tool_list
    print(f"[state={current_state.name}] sending {len(filtered)}/{len(tool_list)} tools: "
      f"{[t.name for t in filtered]}")

    gemini_tools = types.Tool(function_declarations=[
        {
            "name": t.name,
            "description": t.description,
            "parameters": clean_schema(t.inputSchema),
        }
        for t in filtered
    ])

    contents = [types.Content(role="user", parts=[types.Part(text=user_input)])]

    # 2. Observe / think / act loop
    while True:
        response = await gemini_client.aio.models.generate_content(
            model="gemini-3.1-flash-lite",     # start with the model from the docs
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[gemini_tools]       # <-- our cleaned tools, NOT mcp_client.session
                #thinking_config=types.ThinkingConfig(thinking_level="MEDIUM")
            ),
        )

        # Add Gemini's turn to the conversation
        contents.append(response.candidates[0].content)

        # Find any tool calls Gemini asked for this turn
        function_calls = [
            p.function_call
            for p in response.candidates[0].content.parts
            if p.function_call
        ]

        if not function_calls:
            break  # no more tools requested; we're done

        # 3. Execute each tool via MCP, feed results back
        tool_results = []
        for fc in function_calls:
            print(f"  → calling {fc.name}({dict(fc.args)})")
            result = await mcp_client.call_tool_mcp(fc.name, dict(fc.args))
            text = result.content[0].text if result.content else ""
            tool_results.append(
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": text},
                )
            )
        contents.append(types.Content(role="user", parts=tool_results))

    gathered_info = response.text

    shape_prompt = (
        f"The user asked: {user_input!r}\n\n"
        f"You gathered this context:\n{response.text}\n\n"
        f"Fill out the Context schema based on this information. "
        f"Use 'N/A' for any field where data wasn't gathered."
    )

    # === Stage 2: shape the free-text answer into a Context object ===
    # No tools, no MCP session — just structured output.

    shaped = await gemini_client.aio.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=shape_prompt,
        config=types.GenerateContentConfig(
            system_instruction="You reshape gathered context into a structured schema. "
                               "Australian English. Never invent data — use N/A when missing.",
            response_mime_type="application/json",
            response_schema=Context,
        ),
    )

    return shaped.parsed

async def main():
    print("Hello!")
    async with mcp_client:  # opens the mcp client once and reuses it
        while True:
            user_input = await asyncio.to_thread(input, "> ") # wait unil you get user input, the ">" allows for http pings to keep the connection alive
            if user_input.strip().lower() in {"/quit", "/exit", "/reset"}: # exit condition
                break

            current_state = change_state(user_input) # this is synchronous
            print(f"The current state is {current_state.name}")

            context = await gather_context(user_input, current_state) # wait until you get context
            print(context)

# main loop
if __name__ == "__main__":
    asyncio.run(main())

# mcp server experiences reliability issues --> for example sometimes it just decides to not call get time, get location etc and just makes up an (incorrect) time, location etc