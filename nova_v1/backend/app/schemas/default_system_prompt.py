"""
schemas/default_system_prompt.py - Section 5.2: State Estimator  (Georgia)

STATUS: superseded

WHAT THIS FILE IS
A default system prompt for any call to an LLM to include in the system prompt. 
Intended to be used if there are multiple AI clients that need the same information. 
This is useful if there is a lot of the same information that needs to be passed to 
multiple clients. 

WHO USES THIS
- N/A

"""

default_system_prompt = "Please write with Australian-English spelling and grammar."