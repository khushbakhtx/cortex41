# cortex41

**Universal UI Navigation Agent** | Gemini Live Agent Challenge

> See. Reason. Act.

cortex41 is a voice-first UI agent that takes over any browser, reasons about what it sees using Gemini's multimodal vision, and navigates to accomplish your spoken goal — on any website, without DOM access.

---

## Architecture

```
User (Voice/Text)
      |
  Frontend (React + WebRTC)
      | WebSocket
  FastAPI Backend
      |
  ┌───┼────────────────────────────────────┐
  │   Cortex41AgentRunner                  │
  │   ┌─────────────┐  ┌────────────────┐  │
  │   │  Planner    │  │  Executor Loop │  │
  │   │ (Gemini Pro)│  │  See→Cache→Act │  │
  │   └─────────────┘  └────────────────┘  │
  │   ┌──────────┐ ┌──────────┐ ┌───────┐  │
  │   │Sem.Cache │ │Mod.Router│ │Skills │  │
  │   └──────────┘ └──────────┘ └───────┘  │
  └────────────────────────────────────────┘
      |                     |
  Firestore              Gemini Live API
  (memory/cache/skills)  (voice streaming)
      |
  Playwright (browser control)
```

## Key Features

- **Voice-first**: Gemini Live API handles real-time audio, interruptible at any moment
- **Pure vision**: No DOM/accessibility tree — Gemini sees pixels like a human
- **Hierarchical planner**: Two-level Plan→Execute — Pro generates sub-task plan once; Flash/Pro executor fulfils each sub-task
- **Self-writing skills**: Every successful task auto-generates a reusable skill
- **Semantic cache**: Identical visual states skip Gemini entirely (pHash + embedding)
- **Adaptive model routing**: Simple steps use Flash (10x cheaper); complex reasoning uses Pro

## OpenClaw Techniques Inherited

The browser controller inherits proven patterns from [OpenClaw](openclaw/):
- **Screenshot normalization** (`screenshot.ts`): max 2000px, 5MB, JPEG compression pipeline
- **Connection retry with backoff** (`pw-session.ts`): 3 attempts, exponential delay
- **Timeout clamping** (`pw-tools-core.interactions.ts`): [500ms, 60s] — never block forever
- **Page state tracking** (`pw-session.ts`): console, errors, network requests per page
- **Force-disconnect pattern** (`pw-session.ts`): recover from stuck CDP connections
- **Slow typing mode** (`pw-tools-core.interactions.ts`): 75ms per char for finicky inputs

## Quick Start

### Prerequisites

```bash
# Python 3.12+, Node 18+
pip install -r requirements.txt
playwright install chromium

cd frontend && npm install
```

### Environment

Copy `.env.example` to `.env` and fill in:
```
GEMINI_API_KEY=your_key
GOOGLE_CLOUD_PROJECT=your_project_id
```

### Run locally

```bash
# Terminal 1: Backend
uvicorn backend.main:app --reload --port 8000

# Terminal 2: Frontend
cd frontend && npm run dev
# Open http://localhost:5173
```

### Reproducing the Demo (Judge Testing)

Once running locally (see above), open `http://localhost:5173` and try these goals in the input bar:

| Goal | Expected behaviour |
|------|--------------------|
| `play a trump video on youtube` | Opens YouTube, searches, clicks first result |
| `find the attention is all you need paper on arxiv` | Navigates to arxiv.org/abs/1706.03762 |
| `search for the latest AI news on google` | Opens Google, shows news results |
| `open github.com and show trending repos` | Navigates to github.com/trending |

The agent streams its plan, each action, and live screenshots to the UI in real time. You can interrupt it at any time by clicking **Stop** or speaking a new goal (voice button).

### Deploy to Cloud Run

