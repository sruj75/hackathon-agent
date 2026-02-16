# Intentive v0 Implementation Plan
**Vision:** Proactive AI Companion for ADHD Executive Function Scaffolding  
**Target:** Ship v0 ASAP (Memory/Personalization → v1)  
**Status:** Midway - Core voice agent MVP exists, building toward full 24-hour session model

---
Key Insights 
1. Core Design Principle: Proactive External Scaffolding
The conversation nails the fundamental insight:

"User can't self-initiate... I am proactively doing it for the user"

This is not what your current system does yet. Right now, users open the app and talk to the agent. The vision is the agent calls them at 8 AM without prompting.

2. The "Virtual Human Assistant" Mental Model
"Think about a real human virtual assistant... what would they do?"

This framing is powerful for design decisions: Would a human VA have "moods"? No. Would they reschedule the entire day when one task overruns? Yes.

3. Personalization as Core Architectural Requirement
The strongest theme:

"Everything depends on the user... we need hyper-personalization"

The conversation identifies 3-layer memory architecture:

Explicit preferences (user-set rules)
Behavioral telemetry (response rates, drift patterns)
Learned policies (escalation thresholds)
This is missing from your MVP - currently it's stateless beyond the WebSocket session.

4. Dynamic Scheduling as the Hard Problem
The example of lunch shifting from 12→3 PM and cascading gym/meeting changes reveals the complexity. Your current agent can CRUD calendar events but doesn't have:



---

## Current State (What Exists)

✅ **Real-time voice agent** - Gemini Live API via ADK, bidirectional audio streaming  
✅ **Calendar tool** - Google Calendar CRUD via Composio (list, create, update, delete, find slots)  
✅ **Tasks tool** - Google Tasks CRUD via Composio (list, add, complete, update, goal linking)  
✅ **Generative UI** - Agent-driven component rendering (DayView, TodoList, CalendarView)  
✅ **Mobile app** - React Native/Expo with voice/chat/UI modes  
✅ **WebSocket backend** - FastAPI with session management (in-memory)

---

## v0 Vision Gap (What's Missing)

The current system is **reactive** (user initiates). v0 requires **proactive scaffolding**:

| Current MVP | v0 Target |
|-------------|-----------|
| User opens app → talks | Agent calls user at wake time |
| One-off conversations | 24-hour continuous session (wake → sleep) |
| Manual task execution | Scheduled check-ins throughout day |
| No dynamic replanning | Automatic schedule adjustment when plans change |
| Stateless (session only) | Day-persistent state |
| No personalization | User preference layer (channel, granularity, anchors) |
| Single-shot tool calls | Scaffolding loop (prompt → timer → check-in) |

---

## Agent Design Architecture

### Pattern: Single Continuous Agent with Self-Updating Playbook

**Not** multi-agent with meta-agent designer. **Not** mode-based (focus/calm/etc.).  
**IS** one agent that reads its playbook, executes behaviors, learns from outcomes, and updates its own playbook.

> **Design Philosophy:** Think of a human virtual PA. They don't have a "separate version of themselves" that watches them work and rewrites their manual. They just **update their own notes** as they learn. That's what this agent does.

```
┌──────────────────────────────────────────────────────────────────┐
│              SINGLE AGENT (Like a Human PA)                      │
│  - Wakes up each morning                                         │
│  - Reads playbook (yesterday's learnings)                        │
│  - Executes daily tasks using playbook insights                  │
│  - Learns from outcomes throughout the day                       │
│  - Updates own playbook end-of-day                               │
│  - Tomorrow: loads updated playbook, gets smarter                │
└────────────┬─────────────────────────────────────────────────────┘
             │
             │ uses
             │
    ┌────────┴────────┬──────────┬──────────┬───────────┐
    ▼                 ▼          ▼          ▼           ▼
┌─────────┐    ┌──────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│ MORNING │    │EXECUTION │ │ CHECK  │ │ DYNAMIC  │ │ PLAYBOOK │
│PLANNING │    │  LOOP    │ │  IN    │ │ REPLAN   │ │ (MEMORY) │
└─────────┘    └──────────┘ └────────┘ └──────────┘ └──────────┘
    │               │            │           │            │
    ├─ Brain dump   ├─ Set timer├─ Nudge   ├─ Conflict  ├─ Read: How to
    ├─ Prioritize   ├─ Wait     ├─ Call    ├─ Replan    │   work with user
    └─ Timeblock    └─ Resume   └─ Learn   └─ Tradeoff  └─ Write: New insights
```

**The "Machine That Builds The Machine" = One Machine That Improves Itself**

**Key Insight:** No separate "meta-agent" watching and rewriting behavior specs. The agent just updates its own damn playbook, like a human PA updates their notes.

| Robotic Skill | Human-Like Behavior |
|---------------|---------------------|
| Fixed script: "Time to work" | Contextual variants: "Ready for [task]?" / "Let's start tiny: [2-min step]" |
| One prompt per situation | Multiple variants, chooses based on context |
| Never adapts | Modifies approach after failure |
| Follows template | Purposeful: always trying to help user move forward |

**v0 Implementation:** Behaviors are **hand-written but parameterized** (e.g., tone, cadence, variants)  
**v1 Upgrade:** Add **Meta-Agent** that rewrites behaviors based on what works per user

**Why This Pattern?**
- ✅ Single write authority → No calendar conflicts from competing agents
- ✅ Modular → Can replace behavior implementation without touching orchestrator
- ✅ Testable → Each behavior has clear inputs/outputs
- ✅ Human-like → Contextual, adaptive, not robotic
- ✅ Extensible → Add new behaviors without architectural change
- ✅ Can evolve to Meta-Agent in v1 (self-improving behaviors)

---

## 🚨 CRITICAL: 24-Hour Session Architecture (Context Window Strategy)

