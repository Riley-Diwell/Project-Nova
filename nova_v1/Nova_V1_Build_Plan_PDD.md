# **NOVA V1 \- Build Plan (Prototype Design Document)**

> **Status:** Draft for team review · **Scope:** V1 *build* plan (not evaluation) ·  
> **Companion:** term definitions live in Appendix 1

---

## **1\. Purpose & scope**

NOVA V1 is a **proof-of-concept (POC) of an idealised system**. The idealised NOVA System is a discreet wearable that lets a university student handle everyday digital interactions **without taking out their phone**, in full service of three guiding principles \- **Attention, Autonomy, Privacy**. V1 does **not** build that end-state. V1 is a proof-of-concept for *the rest of the system* \- the pipeline, the memory stores, the agent, and the tools, using a hosted LLM and database as a stand-in for the on-device compute that isn't achievable by local-first alternatives.

**In scope for this document**

* The V1 software architecture and data flow.  
* Component responsibilities, interfaces/contracts, and per-component *definition of done*.  
* The seams that let three people build in parallel.  
* Build sequence and phasing.  
* Known risks and the path to V2.

**Out of scope for this document**

* **Evaluation / validation methodology** \- a separate downstream process. Components are validated as they are completed; whole-system validation runs under its own process.  
* **Peripheral form factor & industrial design** \- deliberately deferred (see Section 9). V1 simulates the peripheral with phone voice-in / audio-out.  
* **The idealised local-first / on-device build** \- that comes beyond V1(see Section 10).

---

## **2\. Concept & thesis**

**The problem.** The phone isn't slow \- it's an *attention trap*. You reach in for one  
thing and lose ten minutes. NOVA's reason to exist is **low attention cost per**  
**interaction**.

**The thesis.** NOVA both **reduces** the *volume* of interactions that reach the user  
(filtering/gating) and **relocates** the ones that survive onto NOVA, done **as well or**  
**better** than on the phone \- to the same standard, faster, and with lower attention cost.

**The three guiding principles.** Every function and decision is judged against:

| Principle | Meaning | Where it shows up in V1 |
| :---- | ----- | ----- |
| **Attention** | Intentional engagement, not compulsive interruption | Context-aware notification management; the whole low-attention voice paradigm |
| **Autonomy** | Understandable user control over autonomy & data | User-tunable **Controller Gain**; editable **Persona/Memory** via Knowledge Map |
| **Privacy** | Transparent, user-controlled personal data | Editable/exportable/deletable stores; local-first as the V2 target |

**Idealised vs V1**

| Aspect | Idealised NOVA | V1 (POC) |
| ----- | ----- | ----- |
| Compute | On-device LLM | **Hosted LLM (Claude)** as proxy |
| Storage | Local-first (REQ1.1) | **Supabase cloud** (opt-in-sync framing; see Appendix 2\) |
| Peripheral | Purpose-built device | **Phone simulates it** (voice in / audio out) |
| Goal | Privacy-complete product | **Prove the pipeline works** |

---

## **3\. System architecture**

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ANDROID APP (Kotlin)                — simulates the future peripheral     │
│  • Voice in:  button → SpeechRecognizer (STT)                             │
│  • Audio out: TextToSpeech (TTS) → phone speaker                          │
│  • State signals: sensors, calendar, DND/ringer, ActivityRecognition      │
│  • Knowledge Map UI (view/edit Persona & Memory)                          │
└───────────────┬───────────────────────────────────────────────────────────┘
                │  HTTPS (event + user-state snapshot + audio/text)
                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  BACKEND (Python / FastAPI, hosted cloud)                                 │