```bash
# Build + deploy
gcloud builds submit --config cloudbuild.yaml

# Or with Terraform
cd terraform
terraform init
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

## Project Structure

```
cortex41/
├── backend/
│   ├── main.py                    # FastAPI + WebSocket entrypoint
│   ├── config.py                  # All constants and env vars
│   ├── agent/
│   │   ├── cortex41_agent.py      # Main orchestrator (Plan→Execute loop)
│   │   ├── planner.py             # Hierarchical goal planner (Gemini Pro)
│   │   ├── tools.py               # Tool registry
│   │   ├── prompts.py             # Agent persona prompt
│   │   └── session_manager.py     # Multi-session tracking
│   ├── vision/
│   │   ├── gemini_vision.py       # See→Reason→Act engine
│   │   └── action_parser.py       # Parse Gemini JSON actions
│   ├── browser/
│   │   ├── browser_controller.py  # Playwright + OpenClaw techniques
│   │   └── action_executor.py     # Action dispatch (click/type/scroll/...)
│   ├── memory/
│   │   └── firestore_memory.py    # Workflow storage + semantic recall
│   ├── cache/
│   │   ├── semantic_cache.py      # pHash + embedding two-tier cache
│   │   └── model_router.py        # Flash vs Pro routing
│   ├── skills/
│   │   ├── skill_store.py         # Firestore skill CRUD
│   │   ├── skill_extractor.py     # Post-task reflection → skill
│   │   └── skill_injector.py      # Inject skills into planner
│   └── voice/
│       └── live_api_handler.py    # Gemini Live API streaming
├── frontend/
│   └── src/
│       ├── App.jsx                # Main layout + WebSocket
│       └── components/
│           ├── StatusBar.jsx
│           ├── AgentFeed.jsx      # Real-time narration log
│           ├── GoalInput.jsx      # Text goal input + interrupt
│           ├── VoiceInterface.jsx # Hold-to-speak microphone
│           ├── ScreenPreview.jsx  # Live browser screenshot
│           ├── PlanPanel.jsx      # Sub-task plan display
│           ├── StatsPanel.jsx     # Cache/cost stats
│           └── SkillLibrary.jsx   # Learned skills browser
├── terraform/                     # IaC for Cloud Run + Firestore
├── Dockerfile
└── cloudbuild.yaml
```


# diagram

2. Architecture Diagram — What to Draw
Draw this as a top-to-bottom flow with 4 horizontal layers, connected by arrows. Here's the exact layout:

Layer 1 — User (top)
One box: USER
Two inputs coming out: a microphone icon labeled Voice and a keyboard icon labeled Text Goal

Layer 2 — Frontend (React + Vite)
One wide box split into 5 panels side-by-side:

GoalInput (text + send)
PlanPanel (sub-task list with status dots)
ScreenPreview (live screenshot)
AgentFeed (narration log)
StatsPanel / SkillLibrary
Arrow from Frontend → Backend labeled WebSocket /ws/{session_id}
Arrow from Frontend ← Backend labeled plan · screenshot · audio · events

Layer 3 — Backend / Cloud Run (the big box — most content here)
Big container box labeled Google Cloud Run — FastAPI (Python)

Inside it, draw these sub-boxes:

Left column:

Cortex41AgentRunner (orchestrator) — big central box
Feeds down to: Planner (Gemini 2.5 Pro) — label it "Goal → SubTask list (once per goal)"
Feeds right to: Vision Engine — See→Reason→Act — label it "per step"
Middle column:

Semantic Cache with two sub-lines: Tier 1: pHash (in-memory) and Tier 2: Embedding similarity (Firestore)
Arrow from Vision Engine → Semantic Cache labeled cache check
Arrow from Semantic Cache → Vision Engine labeled hit / miss
Right column:

Model Router — arrow from here to two boxes:
Gemini Flash (label: simple steps, ~70%)
Gemini 2.5 Pro (label: complex reasoning, ~30%)
Bottom of the big box:

Skill System — three sub-boxes in a row: skill_injector → Planner · skill_store (Firestore) · skill_extractor ← on task complete
Voice Handler (Gemini Live API) — bidirectional arrows labeled audio in / narration out
Desktop Executor — pyautogui + mss — label: click · type · scroll · open_app
Layer 4 — Google Cloud Services (bottom)
Three boxes side by side:

Firestore — label: workflows · skills · cache · sessions
Secret Manager — label: GEMINI_API_KEY
Cloud Build / Terraform — label: CI/CD + IaC
Dashed arrows up from Firestore to Semantic Cache, Skill System, and Memory

External services (right side, connected by dashed arrows to Backend):

Gemini Live API (voice streaming)
Gemini 2.5 Flash (vision/action)
Gemini 2.5 Pro (planning/reflection)