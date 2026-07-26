"""
intent_surface.py - Section 5.3: Intent surface (backend, Claude tool-calling loop) (Georgia)

STATUS: wip

WHAT THIS FILE IS
brief description
    1. 

    2. 


WHO USES THIS
- 



"""

# import necessary libraries
from anthropic import Anthropic
import os
from dotenv import load_dotenv
load_dotenv()

# import nova libraries
from schemas.default_system_prompt import default_system_prompt
from schemas.user_state import UserState

# set up ai parameters
client = Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
)

model = 'claude-haiku-4-5'

system_prompt = f"""
{default_system_prompt}

# Who are you?
You are the AI assistant for a new novel Android application that manages attention, autonomy and privacy. 
This is an event-based system, where events could be system events (eg location change, speed change etc) or user events,
(eg STT input, button press input etc). 

# Input
You will receieve an input in the form of {UserState}. Based on this user state, your job is to infer:
1. What the user may intend to do on their phone now
2. What the user may intend to do on their phone soon

# Categories of intent
1. Navigation
2. Note-taking
3. Edit and interface with the calendar
4. Other
4. N/A

# Rules:
- Never make up information. If you don't know information, provide an N/A response. 
- Call relevant tools if available to support your reaosning

"""

def infer_intent(user_input):
  response = client.messages.create(
      model=model,
      max_tokens=1024,
      #tools=[{"type": "web_search_20260209", "name": "web_search"}],
      messages=[{"role": "user", "content": user_input}],
      system=system_prompt
      #tool_choice={"type": "auto", "disable_parallel_tool_use": True}
  )
  print(response.content)
  return response.content

if __name__ == "__main__":
    print("Hello!")
    while True:
       user_input = input()
       intent = infer_intent(user_input)