### Mental Model: One Day = One Conversation Thread

**the core insight:**

```
One "session" = One 24-hour day = ONE continuous conversation thread with full context

Like Cursor/Antigravity chats:
- Current chat = one session
- New chat = new session (loses all previous context)

For Intentive:
- Morning (8 AM): "What's on your mind?"
- Late morning (11 AM): "How did deep work go?" ← remembers deep work from 8 AM
- Afternoon (2 PM): Check-in ← knows about morning tasks AND lunch
- Evening (10 PM): "How did your day go?" ← has FULL day context

The agent builds context through the regression loop all day.
```

**Why this is non-negotiable:**
- Evening reflection requires knowing what was planned in morning
- "How did it go?" assumes agent remembers the task
- Dynamic rescheduling needs to reference original commitments
- User shouldn't repeat information ("I told you I have a 2 PM meeting!")

This is **context engineering** - each interaction feeds into the LLM's growing context window for that day.

---

### The Implementation Challenge

**Problem:**
```
8:00 AM  → Planning conversation (15 min) → WebSocket closes
          ↓ [context = morning tasks, priorities, energy level]
          
11:00 AM → Check-in (3 min) → WebSocket reconnects
          ❓ How does agent remember 8 AM context?
          
2:00 PM  → Another check-in → WebSocket reconnects
          ❓ How does agent have context from 8 AM + 11 AM?
```

**Current v0 spec is INCOMPLETE** - it documents:
- ✅ Application state persistence (DB stores PLANNING/EXECUTING state)
- ✅ WebSocket connect/disconnect pattern
- ❌ **HOW context transfers between WebSocket sessions** ← UNDEFINED


**This decision blocks Phase 4 (Agent Behaviors)** - we can't write behavior prompts without knowing how context flows.

---

### Daily Session State Machine

The orchestrator runs this state machine, not individual conversations:

```
     ┌──────────────────> [INACTIVE] <──────────────────┐
     │                         │                         │
     │                         │ Wake time trigger       │
     │                         │ (8:00 AM)               │
     │                         ▼                         │
     │                   [DAY_START]                     │
     │                         │                         │
     │                         │ Initiate call/notif     │
     │                         │                         │
     │                         ▼                         │
     │                    [PLANNING]                     │
     │                         │                         │
     │         ┌───────────────┼───────────────┐         │
     │         │               │               │         │
     │    Brain dump      Prioritize      Timeblock      │
     │         │               │               │         │
     │         └───────────────┼───────────────┘         │
     │                         │                         │
     │                         │ User confirms plan      │
     │                         ▼                         │
     │     ┌─────────────> [EXECUTING] <──────┐          │
     │     │                   │               │          │
     │     │                   │ Task blocks   │          │
     │     │                   │ + Check-ins   │          │
     │     │                   │               │          │
     │     │                   │ Deviation!    │          │
     │     │                   ├──────────────>│          │
     │     │                   │            [REPLANNING]  │
     │     │                   │               │          │
     │     │                   │               │ Conflict │
     │     │                   │               │ detected │
     │     │                   │               │          │
     │     │                   │               │ Replan   │
     │     └───────────────────┼───────────────┘ algo     │
     │                         │                          │
     │                         │ Bedtime trigger          │
     │                         │ (10:00 PM)               │
     │                         ▼                          │
     │                   [DAY_CLOSE]                      │
     │                         │                          │
     │                         │ Reflection               │
     │                         │ Carryover                │
     │                         │                          │
     └─────────────────────────┘ Session ends
                         (tomorrow = new session)
```

**State Details:**

| State | Duration | Agent Actions | User Actions |
|-------|----------|---------------|--------------|
| **INACTIVE** | Overnight | Wait for wake trigger | Sleeping |
| **DAY_START** | 1 min | Send call/notification | Answer |
| **PLANNING** | 10-20 min | Facilitate brain dump → plan | Collaborate on plan |
| **EXECUTING** | All day | Check-ins, prompts, timers | Execute tasks |
| **REPLANNING** | 5-10 min | Detect conflicts, propose options | Choose tradeoffs |
| **DAY_CLOSE** | 10 min | Reflect, tag carryover | Review day |

---

### Scaffolding Loop (Execution Detail)

The **EXECUTING** state is not passive waiting. It's an active loop:

```
    ┌─────────────────────────────────────────────────────┐
    │                SCAFFOLDING LOOP                     │
    └─────────────────────────────────────────────────────┘
              │
              │ Start of task block
              ▼
    ┌──────────────────┐
    │  1. PROMPT       │  "Time to start [TASK]. Ready?"
    └────────┬─────────┘
             │
             ▼
    ┌──────────────────┐
    │  2. MICRO-CLARIFY│  "What's the first 2-minute step?"
    └────────┬─────────┘  (Reduces initiation friction)
             │
             ▼
    ┌──────────────────┐
    │  3. TIMEBOX      │  "I'll check in at [TIME]."
    └────────┬─────────┘  Agent sets internal timer
             │
             │ Timer running...
             │ User working...
             │
             ▼
    ┌──────────────────┐
    │  4. CHECK-IN     │  Agent calls/notifies at timer end
    └────────┬─────────┘  "How did [TASK] go?"
             │
             ├──────────┬──────────┬──────────┐
             │          │          │          │
             ▼          ▼          ▼          ▼
         [DONE]    [PARTIAL]   [STUCK]   [OVERRUN]
             │          │          │          │
             │          │          │          │
             ▼          ▼          │          ▼
    ┌──────────────────┐          │   ┌──────────────┐
    │  5a. TRANSITION  │          │   │ 5b. REPLAN   │
    │  to next block   │          │   │ (Dynamic)    │
    └────────┬─────────┘          │   └──────┬───────┘
             │                    │          │
             │ If more blocks     │          │
             │ remain today       │          │
             │                    │          │
             └────────────────────┴──────────┘
                        │
                        │ Loop continues
                        │
                        ▼
               Next task block begins
                   (repeat loop)
```

