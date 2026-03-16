"""
Gemini Live API handler for cortex41.
Manages real-time bidirectional audio streaming.
Uses google-generativeai SDK's aio.live.connect interface.
"""

import asyncio
import base64
from typing import Callable, Awaitable, Optional

from google import genai

from backend.config import GEMINI_API_KEY, GEMINI_LIVE_MODEL


LIVE_SYSTEM_PROMPT = """
You are the voice interface of cortex41, a UI navigation agent.
Your role:
1. Listen to the user's goal or interruption
2. Extract the precise intent (goal to accomplish, or change to current task)
3. Respond briefly in a calm, focused voice confirming what you understood
4. If the user interrupts mid-task, acknowledge and adapt

Keep responses SHORT (1-2 sentences). You are a doer, not a talker.
Always end with what you're about to do: "Got it — I'll book the cheapest flight now."
"""

INTERRUPT_KEYWORDS = [
    "wait", "stop", "no", "change", "actually",
    "cancel", "different", "instead", "abort", "quit",
]


class LiveAPIHandler:
    def __init__(
        self,
        on_goal_callback: Callable[[str], Awaitable[None]],
        on_interrupt_callback: Callable[[Optional[str]], Awaitable[None]],
        on_audio_response_callback: Callable[[str], Awaitable[None]],
    ):
        """
        on_goal_callback: called when user states a new goal
        on_interrupt_callback: called on interruption (may include new goal)
        on_audio_response_callback: sends TTS audio back to frontend
        """
        self.on_goal = on_goal_callback
        self.on_interrupt = on_interrupt_callback
        self.on_audio_response = on_audio_response_callback
        self.is_active = False
        self._session = None
        self._session_task: Optional[asyncio.Task] = None

    async def start_session(self):
        """Initialize Gemini Live API session."""
        self.is_active = True
        client = genai.Client(api_key=GEMINI_API_KEY)

        config = {
            "response_modalities": ["AUDIO", "TEXT"],
            "system_instruction": LIVE_SYSTEM_PROMPT,
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": "Charon"}
                },
            },
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

        try:
            async with client.aio.live.connect(model=GEMINI_LIVE_MODEL, config=config) as session:
                self._session = session
                await self._listen_loop()
        except Exception as e:
            print(f"[Voice] Live API session error: {e}")
        finally:
            self.is_active = False
            self._session = None

    async def send_audio_chunk(self, audio_b64: str):
        """
        Send a chunk of raw audio from the frontend microphone.
        audio_b64: base64-encoded 16-bit PCM, 16kHz mono
        """
        if self._session and self.is_active:
            try:
                audio_bytes = base64.b64decode(audio_b64)
                await self._session.send(
                    input={"data": audio_bytes, "mime_type": "audio/pcm;rate=16000"},
                    end_of_turn=False,
                )
            except Exception as e:
                print(f"[Voice] Send audio chunk error: {e}")
        else:
            if not self.is_active:
                print("[Voice] Cannot send audio: session not active")
            if not self._session:
                print("[Voice] Cannot send audio: session not found (check model name/API key)")

    async def signal_end_of_turn(self):
        """Signal that the user has finished speaking."""
        if self._session and self.is_active:
            try:
                await self._session.send(input=" ", end_of_turn=True)
            except Exception as e:
                print(f"[Voice] End of turn signal error: {e}")

    async def _listen_loop(self):
        """Process incoming events from Gemini Live API."""
        try:
            async for response in self._session.receive():
                if response.text:
                    print(f"[Voice] Transcript received: {response.text}")
                    await self._handle_transcript(response.text)

                if response.data:
                    # print(f"[Voice] Audio response received: {len(response.data)} bytes")
                    audio_b64 = base64.b64encode(response.data).decode("utf-8")
                    await self.on_audio_response(audio_b64)
        except Exception as e:
            if self.is_active:
                print(f"[Voice] Listen loop error: {e}")

    async def _handle_transcript(self, transcript: str):
        """
        Determine if this is a new goal or an interruption.
        Interruption signals: "wait", "stop", "no", "change", "actually", etc.
        """
        is_interrupt = any(kw in transcript.lower() for kw in INTERRUPT_KEYWORDS)

        if is_interrupt:
            print(f"[Voice] Interruption detected: {transcript}")
            # If the transcript is long enough, treat it as a new goal after interruption
            new_goal = transcript if len(transcript) > 10 else None
            await self.on_interrupt(new_goal)
        else:
            print(f"[Voice] New goal detected: {transcript}")
            await self.on_goal(transcript)

    async def stop_session(self):
        self.is_active = False
        self._session = None
