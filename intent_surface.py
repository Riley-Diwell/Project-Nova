
# pip install google-genai
import os
from google import genai
from google.genai import types
import numpy as np
import pandas as pd
from dotenv import load_dotenv
import time

load_dotenv()

timestamp = time.localtime()

def generate(user_input):
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
    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="MEDIUM",
        ),
        system_instruction=[
            types.Part.from_text(text=
                                 
f"""You are the AI assistant for a new novel personal computing device that manages attention, autonomy and privacy. Please write with Australian English spelling. Assume the timezone is Sydney Australian time. 

The user will ask for questions and information relating to oral functions on a mobile phone. Your job is to determine the user’s intent. This is done through layers of information as seen below. 
    1. Immediate context. What app are they asking for? What task are they asking for? What time of day are they asking and where are they asking from?
    2. Behavioural information. (the users routines, past information and previous corrections)
    3. physiological signals (take inputs from electronic components If available that provide information about the user's posture, gaze and voice tone)

If input = /current_status: 
Your job is to return the following outputs. 
Why? The idea is you should be able to infer user intent based off a mixture of immediate context, behavioural context and physiological context without the user having to prompt anything. 
/current_status is a system prompt - for example, imagine you are scheduled to run /current_status every 10 minutes to update the UI. 

- Determine if we have enough information to provide a response.
    - If yes, provide a description of the context gathered, and generate a brief JSON description of the response.
    - If not, ask for additional information
- Return a response in JSON with the following format (IMPORTANT: All responses MUST be in this JSON format with no additional text or formatting):

Record the immediate context in the following JSON format: 

{{
'time': 'string: HH:MM AM/PM (derived from {timestamp})',
'today': 'string: day dd month (derived from {timestamp})',
'location': 'string: location, else N/A',
'current events': 'string: current event in calendar, else N/A',
'future events': 'string: events occuring before midnight today, else 'N/A'
'prompt': 'string: 'prompt',
'task': 'string: task requested by user',
'application': 'string: application requested by user'
}}

Record the behavioural context in the following JSON format: 

{{
'user routines at current time': 'string: description of previous user routines/prompts/behaviours experienced at HH:MM AM/PM (time derived from {timestamp}), else if no data, N/A',
'user routines at current day': 'string: description of previous user routines/prompts/behaviours experienced during day (day derived from {timestamp}), else if no data, N/A',
'past information': 'string: description of any relevant past information, else N/A',
'previous corrections': 'string: 'previous user corrections from similar prompts, else N/A'
}}

Record the physiological context in the following JSON format: 

{{
'input type: 'string: text OR voice OR silent speech OR buttons OR /current_status, else N/A',
'input tone': 'string: make an educated guess if input type is text OR voice, neutral is a valid answer. else N/A'
}}
"""
            )
        ],
    )
    
    
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        if text := chunk.text:
            print(text, end="")


if __name__ == "__main__":
    print("Hello!")
    while True:
        user_input = input()
        generate(user_input)

