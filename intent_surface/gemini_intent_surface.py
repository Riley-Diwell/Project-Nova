
# pip install google-genai


# import necessary libraries
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv
import time

from pydantic import BaseModel, Field
from typing import List, Optional

from fastmcp import Client
import asyncio

# load environment where the API keys are stored
load_dotenv()

# mcp connection
#base_url = "http://localhost:8000/api"
#mcp_path = "/mcp"
mcp_url = "http://localhost:8000/mcp"

system_prompt =    """You are the AI assistant for a new novel personal computing device that manages attention, autonomy and privacy. Please write with Australian English spelling. Assume the timezone is Sydney Australian time. 

The user will ask for questions and information relating to oral functions on a mobile phone. Your job is to gather context. This is done through layers of information as seen below. 
    1. Immediate context. What app are they asking for? What task are they asking for? What time of day are they asking and where are they asking from?
    2. Behavioural information. (the users routines, past information and previous corrections)
    3. physiological signals (take inputs from electronic components If available that provide information about the user's posture, gaze and voice tone)

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

# define schema structure
# https://opper.ai/blog/schema-based-prompting

'''
{
    "input_schema": {
        "type": "object",
        "properties": {
            "immediate context": {
                "type": "object",
                "properties" : {
                    "task" : {"type":"string"},
                    "application" : {"type:application"}
                }



            }
        }
    },

    "output_schema": {
        "type": "object",
        "properties": {
            "immediate context": {
                "type": "object",
                "properties" : {
                    "task" : {"type":"string"},
                    "application" : {"type:string"},
                    "prompt" : {"type":"string"}
                }
    }, "required" : ["task", "application", "prompt"],
    "instructions": "Translate the input text to the target language.",
        }
    }
}
'''

# consider where I'm getting the tools - what already exists?
# can i host these tools on a server somewhere? create http request?
# check location() points to a server with all of our tools on it
# you can give pydantic all of the tools and it decides what to use --> i dont have to statically define it in different rows
# this is where the observe, think, act comes in --> it does this until it hits a stop condition
# anthropic has a framework that exposes all tools over a server and they all give the same schema of response
# request a service with a known schema 
# what protocols am I using? --> retrieve relavent context. MCP
# action: conpnect MCP servers to this 
# Jay is running a 4-bit model
# consider quantisation 
# this is the restriction of small local AI
# qwen still beats the original ChatGPT --> we need to focus on very specific instructions --> agentic harness
# benchmark against claude --> 30 tokens per second output
# look at google standards 

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


# define the AI function with the model, thinking level, API key and the system instruction
mcp_client = Client(mcp_url)
async def gather_context(user_input, current_state):
    client = genai.Client(
       api_key=os.environ.get("GEMINI_API_KEY")
    )

    model = "gemini-3.1-flash-lite"

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=user_input),
            ],
        ),
    ]
    response = await client.aio.models.generate_content( # changed from content stream to content, await
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
          #  response_mime_type = "application/json",
           # response_schema=Context,
            tools=[mcp_client.session],
            thinking_config=types.ThinkingConfig(thinking_level="MEDIUM")
            
        )
    )

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
            print(context.model_dump_json(indent=2))

# main loop
if __name__ == "__main__":
    asyncio.run(main())

# 2022 reAct paper
# observe, think, act
#  these can be nested/abstracted
# I have done this 
# know the state --> make a state estimate , get relavent context to update the state
# encouragement: where do behavioural and psychological things can come from
