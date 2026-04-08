"""Structured session tracker — records goals, actions, and outcomes for analysis."""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).parent / "session_logs"


@dataclass
class ActionRecord:
    """A single action taken by the agent."""
    timestamp: float
    tool: str
    input: dict
    result: str
    success: bool
    duration_seconds: float = 0.0


@dataclass
class GoalRecord:
    """A goal and its outcome."""
    goal: str
    start_time: float
    end_time: float = 0.0
    outcome: str = ""  # "completed", "failed", "abandoned"
    summary: str = ""
    actions: list[ActionRecord] = field(default_factory=list)
    action_count: int = 0
    failures: int = 0

    @property
    def duration_minutes(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) / 60.0
        return 0.0


@dataclass
class SessionSnapshot:
    """Player state at a point in time."""
    timestamp: float
    cell: str = ""
    level: int = 0
    health: float = 0
    gold: int = 0
    bounty: int = 0
    quest_count: int = 0
    quests_completed: list[str] = field(default_factory=list)
    spells: list[str] = field(default_factory=list)


class SessionTracker:
    """Tracks structured session data for post-game analysis."""

    def __init__(self):
        self._session_start = time.monotonic()
        self._goals: list[GoalRecord] = []
        self._current_goal: Optional[GoalRecord] = None
        self._snapshots: list[SessionSnapshot] = []
        self._deaths: int = 0
        self._loads: int = 0
        self._total_actions: int = 0
        self._total_failures: int = 0
        self._api_calls: int = 0
        self._model: str = ""

    def set_model(self, model: str):
        self._model = model

    def start_goal(self, goal: str):
        """Begin tracking a new goal."""
        if self._current_goal:
            # Auto-close previous goal as abandoned
            self.end_goal("abandoned", "Replaced by new goal")
        self._current_goal = GoalRecord(goal=goal, start_time=time.monotonic())
        logger.info(f"Tracker: started goal: {goal}")

    def end_goal(self, outcome: str, summary: str):
        """Mark the current goal as done."""
        if not self._current_goal:
            return
        self._current_goal.end_time = time.monotonic()
        self._current_goal.outcome = outcome
        self._current_goal.summary = summary
        self._current_goal.action_count = len(self._current_goal.actions)
        self._current_goal.failures = sum(
            1 for a in self._current_goal.actions if not a.success
        )
        self._goals.append(self._current_goal)
        logger.info(
            f"Tracker: goal {outcome}: {summary} "
            f"({self._current_goal.action_count} actions, "
            f"{self._current_goal.failures} failures, "
            f"{self._current_goal.duration_minutes:.1f} min)"
        )
        self._current_goal = None

    def record_action(self, tool: str, tool_input: dict, result: str, success: bool, duration: float = 0.0):
        """Record an action taken."""
        record = ActionRecord(
            timestamp=time.monotonic(),
            tool=tool,
            input=tool_input,
            result=result[:500],
            success=success,
            duration_seconds=duration,
        )
        self._total_actions += 1
        if not success:
            self._total_failures += 1
        if self._current_goal:
            self._current_goal.actions.append(record)

    def record_snapshot(self, state):
        """Capture a point-in-time snapshot of player state."""
        if not state.current:
            return
        p = state.player or {}
        health = p.get("health", {})
        inv = state.inventory or []
        gold = sum(
            i.get("count", 0) for i in inv if i.get("recordId") == "gold_001"
        )
        quests = state.quests or []
        completed = [q.get("id", "?") for q in quests if q.get("finished")]
        active = [q for q in quests if not q.get("finished")]

        # Extract spell names
        raw_spells = state.current.get("spells", []) if state.current else []
        spell_names = []
        spell_list = raw_spells if isinstance(raw_spells, list) else []
        if isinstance(raw_spells, dict):
            spell_list = [v for k, v in raw_spells.items() if not k.startswith("_") and isinstance(v, dict)]
        for s in spell_list:
            if isinstance(s, dict):
                spell_names.append(s.get("name", s.get("id", "?")))

        self._snapshots.append(SessionSnapshot(
            timestamp=time.monotonic(),
            cell=p.get("cell", ""),
            level=p.get("level", 0),
            health=health.get("current", 0),
            gold=gold,
            bounty=p.get("bounty", 0),
            quest_count=len(active),
            quests_completed=completed,
            spells=spell_names,
        ))

    def record_death(self):
        self._deaths += 1

    def record_load(self):
        self._loads += 1

    def record_api_call(self):
        self._api_calls += 1

    def save(self):
        """Save the session log to disk."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # Close any in-progress goal
        if self._current_goal:
            self.end_goal("incomplete", "Session ended")

        elapsed = (time.monotonic() - self._session_start) / 60.0

        session = {
            "duration_minutes": round(elapsed, 1),
            "model": self._model,
            "stats": {
                "total_actions": self._total_actions,
                "total_failures": self._total_failures,
                "failure_rate": round(self._total_failures / max(1, self._total_actions), 3),
                "goals_completed": sum(1 for g in self._goals if g.outcome == "completed"),
                "goals_failed": sum(1 for g in self._goals if g.outcome == "failed"),
                "goals_abandoned": sum(1 for g in self._goals if g.outcome == "abandoned"),
                "deaths": self._deaths,
                "loads": self._loads,
                "api_calls": self._api_calls,
            },
            "goals": [],
            "snapshots": [],
        }

        for g in self._goals:
            goal_data = {
                "goal": g.goal,
                "outcome": g.outcome,
                "summary": g.summary,
                "duration_minutes": round(g.duration_minutes, 1),
                "action_count": g.action_count,
                "failures": g.failures,
                "actions": [
                    {
                        "tool": a.tool,
                        "input": a.input,
                        "success": a.success,
                        "result": a.result[:200],
                        "duration": round(a.duration_seconds, 1),
                    }
                    for a in g.actions
                ],
            }
            session["goals"].append(goal_data)

        # Include a subset of snapshots (every 5th to keep size down)
        for i, s in enumerate(self._snapshots):
            if i % 5 == 0:
                session["snapshots"].append({
                    "cell": s.cell,
                    "level": s.level,
                    "health": round(s.health),
                    "gold": s.gold,
                    "bounty": s.bounty,
                    "quest_count": s.quest_count,
                    "quests_completed": s.quests_completed,
                    "spells": s.spells,
                })

        import datetime
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filepath = LOG_DIR / f"session_{ts}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
        logger.info(f"Session log saved: {filepath.name}")
        return filepath
