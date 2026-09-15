# Astrid :    Autonomous Multi-Agent Browser AI & Cloud Automation Platform

Astrid takes a plain-language goal and completes it end to end: it reasons about
what needs to happen, browses the real web with Playwright/CDP, calls AWS
directly when a task has an API, checks its own work against real evidence
before declaring success, and remembers what it learned for next time.

## 1. Agent architecture:

Astrid uses an actor-critic style pipeline with three cooperating agents plus
a memory layer, rather than one model trying to plan, act, and grade itself.

```


User goal
   │
   ▼
Orchestrator ── queries memory for similar past runs
   │             generates an ordered subtask plan
   ▼
 ┌─────────────── loop over each subtask ───────────────┐
 │                                                        │
 │   Planner ── reads page/DOM or resource state          │
 │      │        decides: browser action or cloud call    │
 │      ▼                                                  │
 │   Execution engine (Browser CDP  or  AWS boto3)         │
 │      │        returns concrete evidence                 │
 │      ▼                                                  │
 │   Verifier ── checks evidence against success criteria  │
 │      │                                                   │
 │      ├── success → Orchestrator advances to next step   │
 │      └── failure → correction sent back to Planner       │
 │                                                        │
 └────────────────────────────────────────────────────────┘
   │
   ▼
Memory writer ── embeds outcome, stores in Supabase pgvector
```

<img width="2760" height="3640" alt="astrid_full_architecture_dark" src="https://github.com/user-attachments/assets/cb8d0cc2-21ce-4444-951e-e03f5a03b6de" />


**Orchestrator** — breaks the goal into an ordered list of atomic subtasks,
queries memory first so prior learnings inform the plan, and tracks progress
through the loop.

**Planner** — the tactical agent. Inspects the current page (including nested
iframes) or the current cloud resource state, and decides the exact next
action: navigate, click, type, select an option, or hand off to the cloud
engine.

**Verifier** — the critic. Never trusts the Planner's own claim of success.
Instead it checks independent evidence: an HTTP status code, an AWS `describe_*`
call, or a parsed grading report. On failure it sends a specific correction
back to the Planner rather than restarting the task from scratch.

**Memory writer** — after a run finishes, the outcome is embedded and stored,
so a semantically similar future goal retrieves this run's learnings.

### Tri-tier memory

| Tier | Lifetime | Storage | Purpose |
|---|---|---|---|
| In-flight plan memory | One run | Process memory | Active step index, scratchpad (AMI id, ARN, DNS name) |
| Local session memory | Across restarts | `quiz_memory.json` | Instant exact-match recall of previously corrected answers |
| Global semantic memory | Across sessions & users | Supabase Postgres + pgvector | Similarity search over past goals via `all-MiniLM-L6-v2` embeddings |

<img width="2720" height="2160" alt="astrid_supabase_roles" src="https://github.com/user-attachments/assets/623fbe71-8107-432f-b287-74b22cfc302e" />

Supabase specifically plays three distinct roles, not one:

- **Auth** — Google OAuth sign-in, issues the JWT that guards `/ws/task`
- **Relational storage (Postgres + RLS)** — `profiles`, `agent_memory`, `task_runs`, every table scoped by Row-Level Security so a user can only ever read or write their own rows (see `backend/db/schema.sql`)
- **Vector search (pgvector)** — `match_memory()` does the semantic similarity search that powers RAG retrieval

### Execution engines

- **Browser engine** (`backend/browser/agent.py`) — connects to a real,
  already-authenticated Chrome instance over the Chrome DevTools Protocol
  (port `9222`), so logins, cookies, and SSO sessions are preserved.
- **Cloud engine** (`backend/agents/aws_lab_executor.py`) — extracts
  short-lived STS credentials from the active browser session and drives AWS
  directly through `boto3`: AMIs, target groups, load balancers, launch
  templates, auto scaling groups, and CloudWatch alarms — no console clicking.

Anything with a stable API is routed to the cloud engine; the browser engine
is reserved for surfaces that only exist as a UI.

## 2. Web application architecture:

```
User ── Google login (Supabase Auth)
  │
  ▼
Frontend (React + Vite)
  │  WebSocket, Supabase access token in the connection
  ▼
FastAPI gateway (/ws/task)
  │  verifies JWT → resolves user id → logs task run
  ▼
Multi-agent cognitive core (Orchestrator → Planner → Verifier)
  │                                   │
  ▼                                   ▼
Groq API (LLM calls)          Supabase (Postgres + pgvector)
  │
  ▼
Execution engines (Browser CDP / AWS boto3)
  │
  ▼
Target environments (user's Chrome / AWS cloud)
  │
  ▼
Results streamed back over the same WebSocket, live, step by step
```

**Frontend** — a chat interface plus a live plan/step visualizer. It performs
no reasoning of its own; every step shown is driven by an event streamed from
the backend.

**Backend gateway** — a FastAPI app that verifies the Supabase JWT on every
WebSocket connection, resolves it to a user id, and scopes every memory read
and write to that user. Stateless between connections aside from what it
reads from and writes to Supabase, so it scales horizontally on Cloud Run.

**Auth & user data** — Google sign-in via Supabase Auth. A Postgres trigger
auto-creates a `profiles` row (name, email, avatar) on first sign-up, so no
separate profile-management code is needed.

## 3. Tech stack:

| Layer | Technology |
|---|---|
| Frontend | React 18, Vite, Tailwind, WebSocket client |
| Backend | Python 3.12, FastAPI, Uvicorn, asyncio |
| Agent cognition | Groq (Llama 3.3 70B versatile), Qwen 2.5, Pydantic |
| Browser control | Playwright, Chrome DevTools Protocol |
| Cloud automation | AWS SDK (boto3) |
| Database & vector | Supabase, PostgreSQL, pgvector, sentence-transformers |
| Auth | Supabase JWT |
| Deploy | Google Cloud Run (backend), Firebase Hosting (frontend) |

## 4. Setup

### Supabase (auth + user DB + vector DB)
1. Create a project at supabase.com
2. Authentication → Providers → enable Google (OAuth client from Google Cloud Console)
3. SQL Editor → run `backend/db/schema.sql`
4. Copy Project URL, anon key, and service_role key into `.env`

### Groq (LLM inference)
1. Sign up at console.groq.com, generate an API key
2. Set `GROQ_API_KEY` in `.env`

### Backend
```bash
cd backend
cp .env.example .env   # fill in real keys
pip install -r requirements.txt
playwright install chromium
uvicorn main:app --reload --port 8080
```

### Frontend
```bash
cd frontend
npm install @supabase/supabase-js react react-dom
npm start
```

## 5. Deploy (Google Cloud, free tier):
```bash
cd backend
gcloud run deploy astrid-backend \
  --source . --region us-central1 --allow-unauthenticated \
  --set-env-vars GROQ_API_KEY=...,SUPABASE_URL=...,SUPABASE_ANON_KEY=...,SUPABASE_SERVICE_ROLE_KEY=...

cd ../frontend
npm run build
firebase deploy
```

## 6. Cost:
Everything above runs on free tiers today: Supabase free project, Groq free
tier, Cloud Run free monthly quota, Firebase Hosting free tier — $0 to start.
Upgrade path later: swap Groq for a paid model, upgrade the Supabase plan if
storage or rate limits are exceeded — the architecture itself doesn't change.

See `Astrid_Architecture_Report.docx` for the full 17-page architecture and
design report, including the end-to-end sequence diagram, security model, and
limitations.
