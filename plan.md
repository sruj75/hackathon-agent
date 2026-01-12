# Intentive - Future Architecture Context

> **Purpose:** This document captures architectural decisions, patterns, and context for **future redesign branches**. We're intentionally skipping these in the main branch to ship fast and test with users. Come back here when it's time to scale.

---

## Vision

Voice-first AI accountability coach for day planning. User speaks continuously while app updates tasks/schedule in real-time. Hands-off experience (e.g., plan your day while driving).

**Key differentiator:** Not another to-do app. A personal coach that helps you plan and stay accountable.

---

## What We're Skipping Now (Main Branch)

Building scrappy in main branch to get Composio + voice agent working for user testing:

- ❌ Domain API / ports-adapters pattern
- ❌ Proper auth flow architecture  
- ❌ Timezone-aware datetime handling
- ❌ Safety gates (dry_run → confirm → execute)
- ❌ Structured observability
- ❌ WebRTC / LiveKit integration
- ❌ Firebase Auth / Firestore integration

**Main branch is throwaway code.** That's the point.

---

## Redesign Branch: Target Architecture

### Full Stack Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Frontend      │     │    Backend      │     │   Voice Agent   │
│   (Expo/TS)     │────▶│   (Vercel/TS)   │◀────│    (Python)     │
│                 │     │                 │     │                 │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         │                       ▼                       │
         │              ┌─────────────────┐              │
         └─────────────▶│  LiveKit Cloud  │◀─────────────┘
                        │   (WebRTC Room) │
                        └─────────────────┘
```

### Voice Agent + External Tools

```
┌─────────────────┐     ┌─────────────────────────────────┐
│   Frontend      │     │         Python Agent            │
│   (Expo/TS)     │────▶│   ADK + Gemini Live API         │
│   WebSocket     │     │   + Domain Layer                │
└─────────────────┘     │   + Adapters (Composio/etc)     │
                        └─────────────────────────────────┘
                                       │
                        ┌──────────────┴──────────────┐
                        ▼                             ▼
                 ┌────────────┐               ┌────────────┐
                 │  Google    │               │  Google    │
                 │  Calendar  │               │   Tasks    │
                 └────────────┘               └────────────┘
```

### Firebase + LiveKit Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND (Expo)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Firebase SDK │  │ LiveKit SDK  │  │ Firebase Auth State  │  │
│  │  (Auth, DB)  │  │   (Voice)    │  │   → Agent Context    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼─────────────────┼─────────────────────┼───────────────┘
          │                 │                     │
          ▼                 ▼                     ▼
┌──────────────────┐ ┌──────────────┐ ┌───────────────────────────┐
│ Firebase Cloud   │ │LiveKit Cloud │ │  Backend (Vercel)         │
│ (Firestore, FCM, │ │ (WebRTC)     │ │  - Token server           │
│  Functions)      │ │              │ │  - /api/users/:id/context │
└──────────────────┘ └──────┬───────┘ └───────────────────────────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │  Python Agent    │
                   │  (Gemini Live)   │
                   │  + User context  │
                   │  + Tool calling  │
                   └──────────────────┘
```

### Gemini Live API (Why We Use It)

```
Traditional Pipeline:              Our Approach (Realtime):
┌─────┐   ┌─────┐   ┌─────┐       ┌────────────────────────┐
│ STT │ → │ LLM │ → │ TTS │       │    Gemini Live API     │
└─────┘   └─────┘   └─────┘       │  (Speech-to-Speech)    │
                                  │  + Native Tool Calling │
                                  └────────────────────────┘
```

- Lower latency (single model)
- Native audio understanding
- Model decides when to invoke tools automatically
- Real-time generative UX while user speaks

---

## Transport Layer Decision

| Factor | WebRTC (LiveKit) | WebSocket (ADK) | For Intentive |
|--------|------------------|-----------------|---------------|
| Latency | ~50–100ms lower | ~100–200ms | Barely noticeable |
| Composio integration | You write the bridge | Native MCP support ✓ | ADK + Composio just works |
| Tool calling | Works, but manual | Automatic tool routing ✓ | Composio handles OAuth |
| Complexity | More moving parts | Simpler stack | Less to debug |
| Calendar/Tasks auth | You handle OAuth | Composio handles it ✓ | Zero OAuth headache |

**Now (main):** WebSocket + ADK — fastest path to user testing  
**Later (redesign):** WebRTC + LiveKit — production latency

---

## Domain Layer: Ports & Adapters

The "brain" of the agent should be backend-agnostic.

```
LLM → Domain Ops → Adapter (Composio/Google APIs/DB)
```

**Why this matters:**
- Replace Composio later → only rewrite adapters
- Support non-Google backends (Apple Calendar, Notion, Jira, your own DB)
- Consistent audit logs and permissions

