"""
state_estimator/state_estimator.py - Section 5.2: State Estimator  (Georgia)

STATUS: working draft

WHAT THIS FILE IS
brief description
1. 

WHO USES THIS
- 

"""

# debug python -m state_estimator.state_estimator

# import necessary libraries and set up environment
import os
from openai import OpenAI
from math import exp
import numpy as np
from dotenv import load_dotenv
load_dotenv()

# import nova libraries
from schemas.default_system_prompt import default_system_prompt
from schemas.user_state import UserStateInput, UserStateOutput

# set up ai parameters
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

model = 'gpt-5.6-luna'
system_prompt = f"""{default_system_prompt}

You will receive input in the format of {UserStateInput.model_json_schema()}. Your job is to look at the current user state and 
provide a one sentence summary of the current user state. Please also assign a confidence score between 0-1 for this answer. 
Confidence should be based on how much data was available to you, and how believable the behaviour is. If you are
making any inferences about the user's activities or mental state, the confidence should be lower. 

Assign the user state to inferred_user_state and confidence to state_confidence in the output schema. 
""" # debug, need to do more research how to 'classify' user state

# call ai client
def estimate_user_state(user_input):
    response = client.responses.parse(
        model=model,
        instructions = system_prompt,
        text_format = UserStateOutput,
        input = user_input
    )

    print(response.output_text)
    print("Response metadata:")
    return response

if __name__ == '__main__': # debug, integrate
    print("Hello!")
    while True:
        user_input = input()
        user_state = estimate_user_state(user_input)
        #print(sequence_confidence)