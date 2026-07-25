"""
intent_surface.py - Section 5.3: Intent surface (backend, Claude tool-calling loop) (Georgia)

- Responsibility: given {event} + {UserState}, infer intent and select/parameterise 
  a Tool (explicit request → reactive; inferred need → proactive within gain).

- Interface: POST /event {event, user_state}** → ****{speech, actions[]}**. 
  Internally: system prompt + UserState + available MCP tools → **Claude** function-calling loop.

- Done when: a spoken request is correctly routed to the right function's tool 
  and a coherent spoken response is produced, end to end, for all three functions.


WHAT THIS FILE IS
brief description
    1. 

    2. 


WHO USES THIS
- 



"""