**Directory structure:**
```
agent/
├── domain/              # Pure business logic (never imports adapters)
│   ├── models.py        # Backend-agnostic models
│   ├── ports.py         # Adapter interfaces
│   └── operations/      # Coaching intelligence
│       ├── calendar_ops.py
│       └── task_ops.py
├── adapters/            # Implementations (import domain models only)
│   ├── composio/
│   │   ├── client.py
│   │   ├── auth_adapter.py
│   │   ├── calendar_adapter.py
│   │   └── tasks_adapter.py
│   └── fake/            # In-memory for tests
└── voice_agent/         # ADK integration (calls domain ops only)
    ├── agent.py
    └── tools.py
```

**Import discipline:**
- Domain imports ports only
- Adapters import domain models
- Tools import domain ops only (never adapters directly)

---

## Tool Integration Options

| Approach | Pros | Cons | When to use |
|----------|------|------|-------------|
| **SDK-native** | Simplest, in-process debugging, fewer moving parts | Tied to Python | Single Python agent, start here |
| **MCP endpoint** | Language-agnostic, multi-client reuse, isolated execution | Extra service, network boundary | Multi-client tooling needs |

**Decision:** Start SDK-native, add MCP layer later if multi-client needed.

---

## Auth Flow: Frontend-led

```
┌──────────┐         ┌──────────┐         ┌──────────┐
│  Expo    │         │ Backend  │         │ Composio │
│ Frontend │         │  (API)   │         │  OAuth   │
└────┬─────┘         └────┬─────┘         └────┬─────┘
     │                    │                    │
     │ GET /auth/google/  │                    │
     │ connect?user_id=x  │                    │
     │───────────────────▶│                    │
     │                    │                    │
     │    { connect_url } │                    │
     │◀───────────────────│                    │
     │                    │                    │
     │ Open URL in browser│                    │
     │────────────────────────────────────────▶│
     │                    │                    │
     │                    │  callback + tokens │
     │                    │◀───────────────────│
     │                    │                    │
     │ Poll /auth/status  │                    │
     │───────────────────▶│                    │
     │                    │                    │
     │   { connected }    │                    │
     │◀───────────────────│                    │
```

**Backend endpoints:**
- `GET /auth/google/connect?user_id=...` → returns connect URL
- `GET /auth/google/callback` → handles OAuth callback
- `GET /auth/status?user_id=...` → returns connection status

---

## Safety Gates for Writes

All destructive operations should follow:

```
┌─────────┐     ┌─────────────┐     ┌─────────────┐
│ User    │     │ Agent calls │     │ User says   │
│ request │────▶│ dry_run=True│────▶│ "yes"       │
└─────────┘     └─────────────┘     └──────┬──────┘
                                           │
                       ┌───────────────────┘
                       ▼
              ┌────────────────┐
              │ Agent executes │
              │ dry_run=False  │
              │ + idempotency  │
              └────────────────┘
```

**Pattern:** `plan → confirm → execute`
**Idempotency keys:** Prevent duplicate creates on retry

---

## Timezone & Calendar Handling

**Landmines to avoid:**
- Store all datetimes in **UTC internally**
- Preserve **original timezone** for display
- Handle **all-day events** separately (no time component)
- Handle **recurring events** (expand or strategy)
- Use **proper free/busy queries** (not "list events and subtract")

---

## Firebase Integration

Firebase project: `hackathon-intentive`  
Bundle ID: `life.intentive.expo`  
Project Number: `266708443290`

| Service | Purpose | Redesign Status |
|---------|---------|-----------------|
| **Firebase Auth** | User identity, Google Sign-In | Configured, use in redesign |
| **Firestore** | User data, preferences, session history | Implement in redesign |
| **Cloud Functions** | Webhooks, background jobs | Implement in redesign |
| **FCM (Push)** | Reminders, accountability nudges | Enabled |
| **Storage** | Audio recordings (optional) | Available |

**Auth mapping:**
- Firebase Auth = user identity
- Composio = Google Calendar/Tasks OAuth (separate)
- Map: Firebase UID → Composio user_id

---

## Observability (Redesign)

Every adapter call should log:
- Tool name
- User ID
- Args hash (not PII)
- Latency (ms)
- Status (success/error)
- Error type if failed

**Exception taxonomy:**
- `AuthError` — user not authenticated
- `ValidationError` — invalid parameters
- `TransientError` — temporary failure, retry
- `ConflictError` — etag mismatch, 409/412

---

## Redesign Branch Checklist

When ready to scale:

- [ ] Create `redesign` branch from clean state
- [ ] Implement domain layer (ports/adapters)
- [ ] Add proper auth flow endpoints
- [ ] Add safety gates for writes
- [ ] Add timezone-aware datetime handling
- [ ] Add structured logging/observability
- [ ] Migrate to WebRTC (LiveKit) for lower latency
- [ ] Add Firebase Auth integration
- [ ] Add Firestore for user data persistence
- [ ] Write tests with fake adapters

---
