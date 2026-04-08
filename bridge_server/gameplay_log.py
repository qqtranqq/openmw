"""Incremental gameplay log — writes structured JSONL events as they happen.

Each line is a self-contained JSON object with a timestamp, event type, and data.
The file can be tailed in real-time by the observer or other tools.

Event types:
  - goal_start: New goal began
  - goal_complete: Goal finished (success or failure)
  - tool_call: A tool was called with input and result
  - death: Player died
  - level_up: Player leveled up
  - api_call: API call with cost info
  - snapshot: Periodic player state snapshot
  - shutdown: Session ending
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).parent / "gameplay_logs"


class GameplayLog:
    """Append-only JSONL log that flushes every write."""

    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._path = LOG_DIR / f"gameplay_{ts}.jsonl"
        self._f = open(self._path, "a", encoding="utf-8")
        self._event_count = 0
        logger.info(f"Gameplay log: {self._path}")

    def _write(self, event_type: str, data: dict):
        """Write one event line and flush immediately."""
        event = {
            "t": time.time(),
            "n": self._event_count,
            "type": event_type,
            **data,
        }
        self._f.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
        self._f.flush()
        self._event_count += 1

    def goal_start(self, goal: str):
        self._write("goal_start", {"goal": goal})

    def goal_complete(self, goal: str, success: bool, summary: str):
        self._write("goal_complete", {
            "goal": goal,
            "success": success,
            "summary": summary[:300],
        })

    def tool_call(self, tool: str, input_data: dict, result: str, success: bool, duration: float = 0.0):
        self._write("tool_call", {
            "tool": tool,
            "input": {k: str(v)[:100] for k, v in input_data.items()},
            "result": result[:200],
            "success": success,
            "duration": round(duration, 2),
        })

    def death(self, cell: str, killed_by: str, goal: str):
        self._write("death", {
            "cell": cell,
            "killed_by": killed_by,
            "goal": goal[:200],
        })

    def level_up(self, level: int, attributes: list):
        self._write("level_up", {
            "level": level,
            "attributes": attributes,
        })

    def api_call(self, model: str, input_tokens: int, output_tokens: int, cost: float):
        self._write("api_call", {
            "model": model,
            "in": input_tokens,
            "out": output_tokens,
            "cost": round(cost, 4),
        })

    def snapshot(self, state):
        """Write a periodic state snapshot."""
        if not state or not state.current:
            return
        p = state.player or {}
        hp = p.get("health", {})
        mp = p.get("magicka", {})
        inv = state.inventory or []
        gold = sum(i.get("count", 0) for i in inv if i.get("recordId") == "gold_001")
        quests = state.quests or []
        self._write("snapshot", {
            "cell": p.get("cell", ""),
            "level": p.get("level", 0),
            "hp": f"{hp.get('current', 0):.0f}/{hp.get('base', 0):.0f}",
            "mp": f"{mp.get('current', 0):.0f}/{mp.get('base', 0):.0f}",
            "gold": gold,
            "bounty": p.get("bounty", 0),
            "quests": len([q for q in quests if not q.get("finished")]),
        })

    def shutdown(self, reason: str, total_cost: float = 0.0, events: int = 0):
        self._write("shutdown", {
            "reason": reason,
            "total_cost": round(total_cost, 2),
            "total_events": events or self._event_count,
        })

    def close(self):
        self._f.close()

    @property
    def path(self) -> Path:
        return self._path
