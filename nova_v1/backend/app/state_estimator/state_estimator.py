"""
state_estimator/state_estimator.py - Section 5.2: State Estimator  (Georgia)

WHAT THIS FILE IS
brief description
1. 

WHO USES THIS
- 

"""

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

You will receive input in the format of {UserStateInput}. Your job is to look at the current user state and 
make provide a one sentence summary of the current user state. 
""" # debug, need to do more research how to 'classify' user state

# call ai client
def generate_response(user_input):
    response = client.responses.parse(
        model=model,
        instructions = system_prompt,
        text_format = UserStateOutput,
        input = user_input
    )
    print(response)
    return response

# test user_input_state


if __name__ == '__main__': # debug, integrate
    print("Hello!")
    user_input = input()
    generate_response(user_input)
    