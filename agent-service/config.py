import os
from dotenv import load_dotenv

# Load from project root .env
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_REGION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")

# Primary model — Gemini 3 Flash Preview for planning and vision reasoning
GEMINI_MODEL = "gemini-3-flash-preview"
# Live API model for voice streaming
GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-latest"

FIRESTORE_COLLECTION_WORKFLOWS = os.getenv("FIRESTORE_COLLECTION_WORKFLOWS", "cortex41_workflows")
FIRESTORE_COLLECTION_SESSIONS = os.getenv("FIRESTORE_COLLECTION_SESSIONS", "cortex41_sessions")
FIRESTORE_COLLECTION_SCREENSHOT_CACHE = os.getenv(
    "FIRESTORE_COLLECTION_SCREENSHOT_CACHE", "cortex41_screenshot_cache"
)

MAX_STEPS_PER_TASK = 80
MAX_SUBTASK_ATTEMPTS = 3
SCREENSHOT_INTERVAL_MS = 500
ACTION_CONFIDENCE_THRESHOLD = 0.7

# Inherited from OpenClaw: screenshot normalization limits
SCREENSHOT_MAX_SIDE = 2000       # px — beyond this JPEG-compress down
SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB hard cap

# Inherited from OpenClaw: interaction timeout clamping
INTERACTION_TIMEOUT_MIN_MS = 500
INTERACTION_TIMEOUT_MAX_MS = 60_000
INTERACTION_TIMEOUT_DEFAULT_MS = 8_000

# Inherited from OpenClaw: CDP connection retry
CDP_CONNECT_ATTEMPTS = 3
CDP_CONNECT_BASE_DELAY_MS = 250

# Semantic cache settings
CACHE_PHASH_THRESHOLD = int(os.getenv("CACHE_PHASH_THRESHOLD", "8"))
CACHE_EMBEDDING_THRESHOLD = float(os.getenv("CACHE_EMBEDDING_THRESHOLD", "0.92"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))

# Model routing
FLASH_MODEL = "gemini-3-flash-preview"
PRO_MODEL = "gemini-3-pro-preview"
FLASH_CONFIDENCE_FLOOR = 0.85

# Skill system
SKILLS_COLLECTION = "cortex41_skills"
SKILL_RETRIEVAL_THRESHOLD = 0.80
SKILL_MIN_SUCCESS_RATE = 0.50

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
