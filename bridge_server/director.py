"""Live Director agent — watches Scotti play and injects narrative direction."""

import asyncio
import logging
import time
from typing import Optional

from anthropic import AsyncAnthropic

from game_state import GameState

logger = logging.getLogger(__name__)


class ActionLog:
    """Ring buffer of recent agent actions for the director to review."""

    def __init__(self, max_entries: int = 50):
        self._entries: list[dict] = []
        self._max = max_entries

    def append(self, entry: dict):
        entry.setdefault("time", time.monotonic())
        self._entries.append(entry)
        if len(self._entries) > self._max:
            self._entries = self._entries[-self._max:]

    def recent(self, n: int = 20) -> list[dict]:
        return self._entries[-n:]

    def summary(self) -> str:
        """Format recent actions for the director's context."""
        entries = self.recent(15)
        if not entries:
            return "No recent actions."
        lines = ["Recent actions:"]
        for e in entries:
            etype = e.get("type", "?")
            if etype == "tool":
                name = e.get("name", "?")
                inp = e.get("input", {})
                result = e.get("result", "")[:150]
                lines.append(f"  [{name}] {inp} -> {result}")
            elif etype == "thought":
                text = e.get("result", "")[:200]
                lines.append(f"  [thinking] {text}")
        return "\n".join(lines)


DIRECTOR_SYSTEM_PROMPT = """You are a live director watching a character named Decumus Scotti — a bumbling Imperial clerk — play through The Elder Scrolls III: Morrowind. You are directing his adventure like a film.

Your job is pacing and drama. You watch what Scotti is doing and give brief narrative nudges to make the experience more cinematic, entertaining, and emotionally engaging.

Watch for:
- PACING: Too much idle time? Shopping too long? Walking in circles? Nudge toward action.
- DRAMA: Is there a hostile creature nearby he hasn't noticed? An NPC with an interesting story? A building he walked past? Point it out.
- ATMOSPHERE: Beautiful sunset? Eerie fog? Strange sounds? Suggest he pause and take it in.
- CHARACTER: Scotti is a clerk, not a hero. Lean into his discomfort, his hunger, his architectural opinions. Remind him to journal dramatic moments.
- NARRATIVE: Are there story threads he's ignoring? Quest hooks he missed? Push him toward them.
- VARIETY: If he's been doing the same thing, suggest a change — explore a new area, talk to someone new, try something risky.

Output 1-3 short director's notes. Keep total output under 100 words. Be specific — reference what you actually see in the game state and recent actions.

Examples:
- "There's a hostile mudcrab 300m to the south. This could be Scotti's first combat — make it pathetic and funny."
- "You've been inside Arrille's shop for a while. The sun is probably setting outside. Step out and describe the view."
- "Fargoth is nearby and seems like he has a story. Go talk to him — Scotti would appreciate meeting another underdog."
- "Time for a journal entry. Reflect on how this alien landscape compares to the marble avenues of the Imperial City."

Do NOT:
- Issue specific game commands (move forward, turn left, etc.)
- Repeat the same suggestion twice
- Give spoilers about quest outcomes
- Break character — you are a director, not a player
"""


async def run_director(
    state: GameState,
    action_log: ActionLog,
    injection_queue: asyncio.Queue,
    interval_seconds: float = 45.0,
    model: str = "claude-haiku-4-5-20251001",
):
    """Director loop — periodically analyzes gameplay and injects narrative notes."""

    client = AsyncAnthropic()
    director_messages = []

    logger.info(f"Director started (model={model}, interval={interval_seconds}s)")

    # Initial delay — let Scotti get settled
    await asyncio.sleep(interval_seconds)

    while True:
        try:
            # Build context snapshot
            game_summary = state.summarize() if state.current else "No game state yet."
            action_summary = action_log.summary()

            context = f"=== CURRENT GAME STATE ===\n{game_summary}\n\n=== RECENT ACTIONS ===\n{action_summary}"

            # Keep director history short (last 2 exchanges max)
            if len(director_messages) > 4:
                director_messages = director_messages[-4:]

            director_messages.append({"role": "user", "content": context})

            response = await client.messages.create(
                model=model,
                max_tokens=200,
                system=DIRECTOR_SYSTEM_PROMPT,
                messages=director_messages,
            )

            note = ""
            for block in response.content:
                if hasattr(block, "text"):
                    note += block.text

            director_messages.append({"role": "assistant", "content": response.content})

            if note.strip():
                await injection_queue.put(note.strip())
                logger.info(f"Director note: {note.strip()[:200]}")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Director error (non-fatal): {e}")

        await asyncio.sleep(interval_seconds)