**What This Solves (ADHD-Specific):**
- **Task Initiation:** Agent prompts externally → user doesn't self-initiate
- **Time Blindness:** Check-ins prevent "lost 3 hours scrolling"
- **Hyperfocus Protection:** Forces breaks for meals/transitions
- **Planning Fallacy:** Micro-clarify reveals if estimate was wrong
- **Avoidance:** Agent persists proactively vs user procrastinating

---

### Attention Router (How Agent Gets User's Attention)

Policy-driven decision tree, not "moods":

```
                    ┌────────────────────┐
                    │ Need to reach user │
                    └──────────┬─────────┘
                               │
                    What type of check-in?
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
          ▼                    ▼                    ▼
    [CRITICAL]           [NORMAL]            [LOW-PRIORITY]
    Meal, Medicine       Task block end      Optional nudge
    Bedtime              Transition          
          │                    │                    │
          │                    │                    │
          ▼                    ▼                    ▼
    ┌─────────┐          ┌─────────┐         ┌──────────┐
    │  CALL   │          │ CHECK   │         │ SILENT   │
    │ Always  │          │ CONTEXT │         │ PASS     │
    └─────────┘          └────┬────┘         └──────────┘
                              │
                   User in meeting/busy?
                   (Check calendar)
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
                [BUSY]              [FREE]
                    │                   │
                    │                   │
                    ▼                   ▼
            ┌──────────────┐    ┌─────────────┐
            │ NOTIFICATION │    │ Check User  │
            │   (Soft)     │    │  Preference │
            └──────────────┘    └──────┬──────┘
                                       │
                            ┌──────────┴──────────┐
                            │                     │
                            ▼                     ▼
                    User prefers          User prefers
                    NOTIFICATIONS         CALLS
                            │                     │
                            ▼                     ▼
                    ┌──────────────┐      ┌──────────┐
                    │NOTIFICATION  │      │  CALL    │
                    │  (Primary)   │      │ (Primary)│
                    └──────┬───────┘      └──────────┘
                           │
                  Escalation if ignored
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │ Wait 5 min → Notification again      │
        │ Wait 5 min → CALL                    │
        │ No answer  → PAUSE (retry in 15 min) │
        └──────────────────────────────────────┘
```

**v0 Implementation:** Simple rule-based (if/else)  
**v1 Upgrade:** Learn optimal channel per user via response rate telemetry

---

### Dynamic Rescheduling Algorithm

When task overruns or user requests change:

```
┌───────────────────────────────────────────────────────────────┐
│  USER: "I need 2 more hours for this work"                    │
└─────────────────────────┬─────────────────────────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 1. CAPTURE NEW CONSTRAINT       │
          │    Current task needs +2 hours  │
          └───────────────┬─────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 2. GET CALENDAR SNAPSHOT        │
          │    Fetch all remaining events   │
          └───────────────┬─────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 3. CLASSIFY BLOCKS              │
          └───────────────┬─────────────────┘
                          │
          ┌───────────────┼────────────────┐
          │               │                │
          ▼               ▼                ▼
    [IMMUTABLE]      [FLEXIBLE]      [HEALTH ANCHORS]
    Meetings with    Personal        Meals, sleep,
    other people     tasks           medicine
          │               │                │
          └───────────────┼────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 4. COMPUTE CONFLICTS            │
          │    Which blocks now overlap?    │
          └───────────────┬─────────────────┘
                          │
          Example: Lunch (12 PM) → Now overlaps with extended work
                   Meeting (4 PM) → Now in old gym slot (7 PM)
                   Gym (7 PM) → Conflicts with meeting
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 5. APPLY PRIORITY, agent is smart enough learn user to a point it understands the priorities.                       │
          │                                 │
          └───────────────┬─────────────────┘
                          │
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 6. GENERATE OPTIONS             │
          └───────────────┬─────────────────┘
                          │
          ┌───────────────┼────────────────┐
          │               │                │
          ▼               ▼                ▼
    [OPTION A]      [OPTION B]       [OPTION C]
    Skip gym,       Keep gym,        Keep gym,
    preserve        preserve         delay sleep
    sleep           sleep,           1 hour
                    order food
                    (save cooking)
          │               │                │
          └───────────────┼────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 7. PRESENT TO USER              │
          │ "Here are your options..."     │
          │ Recommend Option B (best fit)   │
          └───────────────┬─────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 8. USER CHOOSES                 │
          │ Confirms                        │
          └───────────────┬─────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 9. UPDATE CALENDAR              │
          │ - Lunch: 12 PM → 3 PM           │
          │ - Meeting: stays 4 PM           │
          │ - Gym: 7 PM (unchanged)         │
          │ - Dinner: Cook → Order (note)   │
          └───────────────┬─────────────────┘
                          │
                          ▼
          ┌─────────────────────────────────┐
          │ 10. SHOW UPDATED DAY_VIEW       │
          │     Trigger generative_ui       │
          └─────────────────────────────────┘
```

---

### Memory Architecture (v1 Preview)

Even though deferred to v1, worth visualizing now for architectural planning:

```
┌─────────────────────────────────────────────────────────────┐
│                    MEMORY SYSTEM (v1)                       │
└─────────────────────────────────────────────────────────────┘
           │
           │ 3-Layer Architecture
           │
    ┌──────┴───────┬────────────────┬─────────────────┐
    │              │                │                 │
    ▼              ▼                ▼                 ▼
┌─────────┐  ┌──────────┐  ┌──────────────┐  ┌─────────────┐
│EXPLICIT │  │BEHAVIORAL│  │ PROCEDURAL   │  │ TOOLING     │
│PREFS    │  │ MODEL    │  │ POLICY       │  │ (v1)        │
└─────────┘  └──────────┘  └──────────────┘  └─────────────┘
     │            │              │                   │
     │            │              │                   │
User-set    Learned from    Derived from      mem0, zep,
manual      telemetry       prefs + behavior  supermemory
     │            │              │                   │
     ▼            ▼              ▼                   ▼
┌─────────────────────────────────────────────────────────────┐
│ EXAMPLES                                                    │
├─────────────────────────────────────────────────────────────┤
│ • Wake: 8 AM    │ • Notification    │ • If ignored   │ Vector│
│ • Channel:      │   response: 40%   │   2x → call    │ search│
│   CALL          │ • Call response:  │ • If busy →    │ for   │
│ • Granular:     │   95%             │   notification │ past  │
│   MEDIUM        │ • Task overrun:   │ • If critical  │ convos│
│ • Sleep>Gym     │   avg +30 min     │   → always call│       │
│                 │ • Best check-in:  │                │       │
│                 │   10 AM, 3 PM     │                │       │
└─────────────────────────────────────────────────────────────┘
        │                  │                    │
        └──────────────────┼────────────────────┘
                           │
                      Fed into
                           │
                           ▼
              ┌───────────────────────┐
              │  ATTENTION ROUTER     │
              │  decides: call or     │
              │  notification?        │
              └───────────────────────┘
```

**v0:** Only uses **Explicit Prefs** (user manually configures)  
**v1:** Adds **Behavioral Model** (learns from response patterns) + **Memory Tools** (long-term context)

---

### Tool Execution Flow

How the orchestrator uses tools during conversation:

```
     USER SPEAKS
         │
         │ "Schedule meeting at 2 PM tomorrow"
         │
         ▼
┌─────────────────┐
│  GEMINI LIVE    │  (Understands intent)
│  via ADK        │
└────────┬────────┘
         │
         │ Decides to call calendar_tool.create()
         │
         ▼
┌──────────────────────────────────────────────┐
│  ORCHESTRATOR                                │
│  (agent.py - main loop)                      │
├──────────────────────────────────────────────┤
│  1. Receives function call request           │
│  2. Routes to appropriate tool               │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  TOOL: calendar_tool.create()                │
│  (composio_tools.py)                         │
├──────────────────────────────────────────────┤
│  1. Parse parameters                         │
│  2. Call Composio SDK                        │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  COMPOSIO SDK                                │
│  (external service)                          │
├──────────────────────────────────────────────┤
│  1. Authenticate with Google                 │
│  2. Call Google Calendar API                 │
│  3. Create event                             │
└────────┬─────────────────────────────────────┘
         │
         │ Returns: {event_id, success}
         │
         ▼
┌──────────────────────────────────────────────┐
│  TOOL returns result to orchestrator         │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  ORCHESTRATOR                                │
│  Decides next action                         │
├──────────────────────────────────────────────┤
│  "Should I show the user the calendar?"     │
└────────┬─────────────────────────────────────┘
         │
         │ Calls generative_ui tool
         │
         ▼
┌──────────────────────────────────────────────┐
│  TOOL: generative_ui("calendar_view")        │
│  (render_ui_tools.py)                        │
├──────────────────────────────────────────────┤
│  1. Prepare props (events list)              │
│  2. Send WebSocket message to frontend       │
└────────┬─────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│  FRONTEND                                    │
│  (useWebSocketAgent.ts)                      │
├──────────────────────────────────────────────┤
│  1. Receives {type: "generative_ui", ...}   │
│  2. Inflates CalendarView component          │
│  3. Renders to screen                        │
└──────────────────────────────────────────────┘
         │
         ▼
     USER SEES CALENDAR
```

**Parallel Flow:** While UI renders, Gemini generates audio response:
```
"I've scheduled your meeting for 2 PM tomorrow."
  │
  ▼
Audio chunks stream to frontend → Play simultaneously with UI update
```

---

### Morning Planning Flow (Detailed Protocol)

```
    WAKE TIME (8:00 AM)
         │
         │ Backend scheduler triggers
         │
         ▼
┌──────────────────────────────────┐
│  ATTENTION ROUTER                │
│  Decision: Call or Notification? │
└────────┬─────────────────────────┘
         │
         ▼
    ┌────┴─────┐
    │          │
    ▼          ▼
 [CALL]   [NOTIFICATION]
    │          │
    └────┬─────┘
         │
         │ User responds
         │
         ▼
┌──────────────────────────────────┐
│  STATE: DAY_START → PLANNING     │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ PHASE 1: GREETING                │
│ "Good morning! How are you       │
│  feeling today?"                 │
└────────┬─────────────────────────┘
         │
         │ Energy check, constraints
         │
         ▼
┌──────────────────────────────────┐
│ PHASE 2: BRAIN DUMP              │
│ "What's on your mind for today?" │
│ "Just tell me everything..."     │
└────────┬─────────────────────────┘
         │
         │ User verbalizes all tasks
         │ Agent listens, captures
         │
         ▼
┌──────────────────────────────────┐
│ PHASE 3: PRIORITIZATION          │
│ "Which of these moves the        │
│  needle for your goals?"         │
└────────┬─────────────────────────┘
         │
         │ Identify top 3 critical tasks
         │ Tag others as secondary
         │
         ▼
┌──────────────────────────────────┐
│ PHASE 4: TIMEBOXING              │
│ "Let's schedule these..."       │
│ • Task A: 9-11 AM                │
│ • Lunch: 12-1 PM                 │
│ • Task B: 1-3 PM                 │
│ • Gym: 7-8 PM                    │
└────────┬─────────────────────────┘
         │
         │ Agent calls calendar_tool.create()
         │ for each block
         │
         ▼
┌──────────────────────────────────┐
│ PHASE 5: CONFIRMATION            │
│ "Here's your plan..."           │
│ [Trigger generative_ui(day_view)]│
│ "Does this look good?"          │
└────────┬─────────────────────────┘
         │
         │ User confirms or adjusts
         │
         ▼
┌──────────────────────────────────┐
│ PHASE 6: FIRST ACTION            │
│ "What's the first 2-min step     │
│  to start Task A?"               │
└────────┬─────────────────────────┘
         │
         │ Reduce initiation friction
         │
         ▼
┌──────────────────────────────────┐
│ TRANSITION TO EXECUTING          │
│ "I'll check in at 11 AM."       │
│ [Agent sets timer for 2 hours]   │
└──────────────────────────────────┘
         │
         │ STATE: PLANNING → EXECUTING
         │
         ▼
    User starts day
    Agent waits for timer
```

