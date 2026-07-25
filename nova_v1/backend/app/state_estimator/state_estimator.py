"""
state_estimator/state_estimator.py - Section 5.2: State Estimator  (Georgia)

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

You will receive input in the format of {UserStateInput}. Your job is to look at the current user state and 
provide a one sentence summary of the current user state. Please also assign a confidence score on this answer. 
Confidence should be based on how much data was available to you, and how believable the behaviour is. If you are
making any inferences about the user's activities or mental state, the confidence should be lower. 

Assign the user state to inferred_user_state and confidence to state_confidenc in the output schema. 
""" # debug, need to do more research how to 'classify' user state

# call ai client
def estimate_user_state(user_input):
    response = client.responses.parse(
        model=model,
        instructions = system_prompt,
        text_format = UserStateOutput,
        input = user_input
    )
   # response = client.completions.create(
    #    model=model,
     #   instructions = system_prompt,
        #text_format = UserStateOutput,
      #  input = user_input
    #)
    print(response.output_text)
    print("Response metadata:")
    return response

'''
def calculate_confidence_linear_probs(log_probs): # debug from https://gautam75.medium.com/unlocking-llm-confidence-through-logprobs-54b26ed1b48a accessed 25/07/2026
    linear_probs = np.round(np.exp(log_probs)*100,2)
    confidence = np.mean(linear_probs)
    return confidence  # closer to 100 indicates higher confidence

def calculate_log_probs(response):
    joint_logprob=0.0

    count=0
    for prob in response.metadata['logprobs']['content']:
        
        count+=1
        joint_logprob += np.round(np.exp(prob['logprob']) * 100,2)

    print("Joint prob:", np.round(joint_logprob/count , 2), "%")


def generate_response(user_input):
    return client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]#,
       # logprobs=True,
       # top_logprobs=5,   # optional; lets you inspect alternatives
    )

def sequence_confidence(response):
    tokens = response.choices[0].logprobs.content
    logprobs = np.array([t.logprob for t in tokens])
    # geometric mean of token probs = length-normalized joint probability
    return float(np.exp(logprobs.mean()))   # 0-1, higher = more confident
'''
if __name__ == '__main__': # debug, integrate
    print("Hello!")
    while True:
        user_input = input()
        user_state = estimate_user_state(user_input)
        #print(sequence_confidence)
