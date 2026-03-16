# cortex41

A browser agent powered by Gemini. Message or Speak a goal, watch it execute.

---

## Stack

- **Backend**: Python, FastAPI, Playwright, Firestore
- **Frontend**: React, Vite
- **AI**: Gemini 3 Flash (planning and execution), Gemini Live API (voice)

## Platform Support

> **Tested on macOS only.**

The agent has two execution modes:

| Mode | Platform | What it does |
|------|----------|-------------|
| **Browser mode** | macOS, Linux, Windows | Controls a Playwright Chromium browser — all web tasks |
| **Desktop mode** | macOS only | Controls native apps via `osascript`, `CoreGraphics`, `mss` screen capture |

On **Windows / Linux**, the backend starts and browser-mode goals (YouTube, Google, GitHub, arXiv, etc.) work normally. Desktop-mode goals (opening native apps like Terminal, Finder, System Settings) will fail — those APIs are macOS-specific and have no cross-platform equivalent in this codebase.

On **macOS**, two one-time permissions are required before running:
- **Screen Recording**: System Settings → Privacy & Security → Screen Recording → add your terminal
- **Accessibility**: System Settings → Privacy & Security → Accessibility → add your terminal

Without these, screen capture and window focus will silently fail.

---

## Quick Start

### Prerequisites

- Python 3.12+
- Node 18+
- A Gemini API key ([get one here](https://aistudio.google.com/app/apikey))
- A Google Cloud project with Firestore enabled

```bash
pip install -r requirements.txt
playwright install chromium
cd agent-ui && npm install
```

### Environment

Copy `.env.example` to `.env` and fill in:
```
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_CLOUD_PROJECT=your_gcp_project_id
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
```

### Run

```bash
# Terminal 1 — backend
uvicorn agent-service.main:app --reload --port 8000

# Terminal 2 — frontend
cd agent-ui && npm run dev
# Open http://localhost:5173
```

### Try it

All of these are browser-mode goals and work on any platform:

| Goal | What happens |
|------|-------------|
| `play a trump video on youtube` | Searches YouTube, plays first result |
| `find the attention is all you need paper on arxiv` | Opens arxiv.org/abs/1706.03762 |
| `search for the latest AI news` | Opens Google, shows results |
| `open github trending` | Navigates to github.com/trending |

Type the goal in the input bar and press Enter (or use the mic button for voice).

### Deploy

```bash
gcloud builds submit --config cloudbuild.yaml
```