**Output Artifacts:**
- ✅ Calendar populated with time blocks
- ✅ Tasks list updated with priorities
- ✅ First check-in timer scheduled
- ✅ Session state = EXECUTING

---

### Check-in Cycle (Execution Phase Detail)

```
    TIMER EXPIRES (e.g., 11:00 AM)
         │
         ▼
┌──────────────────────────────────┐
│  Backend scheduler fires         │
│  check_in event                  │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│  ATTENTION ROUTER                │
│  Decide: Call, notif, or skip?   │
└────────┬─────────────────────────┘
         │
         ▼
    Agent reaches user
         │
         ▼
┌──────────────────────────────────┐
│  "How did [TASK] go?"            │
│  Listen to response              │
└────────┬─────────────────────────┘
         │
         │ User answers
         │
    ┌────┴─────┬─────────┬─────────┐
    │          │         │         │
    ▼          ▼         ▼         ▼
[DONE]   [PARTIAL]  [STUCK]  [OVERRUN]
    │          │         │         │
    ▼          ▼         │         ▼
┌────────┐ ┌────────┐  │  ┌──────────┐
│Mark    │ │Update  │  │  │ TRIGGER  │
│complete│ │progress│  │  │ REPLAN   │
│in tasks│ │note    │  │  └────┬─────┘
└───┬────┘ └───┬────┘  │       │
    │          │        │  State change
    │          │        │  EXECUTING →
    │          │        │  REPLANNING
    └──────────┴────────┘       │
                │                │
                ▼                ▼
         ┌──────────────────────────┐
         │ If STUCK:                │
         │ "What's blocking you?"  │
         │ Offer help/adjustment    │
         └──────────┬───────────────┘
                    │
                    ▼
         ┌──────────────────────────┐
         │ "What's next on the      │
         │  schedule?"              │
         └──────────┬───────────────┘
                    │
         Fetch next calendar block
                    │
                    ▼
         ┌──────────────────────────┐
         │ TRANSITION PROTOCOL      │
         │ "Now it's time for [X].  │
         │  Ready to start?"        │
         └──────────┬───────────────┘
                    │
                    ▼
         ┌──────────────────────────┐
         │ Set next timer           │
         │ "I'll check in at [TIME]"│
         └──────────────────────────┘
                    │
                    ▼
              Loop continues
         (Repeat for all blocks)
```

---

### Agent Architecture Evolution: v0 → v1 → v2

The conversation explored this progression:

```
MULTI-AGENT SWARM (Rejected for v0 - too complex)
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ PLANNER     │  │ SCHEDULER   │  │ COACH       │
│ AGENT       │  │ AGENT       │  │ AGENT       │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       │ Coordination overhead!          │
       │ Who owns calendar writes?       │
       └────────────────┼────────────────┘
                        │
                   Complexity ↑
                   Race conditions ↑


vs


v0: ORCHESTRATOR + BEHAVIORS (Ship This)
         ┌──────────────────┐
         │  ORCHESTRATOR    │ ← Single authority
         │  (One Agent)     │
         └─────────┬────────┘
                   │
       ┌───────────┼───────────┐
       │           │           │
       ▼           ▼           ▼
  [Planner]  [Scheduler]  [Prompter]
  Behavior   Behavior     Behavior
   
   Hand-written, parameterized behaviors
   No self-modification yet


vs
              │
              ├─ Analyzes telemetry
              ├─ Updates operating_manual.md
              ├─ Rewrites behavior_specs.md
              └─ Creates/retires behaviors
   
   System learns what works per user
   Self-improving behaviors
```

**Why Orchestrator + Behaviors for v0:**
1. **No coordination needed** - One brain, executes behaviors
2. **Clear write authority** - No calendar conflicts
3. **Easier to debug** - Single execution trace
4. **Human-like** - Behaviors have variants, context-aware
5. **Can upgrade to v1** - Add Meta-Agent layer later
6. **Follows product vision** - "Single orchestrator + behaviors that can be rewritten"

**v0 → v1 Migration Path:**
- v0: Hand-write `operating_manual.md` template, orchestrator follows it
- v1: rewrites it based on telemetry
- System becomes self-improving without architectural overhaul

**Future (v2+):** Could split into:
- Planning Agent (morning/evening)
- Execution Agent (check-ins, prompts)
- Replan Agent (dynamic scheduling)
- Meta-Agent (coordinates and tunes all agents)

But only if scale demands it.

---

## v0 Core Requirements (From Product Vision)

### FR-V0-01: 24-Hour Session Model
**Status:** 🔴 Not Implemented  
**Description:** Sessions span wake → sleep, not conversation-scoped. New session starts each day at user wake time.

**Implementation Needs:**
- Backend: Session lifecycle management (DAY_START → PLAN → EXECUTE → REPLAN → DAY_CLOSE)
- Database: Persist session state across server restarts (replace in-memory)
- Scheduler: Trigger morning kickoff automatically

---

