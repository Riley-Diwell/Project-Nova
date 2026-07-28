# NOVA V1

Proof-of-concept implementation of the NOVA system.

## Structure

### backend
Python backend containing:
- State estimator
- Intent surface
- MCP tool interface
- Controller gain
- Memory
- Persona
- Schemas

### functions
Independent NOVA function modules:
- Function 1
- Function 2
- Function 3

### android
Android companion application:
- Voice input
- Text-to-speech output
- User interaction

### tests
Automated tests for components and integration.

### docs
documentation

## Running the backend locally

```
cd backend
.venv\Scripts\python.exe -m venv .venv        # first time only
.venv\Scripts\python.exe -m pip install -r requirements.txt   # first time only
cd app
..\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Copy `backend/.env.example` to `backend/.env` and fill in `ANTHROPIC_API_KEY`
(and Supabase/Maps keys as needed) first.

### Testing without the Android app

FastAPI serves an interactive test UI at **http://127.0.0.1:8000/docs** —
open it in a browser, expand `POST /event`, click "Try it out", paste the
contents of `backend/test_requests/sample_voice_event.json` into the body,
and hit Execute. No phone, emulator, or curl needed.

### Testing without spending Claude API credits

Set `NOVA_MOCK_LLM=1` in `backend/.env` and restart the server. `/event`
then skips the real Anthropic call and echoes back what it received, so you
can confirm the wire contract (schema validation, event/user_state parsing)
without touching the API. Unset it (or set to `0`) to go back to calling
Claude for real.