│                                                                           │
│   Event ─► State Estimator ─► INTENT SURFACE ──► Tool dispatch            │
│                (fuses signals)   (Claude tool-      │                     │
│                                   calling loop)     ▼                     │
│                                              ┌──────────────┐             │
│                                              │  MCP server  │  (1 total)  │
│                                              │  tools/      │             │
│                                              │   notif-mgmt │  1 package  │
│                                              │   notes      │  per func   │
│                                              │   calendar   │  (the seam) │
│                                              └──────┬───────┘             │
│                                                     │ per-tool GAIN       │
│   Memory (log outcome) ◄────────────────────────────┘  (reactive⇄proact) │
└───────────────┬───────────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SUPABASE (Postgres + pgvector)                                           │
│  • Persona (durable, vector)   • Memory (episodic)   • live User State    │
└─────────────────────────────────────────────────────────────────────────┘
```

**The pipeline (one pass):**

event 

→ user state estimated from all available signals

      	→ Intent Surface (Claude) infers intent

      	→ dispatches a Tool (reactive on request, or proactive per its Controller Gain)

      	→ Tool does the task off-phone (result spoken back via TTS)

      	→ outcome logged to Memory (feeds gain reinforcement \+ persona correction)

---

## **4\. Domain model**

All terminology is defined once in Appendix 1 and is the single source  
of truth. Key terms this doc assumes: *Attention Cost, User Persona, User State, Memory, Knowledge Map, Intent Surface, Event, Tool, Controller Gain, Grounding, Idealised System vs V1*. 

---

## **5\. Component specifications**

Each component below lists **responsibility**, **interface/contract**, and what **done** looks like.  
The contracts in **bold** are the frozen seams other components build against \- change them only by team agreement (see Section 6).

### **5.1 Voice I/O (Android) (Riley)**

* **Responsibility:** capture speech → text; speak text responses. Simulates the peripheral.  
* **Interface:** button press → STT string → `POST /event` ; response payload `{speech: string}` → TTS.  
* **Done when:** a spoken phrase round-trips to text, hits the backend, and a returned string is spoken aloud.

### **5.2 State Estimator (Android signals \+ backend fusion) (Georgia)**

* **Responsibility:** produce a **User State** estimate from *all available* signals \- full  
  sensor inference (accelerometer, location, ambient, screen state) **and** cheap signals (time, calendar, DND/ringer, Android `ActivityRecognition`).  
* **Interface:** **`UserState`**\*\* object\*\* \- `{activity, location_ctx, calendar_ctx, dnd, screen, timestamp, confidence}`. Attached to every event.  
* **Done when:** the app emits a populated `UserState` (with a confidence value) on each event; declared/simulated override available for demos.  
* **Note:** build cheap signals first, layer sensor inference on top (see Section 7\) to manage scope.

### **5.3 Intent Surface (backend, Claude tool-calling loop) (Georgia)**

* **Responsibility:** given `event + UserState`, infer intent and select/parameterise a Tool (explicit request → reactive; inferred need → proactive within gain).  
* **Interface:** **`POST /event {event, user_state}`**\*\* → \*\*\*\*`{speech, actions[]}`\*\*. Internally: system prompt \+ `UserState` \+ available MCP tools → Claude function-calling loop.  
* **Done when:** a spoken request is correctly routed to the right function's tool and a coherent spoken response is produced, end to end, for all three functions.

### **5.4 Persona store (Supabase pgvector) (Jay)**

* **Responsibility:** durable, hierarchical model of the user (opinions → likes → …). Vector-searchable. **No raw sensor data.**  
* **Interface:** **`persona.search(query)`**\*\* / **`persona.upsert(fact)`** / \*\*\*\*`persona.delete(id)`\*\*. Facts are grounded (indexicals dereferenced) before upsert.  
* **Done when:** a stated preference is stored, retrieved by semantic search, corrected by a later contradicting episode, and is visible/editable in the Knowledge Map.

### **5.5 Memory store (Supabase, episodic) (Jay)**

* **Responsibility:** append-only log of `{event, action, outcome}`; feeds gain reinforcement and persona correction.  
* **Interface:** **`memory.append(entry)`**\*\* / \*\*\*\*`memory.query(filter)`\*\*.  
* **Done when:** every interaction is logged; a logged outcome demonstrably (a) nudges a tool's gain and (b) can trigger a persona search+update.

### **5.6 Knowledge Map (Android UI) (Jay and Riley)**

* **Responsibility:** render Persona (and Memory) back to the user as a navigable, editable map \- the visible face of Agency & Privacy.  
* **Interface:** reads `persona.*` / `memory.*`; edits write back through the same APIs.  
* **Done when:** the user can browse, edit, and delete stored beliefs and see the change reflected in subsequent agent behaviour.

### **5.7 Tool / MCP interface \+ Controller Gain (Naoise)**

* **Responsibility:** the common contract every function's tools implement, plus the gain mechanism.  
* **Interface:** **each function \= one MCP server** exposing tools with a standard schema `{name, description, input_schema, gain}`. **Controller Gain** per tool: self-tuning via reinforcement from Memory outcomes (accept → up, reject → down), **user-overridable**. Low gain \= reactive (only on explicit request); high gain \= proactive.  
* **Done when:** a tool can be invoked reactively; a proactive invocation fires only when gain × state-confidence clears threshold; user override clamps it; reinforcement moves it.

### **5.8 Function modules (the Top 3\) (Ella**

| \# | Function | Reduces | Proves | Owner |
| ----- | ----- | ----- | ----- | ----- |
| **1 (hero)** | **Context-aware notification management** | unlock-and-scroll notification trap | Intent Surface \+ User State | *TBD* |
| **2** | **Note-taking \- remember & recall** | opening Notes / typing / search | Persona \+ Memory \+ Knowledge Map | *TBD* |
| **3** | **Calendar interface** (voice add/edit \+ proactive "leave now") | opening Calendar / Maps | Tools/MCP \+ proactivity \+ grounding | *TBD* |

Each function is an independent MCP server built on the shared platform; no two share files. Selection criteria that chose them: 

C1 thesis fit · 

C2 frequency×impact · 

C3a I/O accessibility · 

C3b implementability · 

**C4 pipeline coverage** (each exercises a different organ of the system so V1 proves the *whole* architecture).

### **5.9 Hardware interface and UI (Josh)**

* **Responsibility:**   
* **Interface:**   
* **Done when:** 

---

## **6\. Interfaces & seams (the parallel-work contract)**

Three people build in parallel **only** because these contracts are frozen up front. The  
platform team owns them; function owners consume them:

1. **`UserState`**\*\* object\*\* (Section 5.2) \- the shape of context every function receives.  
2. **`POST /event → {speech, actions[]}`** (Section 5.3) \- the app↔backend boundary.  
3. \*\*MCP tool schema \*\***`{name, description, input_schema, gain}`** (Section 5.7) \- how a function  
   plugs into the Intent Surface.  
4. **Store APIs** `persona.*` / `memory.*` (Section 5.4–5.5) \- how a function reads/writes memory.

**Rule:** a function owner may build freely behind these seams; changing a seam requires team sign-off. This is what prevents shared-file contention.

---

## **7\. Build sequence / phasing**

**Phase 0 \- Skeleton (platform team).** FastAPI app \+ `/event` stub that echoes; Android button→STT→POST→TTS round-trip; Supabase project \+ empty schemas. *Milestone: a spoken word comes back spoken.*

**Phase 1 \- Platform vertical slice.** Intent Surface (Claude loop) with **one** trivial  
tool; `UserState` from **cheap signals only**; Persona \+ Memory read/write; freeze the four seams (Section 6). 

*Milestone: "remember X" → stored → "what did I say about X" → recalled, spoken.*

**Phase 2 \- Fan out (3 owners in parallel).** Each builds their MCP server against the  
frozen seams: notification management, notes, calendar. Platform team layers **sensor-based State inference** on top of cheap signals, and implements **Controller Gain** \+ reinforcement. 

*Milestone: all three functions demoable reactively.*

**Phase 3 \- Proactivity & polish.** Turn on per-tool gain; Knowledge Map editing; grounding of indexicals; persona correction loop. *Milestone: a proactive action fires appropriately, is reflected in Memory, and moves the gain.*

**Definition of done for V1:** the three functions run end-to-end via voice; the three  
stores work and are user-editable; gain is self-tuning and overridable; the whole pipeline demonstrably reduces at least one real phone interaction per function.

---

## **8\. Ownership map**

| Area | Owner |
| ----- | ----- |
| Platform (backend, Intent Surface, seams, State fusion, gain) | *TBD* |
| Function 1 \- notification management | *TBD* |
| Function 2 \- note-taking | *TBD* |
| Function 3 \- calendar | *TBD* |
| Android app shell \+ Knowledge Map UI | *TBD* |
| Supabase schema & data-agency (export/delete) | *TBD* |

---

## **9\. Risks & open decisions**

| \# | Risk / open decision | Mitigation |
| ----- | ----- | ----- |
| R1 | **Peripheral form factor undecided** | V1 simulates via phone; doc defines the I/O *contract* so hardware slots in later without pipeline rework. |
| R2 | **State-inference scope creep** (full sensor fusion is a project in itself) | Phase it: cheap signals first (Phase 1), sensor inference layered later (Phase 2); demo fallback to declared state. |
| R3 | **Cloud vs local-first (REQ1.1 divergence)** | Stated as a POC limitation with a V2 path; see ADR-0001. |
| R4 | **Android notification/permission access** (NotificationListener, ActivityRecognition, calendar) | Validate permissions in Phase 0 as a spike before committing Function 1\. |
| R5 | **Proactive actions annoying users** | Gain starts low (reactive), earns proactivity via reinforcement; user override always available. |
| R6 | **Sending messages on user's behalf** (rejected from Top 3\) | Deliberately excluded from V1 to avoid the Android cross-app/permissions/privacy minefield. |

---

## **10\. Extending into V2 (path to the idealised system)**

V1 is a rung on the ladder, not the destination. The architecture is deliberately shaped so each step below swaps a V1 stand-in for its idealised counterpart **without redesign**:

* **Compute → local.** Replace the cloud Claude call behind the Intent Surface with an **on-device LLM** as small models mature. The `POST /event` seam and tool schema are unchanged; only the loop's backing model moves. (This is the core promise of the POC.)  
* **Storage → local-first.** Promote the on-device store to authoritative, demote Supabase to **opt-in sync** \- satisfying REQ1.1 (see ADR-0001).  
* **Peripheral → real hardware.** Replace phone voice-I/O with the chosen wearable; because V1 froze the I/O contract (Section 5.1), the device plugs into the same `{speech}`/event boundary.  
* **More functions.** Any candidate from the function menu becomes a new MCP server on the same seams \- no platform change.  
* **Richer state & gain.** Deeper sensor fusion and more sophisticated reinforcement for Controller Gain; the interfaces already accommodate a `confidence` signal.

---

## **Appendix 1 \- Project Context & Glossary**

The shared language for the NOVA project. This file is a glossary, not a spec.

## **Glossary**

### **NOVA**

An alternative computing peripheral (hardware) for university students. It lets a  
student handle everyday digital interactions **without** taking out their phone, in  
situations where using a phone is impractical, discouraged, or obscured (a lecture,  
phone in a pocket, socially inappropriate moments).

### **Attention Cost**

The amount of attention an interaction pulls from the user. NOVA's central design  
constraint: every task NOVA performs must cost **less** attention than doing the same  
task on a phone, while meeting the same standard of outcome. Low attention cost is the product, not a feature.

### **The NOVA Thesis (dual goal)**

NOVA both:

1. **Reduces** the *volume* of interactions that reach the user (filtering / gating), and  
2. **Relocates** the interactions that survive onto NOVA, done faster and more discreetly  
   than on a phone.

An interaction handled on NOVA should be **as good or better** than on the phone \-  
same task, same standard \- but faster, more convenient, and lower attention cost.

### **Obscured (context)**

The set of situations NOVA is designed for: when conventional phone interaction is  
blocked or undesirable \- in a lecture, phone buried/away, hands busy, or where pulling out a screen would be disruptive or draw attention.

### **Function**

A discrete capability NOVA can perform (e.g. "notification management", "automated text messages", "reminders"). The full candidate list lives in the team's "Top 3 Function Selection" sheet. Functions are agentic/LLM-backed where useful (suggested replies, summarisation, autonomous calendar edits, chatbot), likely implemented with MCPs / agent frameworks. V1 scope \= a small selected subset ("Top 3").

### **Top 3 Function Selection**

The team's current activity: scoring candidate Functions against criteria (C1/C2/C3) to choose the small set that V1 will actually build and evaluate.

### **Modality**

The channel through which a Function's input or output travels (e.g. haptic, audio/bone-conduction, voice, gesture, small display, capacitive touch). "Modality accessibility" \= whether the data and the I/O channel a Function needs are actually obtainable/buildable.

### **Companion App**

An Android app (Android Studio) \+ backend. Holds the user's accounts and connectivity, runs the agent pipeline, and pushes distilled results to the NOVA peripheral over BLE. The phone stays in the pocket, screen off \- used as compute/connectivity, never as a screen to interact with.

### **User Persona**

The durable, slow-changing model of *who the user is* \- opinions, preferences, routines, contacts, courses. Structured as a hierarchy/ontology (e.g. opinions → likes → food → \[bagels\]). Stored as vectors in Supabase (pgvector). Contains **no** raw sensor data. Fully transparent and editable by the user via the Knowledge Map.

### **User State**

The **live**, transient estimate of the user's current situation (in-lecture / walking /  
asleep / in-conversation), inferred from sensor data. Ephemeral \- feeds the Intent  
Surface, not persisted as its own store. Durable facts derived from it are *promoted* to Persona; interactions are *logged* to Memory. **V1 maximises signal**: full phone-sensor inference (accelerometer, location, ambient, screen state) *plus* cheap signals (time, calendar, DND/ringer, Android ActivityRecognition). Higher-confidence state enables higher Controller Gain (safer proactive action).

### **Memory (episodic)**

An append-only log of *what happened* \- past events, the action NOVA took, and the  
outcome. Written "when relevant." An episodic entry can trigger a **search \+ correction**  
of Persona (e.g. "this bagel is good, I was wrong" → find the bagel opinion → update it).  
Transparent and editable by the user.

### **Knowledge Map**

The UI that renders the User Persona (and Memory) back to the user as a navigable map,  
giving visibility and edit control over what NOVA believes about them.

### **Intent Surface**

The LLM-driven component that takes an **Event \+ current User State** and infers the  
user's **Intent**, then selects the Tool to enact it (the user's explicit request, or the  
best appropriate action given the inferred intent).

### **Event**

The trigger that starts the pipeline (e.g. an incoming notification, a sensor threshold, a  
user action on the NOVA peripheral, a time/calendar trigger). \[taxonomy still to define\]

### **Tool**

An action the agent invokes to enact an Intent. A NOVA Tool's job is to **remove a phone**  
**interaction** \- accomplish the task off-phone (often by delivering the result to the NOVA  
peripheral instead of the phone screen). Likely implemented via MCP.

### **Controller Gain (per-tool)**

A tunable parameter on each **Tool** (control-systems metaphor) that sets how strongly the  
tool acts on *inferred* intent without an explicit user request.

* **Low gain → Reactive:** the tool acts only when the user explicitly asks.  
* **High gain → Proactive:** the tool initiates on its own from inferred User State/Intent,  
  anticipating the user's need.  
  Gain is **per-tool** (calendar may run hot, notification management cooler). It is  
  **self-tuning via reinforcement** *and* **user-overridable**:  
* **Self-set (reinforcement):** a proactive action the user keeps/accepts raises that  
  tool's gain; one they reject/undo lowers it. The reinforcement signal comes from logged  
  outcomes in **Memory**.  
* **User override:** the user can manually set/clamp any tool's gain at any time.  
  This learned-default-plus-human-veto loop is the operational expression of the **Agency**  
  pillar. Higher-confidence User State lets the system safely act at higher effective gain.

### **Proactive vs Reactive action**

The two modes in which a Tool can fire. **Reactive** \= triggered by an explicit user  
request/intent. **Proactive** \= triggered by the system from inferred state/intent. The  
Controller Gain sets the balance/threshold between them per tool.

### **Grounding (indexical resolution)**

Before a transient, context-dependent reference ("today", "here", "this") can be stored  
durably in Persona, it is **dereferenced** using User State or an external tool call  
(e.g. "the weather today" → weather API → "sunny, 24°C"). Ungrounded indexicals stay in  
episodic Memory only.

### **NOVA Peripheral (form factor \- DEFERRED)**

The physical wearable a student operates while phone interaction is obscured. Form factor  
and primary I/O modality are **not yet decided**. This is an open decision the design doc  
must carry. Downstream decisions that depend on it: (a) which output/input modality each  
Function can use, and (b) where User-State sensors live (on the peripheral vs the phone).

### **The Three Pillars (Attention · Agency · Privacy)**

NOVA's core value proposition, from the project brief (S1): give the user "complete  
control and autonomy of one's attention, agency and privacy." Every Function and design  
decision is judged against these three.

* **Attention** \- intentional engagement, not compulsive/interrupted use (N1, N2, N5).  
* **Agency** \- understandable user control, boundaries, and modes of use (N3).  
* **Privacy** \- transparent personal-data control; the user can view/edit/add/delete/  
  export/control storage & processing of all their data (N4, REQ1).

### **Need / Requirement (formal)**

The project uses an INCOSE-TP-2010-006 requirements process. **Needs** (N1…) are  
stakeholder statements; **Requirements** (REQ1…) are traceable "shall" statements with  
success criteria and a verification method. The authoritative register is the team's  
"DRAFT Requirement List V2.0" sheet \- the design doc must stay consistent with it.

## **Project facts**

* Type: undergraduate **capstone engineering project**.  
  Team: Jay Horan, Naoise Michelin, Riley Diwell, Georgia Gill, Joshua Dunn, Ella Liu. Host/proxy stakeholder: Alex Ollman.  
* Formal INCOSE requirements register exists (Needs, Requirements, importance rankings).  
* Current stage: selecting the Top 3 Functions for V1.  
* V1 goal: a working prototype good enough to **evaluate** against the phone on the three  
  metrics \- same standard, faster, lower Attention Cost.  
* V1 POC I/O stand-in: **voice in** (in-app button → speech-to-text) \+ **audio out**  
  (phone speaker → TTS). The phone simulates the future NOVA peripheral. The interaction  
  paradigm being prototyped is therefore **conversational/voice**.  
* **Top 3 Functions for V1 (SELECTED):**  
  * **Context-aware notification management** (hero demo; Attention pillar; Intent Surface \+ User State)  
  * **Note-taking \- remember & recall** (Persona \+ Memory \+ Knowledge Map; Agency/Privacy)  
  * **Calendar interface** \- voice add/edit \+ proactive "leave now" (Tools/MCP \+ proactivity \+ grounding)  
    Selection criteria: C1 thesis fit · C2 frequency×impact · C3a I/O accessibility ·  
    C3b implementability · C4 **pipeline coverage** (the three must exercise different organs  
    of the system so V1 proves the *whole* architecture).  
* **Parallel ownership:** each of the Top 3 is built by a different team member. Therefore  
  the architecture must be a **shared platform \+ independent function modules behind clean**  
  **seams** (no shared-file contention). This is a hard design constraint on the plan.  
* **Scope of this doc:** the V1 **build plan** \- architecture, module boundaries,  
  interfaces/contracts, build sequence, per-component definition-of-done. **Out of scope:**  
  evaluation/validation methodology (a separate later process; components are validated as  
  completed, whole-system validation runs under its own process).  
* **V1 build stack (DECIDED):**  
  * Front end: **Android (Kotlin, Android Studio)**; voice in via `SpeechRecognizer`, audio  
    out via `TextToSpeech` (Android-native for V1).  
  * Backend: **Python (FastAPI)**, single API the app calls; hosted cloud for the POC.  
  * Data: **Supabase (Postgres \+ pgvector)** \- Persona (vectors), Memory (episodic), user data.  
  * Intelligence: **cloud LLM \= Claude (Anthropic)**; the **Intent Surface \= an LLM**  
    **tool-calling loop** (system prompt \+ User State → model selects a Tool → runs → spoken back).  
  * Tools: **one MCP server per Function** (each owned by a different team member \= the seam).  
* Open decisions: NOVA peripheral form factor & I/O modality.  
* **RESOLVED (was a conflict):** V1 is a **POC of an idealised system**. The *idealised*  
  NOVA is fully local & privacy-complete (satisfies REQ1.1 local-first). **V1** uses a  
  **cloud LLM \+ cloud infra (Supabase) as a temporary proxy** for local compute that isn't  
  good enough yet. The claim: the architecture is designed to go fully local once on-device  
  models mature; V1 proves the rest of the system works today. → ADR candidate.

### **Idealised System vs V1 (POC)**

* **Idealised NOVA** \- the target design: fully on-device LLM, local-first storage, all  
  three pillars fully satisfied. What the requirements register describes.  
* **V1 (POC)** \- proves the *system architecture and pipeline* (persona, intent surface,  
  memory, tools, peripheral I/O) work, using cloud LLM/infra as a stand-in for future  
  local compute. Its job is to validate the concept, not to be the privacy-complete product.  
  Where V1 diverges from the register (e.g. cloud vs local), the divergence is stated as a  
  POC limitation with a path to the idealised end-state.

---

## **Appendix 2 \- V1 is a cloud-backed POC of an idealised local-first system**

**Status:** Accepted · **Date:** 2026-07-23

## **Context**

NOVA's requirements register makes local-first, privacy-complete storage a **high-priority,**  
**host-driven** requirement:

* **REQ1.1** \- "The system shall employ **local-first** storage as the default data control  
  mechanism," with success criterion "no external sync occurring without explicit user action."  
* **REQ1.1.1** \- "≥ 8 GB local storage (target 16–32 GB)."  
* **REQ1** \- full data agency (view/edit/delete/export/control storage and processing).

Privacy is one of NOVA's three pillars, and this requirement cluster ranks among the highest  
in the pairwise importance ranking.

However, the V1 pipeline depends on an **agentic LLM** (Intent Surface), semantic **vector**  
**search** over the Persona, and internet-connected **Tools**. Running that fully on-device  
today is not feasible: on-device LLMs are not yet good enough for the reasoning quality the  
Intent Surface needs, and building local vector search \+ agent orchestration \+ sensor fusion  
inside a single capstone cycle would consume the whole project and still under-deliver.

A cloud LLM (Claude) and cloud storage (Supabase) are, on their face, "external processing  
and sync by default" \- a direct tension with REQ1.1.

## **Decision**

**V1 is a proof-of-concept of an *idealised* system.** The idealised NOVA is fully local and  
privacy-complete (satisfying REQ1.1). V1 uses a **cloud LLM \+ cloud infrastructure as a**  
**temporary stand-in** for the local compute that does not yet exist, in order to prove that  
*the rest of the system* \- the event→state→intent→tool→memory pipeline, the three stores,  
the Knowledge Map, per-tool Controller Gain \- works today.

The architecture is deliberately shaped so the cloud components can be swapped for local ones  
**without redesign** (see DESIGN.md Section 10): the `POST /event` seam and MCP tool schema are  
independent of whether the LLM runs in the cloud or on-device; the store APIs are independent  
of whether the authoritative copy is local or in Supabase.

Where V1 diverges from the register, the divergence is documented as a **POC limitation with**  
**a stated path to the idealised end-state**, not hidden.

## **Alternatives considered**

* **(A) Local-first for real in V1.** On-device LLM \+ local vector store, cloud never touched  
  by default. *Rejected:* on-device model quality is currently inadequate for the Intent  
  Surface, and the build effort would swamp the capstone while degrading the demo.  
* **(C) Amend/relax REQ1.1 to "cloud-first with transparency."** *Rejected:* the requirement  
  is host-driven and top-ranked; walking it back would weaken the Privacy pillar. The POC  
  framing keeps the requirement intact as the *target* rather than deleting it.

## **Consequences**

* **Positive:** the agent stays smart enough to prove the concept; the Privacy pillar survives  
  as an explicit design target; the swap-to-local path is a clean V2 story; markers see an  
  honest account of the trade-off rather than a hidden violation.  
* **Negative / accepted:** V1 does **not** satisfy REQ1.1 as built \- this must be stated  
  plainly in reporting. User data transits cloud services during the POC; data-agency  
  controls (export/delete/edit) are demonstrated against the cloud store as the V1  
  approximation of the idealised local controls.  
* **Reversal cost:** low by design \- the seams isolate the cloud dependency, so moving to  
  local compute/storage in V2 changes backing implementations, not interfaces.