### FR-V0-02: Proactive Initiation (Agent Calls First)
**Status:** 🔴 Not Implemented  
**Description:** Agent initiates contact via calls/notifications, not user.

**Implementation Needs:**
- **Morning Kickoff:** Agent triggers call/notification at user wake time
- **Check-in Loop:** Agent sets internal timers and initiates next check-in after task blocks
- **Attention Router:** Decision engine for call vs push notification vs escalation ladder

**Technical Components:**
1. **Push Notifications:** iOS native module for scheduled local notifications
2. **Calling Mechanism:** In-app VoIP calling (CallKit/PushKit) to initiate voice session
3. **Timer Service:** Backend scheduler to track task blocks and trigger check-ins

---

### FR-V0-03: Morning Planning Session
**Status:** 🟡 Partial (tools exist, flow doesn't)  
**Description:** Daily kickoff conversation to brain dump → prioritize → timeblock.

**Current:** Agent can CRUD calendar/tasks on demand  
**Missing:** Structured planning flow with explicit phases

**Implementation Needs:**
- Agent prompt: Add "Morning Planning Protocol" instructions
- Flow states: Greeting → Brain Dump → Prioritization → Timeboxing → Confirmation → First Action
- Output validation: Ensure calendar blocks + task list are updated before ending planning

---

### FR-V0-04: Scaffolding Loop (Task Execution Support)
**Status:** 🔴 Not Implemented  
**Description:** Agent shepherds user through each task with: Prompt → Timebox → Check-in → Transition.

**Implementation Needs:**
1. **Task Blocks:** Calendar events tagged as "work blocks" with timers
2. **Check-in Triggers:** Backend scheduler fires at block end times
3. **Transition Protocol:** Agent asks "Done/Partial/Stuck?" → moves to next block
4. **Context Preservation:** Agent remembers current task across check-ins

**Example Flow:**
```
9:00 AM → "Time to start deep work on project X. I'll check in at 10:30."
         → Set timer for 90 minutes
10:30 AM → Agent calls: "How did the work block go?"
         → Update task status → Prompt next block
```

---

### FR-V0-05: Dynamic Rescheduling
**Status:** 🔴 Not Implemented  
**Description:** When reality deviates (task overruns, new urgent item), agent replans the day.

**Implementation Needs:**
- **Conflict Detection:** Identify when current task exceeds timeboxed duration
- **Replan Algorithm:**
  1. Protect fixed blocks (meetings with others)
  2. Protect health anchors (meals, sleep) based on user priorities
  3. Push flexible blocks later or compress
  4. Propose tradeoffs (skip gym vs order food vs delay bedtime)
- **Confirmation Gate:** Agent presents new plan, user approves before updating calendar

**Example (from vision):**
```
Lunch planned 12 PM → work overruns → agent proposes:
- Shift lunch to 3 PM
- Meeting moves to 7 PM (gym slot)
- Options: Skip gym OR keep gym + order dinner (save cooking time)
```

---

### FR-V0-06: End-of-Day Reflection
**Status:** 🔴 Not Implemented  
**Description:** Bedtime closeout to review day + prep tomorrow.

**Implementation Needs:**
- Agent prompt: Add "Evening Reflection Protocol"
- Trigger: Scheduled at user bedtime target (e.g., 10 PM)
- Flow: What completed → What derailed → What worked → Carryover tasks → Tomorrow wake time
- Output: Tag incomplete tasks for next day, update wake alarm

---

### FR-V0-07: User Preference Layer
**Status:** 🔴 Not Implemented  
**Description:** Explicit user settings for personalization (v0 = manual config, v1 = learned).

**v0 Preferences (User-Set):**
- **Wake Time:** Daily session start (e.g., 8:00 AM)
- **Bedtime Target:** Daily session end (e.g., 10:00 PM)
- **Communication Channel:** Call-first vs Notification-first
- **Granularity Level:** LOW (coarse blocks) / MEDIUM (hourly) / HIGH (30min)
- **Health Anchors:** Non-negotiable blocks (sleep window, meals, medication)
- **Do Not Disturb Windows:** Calendar-based or manual (e.g., "no calls during meetings")

**Storage:** Simple JSON config per user (in DB), loaded at session start

---

### FR-V0-08: Attention Router
**Status:** 🔴 Not Implemented  
**Description:** Policy engine to decide how to reach user (notification vs call vs escalation).

**v0 Logic (Rule-Based):**
```python
def get_attention_channel(user, check_in_type):
    # If user preference is call-first
    if user.pref.channel == "CALL":
        return "call"
    
    # If critical anchor (meal, medicine, bedtime)
    if check_in_type in ["meal", "sleep", "medicine"]:
        return "call"
    
    # If calendar shows busy/in meeting
    if user.calendar.is_busy(now):
        return "notification"  # Soft nudge
    
    # Default: notification → call escalation
    return "notification"
```

**Escalation Ladder (if notification ignored):**
1. Push notification
2. Wait 5 min → Push again
3. Wait 5 min → Call
4. No answer → Pause mode (retry in 15 min)

---

### FR-V0-09: Internal Timer System
**Status:** 🔴 Not Implemented  
**Description:** Backend scheduler to track task blocks and trigger check-ins.

**Implementation Needs:**
- **Option A:** Use APScheduler or Celery Beat for Python-based scheduling
- **Option B:** Store "next_checkin" timestamp in session, poll every minute
- **Option C:** iOS native background tasks + server-triggered notifications

**Data Model:**
```python
class CheckInSchedule:
    session_id: str
    next_checkin_time: datetime
    checkin_type: str  # "task_block_end" | "meal" | "transition"
    context: dict      # Current task details for agent
```

---

## v0 Architecture Changes

### Backend State Management
**Current:** In-memory sessions (cleared on restart)  
**v0 Needs:** Persistent state across server restarts

**Migration Path:**
1. Add PostgreSQL/SQLite for session persistence
2. Schema: `sessions(id, user_id, state, next_checkin, created_at, ended_at)`
3. Store: Current task, calendar snapshot, check-in history
4. Load: On WebSocket connect, resume active session

---

### Session Lifecycle State Machine

```
DAY_START (Morning kickoff)
    ↓
PLANNING (Brain dump → prioritize → timeblock)
    ↓
EXECUTING (Task blocks with check-ins)
    ↓ (when deviation detected)
REPLANNING (Dynamic reschedule)
    ↓ (loop back)
EXECUTING
    ↓ (at bedtime)
DAY_CLOSE (Reflection + carryover)
    ↓
INACTIVE (Session ended, wait for next day)
```

**State Transitions:**
- `DAY_START` → Triggered by scheduler at wake time → Initiates call/notification
- `PLANNING → EXECUTING` → When user confirms plan
- `EXECUTING → REPLANNING` → When task overruns or user requests change
- `EXECUTING → DAY_CLOSE` → At bedtime trigger
- `DAY_CLOSE → INACTIVE` → After reflection complete

---

## v0 Agent Prompt Enhancements

### Add Structured Protocols
**File:** `agent/voice_agent/agent.py`

**Insert:**
```markdown
## DAILY SESSION PROTOCOLS

### Morning Planning Protocol
1. Greeting + energy check
2. Brain dump: "What's on your mind for today?"
3. Prioritize: Identify top 3 needle-movers
4. Timeblock: Assign calendar blocks with durations
5. Confirm first action + next check-in time
6. ALWAYS trigger generative_ui (day_view) after planning

### Task Execution Protocol
1. Prompt: "It's time for [TASK]. Ready to start?"
2. Micro-clarify: "What's the first 2-minute step?"
3. Set timer: Announce check-in time
4. Check-in: "How did [TASK] go? Done/Partial/Stuck?"
5. Transition: Move to next block or replan if needed

### Evening Reflection Protocol
1. Review: "Here's what we accomplished today..."
2. Derailment analysis: "What caused delays?"
3. Wins: "What worked well?"
4. Carryover: Tag incomplete tasks for tomorrow
5. Tomorrow prep: "What time should I call you tomorrow?"

### Dynamic Rescheduling Protocol
WHEN user says task needs more time:
1. Detect conflict with upcoming blocks
2. Identify: Fixed (meetings) vs Flexible (personal tasks) vs Health Anchors
3. Propose options: "We can skip X, delay Y, or compress Z"
4. Get user decision
5. Update calendar
6. Show updated day_view
```

---

## v0 Tool Enhancements

### Calendar Tool Updates
**File:** `agent/voice_agent/composio_tools.py`

**Add:**
1. `check_conflicts(start_time, end_time)` → Returns overlapping events
2. `is_busy(timestamp)` → Check if calendar shows busy at given time
3. `get_fixed_blocks()` → Return events with other attendees (immutable)
4. `get_flexible_blocks()` → Return personal events (movable)

---

### New Tool: Timer Tool
**File:** `agent/voice_agent/set_timer.py` (new)

**Purpose:** Let agent set internal timers for check-ins

```python
@tool
def set_checkin_timer(
    duration_minutes: int,
    checkin_type: str,  # "task_block" | "meal" | "transition"
    context: str
) -> str:
    """
    Sets a timer for the agent to check in with user after duration.
    
    Args:
        duration_minutes: How long until check-in
        checkin_type: Type of check-in
        context: What to ask about (e.g., "How did the deep work go?")
    """
    # Store in session/DB
    next_checkin = now() + timedelta(minutes=duration_minutes)
    store_checkin(session_id, next_checkin, checkin_type, context)
    return f"Timer set for {duration_minutes} min. I'll check in then."
```

---

### New Tool: Get User Preferences
**File:** `agent/voice_agent/user_prefs_tool.py` (new)

**Purpose:** Let agent query user settings

```python
@tool
def get_user_preferences() -> dict:
    """Returns user preferences for session personalization."""
    return {
        "wake_time": "08:00",
        "bedtime": "22:00",
        "granularity": "MEDIUM",
        "channel": "CALL",
        "health_anchors": ["meals", "sleep"],
        "dnd_windows": ["during calendar busy times"]
    }
```

---

## v0 Implementation Phases

### Phase 1: Session Persistence (Foundation)
**Blocks everything else**

- [ ] Add database (PostgreSQL or SQLite)
- [ ] Create sessions table + schema
- [ ] Update backend to persist/restore session state
- [ ] Add session lifecycle state machine
- [ ] Test: Server restart doesn't lose active session

---

### Phase 2: Proactive Initiation (Core Feature)
**Unblocks scaffolding loop**

- [ ] Implement scheduler service (APScheduler or Celery)
- [ ] Add wake time trigger → morning notification
- [ ] Implement push notification system (iOS native module or Expo)
- [ ] Implement Push-to-Session flow (Notification tap opens voice session)
- [ ] Add basic attention router logic (Push/Silent)
- [ ] Test: Agent notifies user at 8 AM without user opening app

---

### Phase 3: Morning Planning Flow (First Structured Protocol)
**Validates protocol architecture**

- [ ] Update agent prompts with Morning Planning Protocol
- [ ] Test planning conversation flow (brain dump → timeboxing)
- [ ] Ensure calendar + tasks are updated after planning
- [ ] Add generative_ui trigger after planning complete
- [ ] Test: Full morning session creates complete daily schedule

---

### Phase 4: Scaffolding Loop (Execution Support)
**Core value delivery**

- [ ] Implement timer tool for agent
- [ ] Add backend scheduler for check-in triggers
- [ ] Update agent prompts with Task Execution Protocol
- [ ] Test: Agent checks in after 90-min work block
- [ ] Add transition handling - Conversation only, no persistent history
- [ ] Test: Full day loop (multiple check-ins)

---

### Phase 5: Dynamic Rescheduling (Complex Logic)
**Differentiator feature**

- [ ] Add conflict detection to calendar tool
- [ ] Implement Unified Task/Event linking (using Task notes)
- [ ] Implement fixed/flexible block classification
- [ ] Update agent prompts with Rescheduling Protocol
- [ ] Add replan algorithm (protect anchors, propose tradeoffs)
- [ ] Add confirmation gate before calendar updates
- [ ] Test: Lunch shift scenario from product vision

---

### Phase 6: User Preferences Layer (Personalization v0)
**Enables customization**

- [ ] Add preference fields to UserProfile schema
- [ ] Add onboarding flow to collect preferences (wake time, channel, granularity)
- [ ] Implement get_user_preferences tool
- [ ] Update attention router to use preferences
- [ ] Update agent to adjust granularity based on user setting
- [ ] Test: User preferences loaded correctly at session start

---

### Phase 7: Evening Reflection (Closes the Loop)
**Completes 24-hour cycle**

- [ ] Add bedtime trigger to scheduler
- [ ] Update agent prompts with Reflection Protocol
- [ ] Implement carryover task tagging
- [ ] Store reflection data for tomorrow's context
- [ ] Test: Full day cycle (morning → execution → evening)

---

## v0 Success Criteria

**Ship when:**
1. ✅ Agent calls user at wake time without user opening app
2. ✅ Morning planning creates full daily schedule in calendar + tasks
3. ✅ Agent checks in proactively between task blocks
4. ✅ Dynamic rescheduling works for task overruns
5. ✅ Evening reflection creates carryover for next day
6. ✅ Session persists across server restarts
7. ✅ User can set preference for call vs notification

**Known v0 Limitations (OK to Ship):**
- No behavioral learning (v1: mem0/zep memory)
- No adaptive granularity (v1: telemetry-based tuning)
- No multi-user (single user "default" entity OK)
- Basic attention router (rule-based, not ML)
- No location/commute integration
- No external integrations (food ordering, etc.)

---

## v0 to v1 Roadmap Preview

**v1 Additions (Post-v0 Ship):**
- Memory systems (mem0, supermemory, zep) for behavioral learning
- Adaptive personalization (telemetry → policy tuning)
- Multi-user support + auth
- Advanced attention router (response rate learning)
- Long-term goal tracking (monthly/yearly OKRs)
- Integration expansion (email, Slack, location, food delivery)

---

## Critical Dependencies

**Must Have Before v0:**
1. Database for session persistence (blocks Phase 1)
2. Scheduler service for proactive triggers (blocks Phase 2)
3. iOS push notifications (blocks Phase 2)
4. In-app calling mechanism (blocks Phase 2)

**Can Mock/Defer:**
- Advanced conflict resolution → Simple rule-based OK for v0
- Behavioral learning → User-set preferences sufficient for v0
- Multi-modal notifications → Start with one channel (call or push)

---

## Risk Mitigation

### High Risk: iOS Calling UX
**Problem:** CallKit requires Apple Developer Program, complex setup  
**Mitigation:** Start with "local notification → opens app → connects WebSocket" as fallback

### High Risk: Scheduler Reliability
**Problem:** Backend timer service must be highly reliable (missed check-ins break UX)  
**Mitigation:** Use battle-tested scheduler (APScheduler) + monitoring/alerts

### Medium Risk: Agent Prompt Complexity
**Problem:** Adding protocols may confuse model or increase latency  
**Mitigation:** Test each protocol in isolation, measure latency, simplify if needed

### Medium Risk: User Annoyance
**Problem:** Too many check-ins may irritate instead of help  
**Mitigation:** Start with LOW granularity as default, let users opt-in to MEDIUM/HIGH

---

## Development Workflow

**Recommended Order:**
1. Phase 1 (Foundation) → Blocks everything, do first
2. Phase 2 (Proactive) → Core architectural shift, do second
3. Phase 3, 4, 7 in parallel → Independent protocol implementations
4. Phase 5 (Dynamic) → Most complex, do after basic loops work
5. Phase 6 (Prefs) → Polish layer, add throughout

**Testing Strategy:**
- Unit tests: Each tool function
- Integration tests: Full protocol flows (planning, check-in, reflection)
- End-to-end: Simulated 24-hour session with mock timers (fast-forward time)
- User testing: 3-5 ADHD users, one week of real usage

---

## Appendix: Technical Notes

### Session State Schema

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    state VARCHAR NOT NULL,  -- DAY_START, PLANNING, EXECUTING, etc.
    next_checkin TIMESTAMP,
    current_task JSON,
    calendar_snapshot JSON,
    created_at TIMESTAMP,
    ended_at TIMESTAMP
);

-- user_preferences merged into UserProfile for v0
-- See UserProfile model in task.md
```

---

### Example Notification Payload (iOS)

```json
{
    "aps": {
        "alert": {
            "title": "Time for Deep Work",
            "body": "Ready to start your project work block?"
        },
        "sound": "default",
        "category": "AGENT_CHECKIN",
        "content-available": 1
    },
    "session_id": "abc123",
    "checkin_type": "task_block_start",
    "action": "open_voice_session"
}
```

---

## Ship Checklist

**First User Onboarding:**
1. Collect: Wake time, bedtime, channel preference, health anchors
2. Demo: Show one planning session manually
3. Explain: "I'll call you tomorrow at 8 AM to plan your day"
4. Set expectations: "This is v0 - I'm learning your patterns"

---

## Notes
- **v0 = Rule-based personalization** (user sets preferences)
- **v1 = Learned personalization** (memory systems adapt automatically)
- Memory systems (mem0, zep, supermemory) are explicitly deferred to v1
- Focus v0 on reliability of proactive loop, not intelligence
