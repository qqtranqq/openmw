"""Adventure log system for tracking sessions across multiple plays.

Saves timestamped session logs with goals, knowledge gained, and narrative.
Loads prior session summaries so the agent has continuity between sessions.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from knowledge import KnowledgeBase


LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adventures")


def _ensure_dir():
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)


def save_session(goal: str, knowledge: KnowledgeBase, session_minutes: float = 0):
    """Save a session log after the agent finishes playing."""
    _ensure_dir()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_num = len(list_sessions()) + 1

    # Gather all knowledge entries
    all_knowledge = {}
    for cat in knowledge._cache:
        entries = knowledge.get_all(cat)
        if entries:
            all_knowledge[cat] = entries

    session = {
        "session": session_num,
        "timestamp": timestamp,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "duration_minutes": round(session_minutes, 1),
        "goal": goal,
        "knowledge": all_knowledge,
    }

    filename = f"session_{session_num:03d}_{timestamp}.json"
    filepath = os.path.join(LOG_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)

    logging.getLogger(__name__).info(f"Adventure log saved: {filename}")
    return filepath


def list_sessions() -> list[dict]:
    """List all saved sessions, sorted by time."""
    _ensure_dir()
    sessions = []
    for f in sorted(Path(LOG_DIR).glob("session_*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                sessions.append({
                    "file": f.name,
                    "session": data.get("session", 0),
                    "date": data.get("date", ""),
                    "duration": data.get("duration_minutes", 0),
                    "goal": data.get("goal", "")[:100],
                })
        except (json.JSONDecodeError, IOError):
            pass
    return sessions


def load_session(session_num: Optional[int] = None) -> Optional[dict]:
    """Load a specific session (or the latest if no number given)."""
    _ensure_dir()
    files = sorted(Path(LOG_DIR).glob("session_*.json"))
    if not files:
        return None

    if session_num is not None:
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if data.get("session") == session_num:
                        return data
            except (json.JSONDecodeError, IOError):
                pass
        return None
    else:
        # Load latest
        try:
            with open(files[-1], "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, IOError):
            return None


def get_adventure_summary(max_sessions: int = 5) -> str:
    """Generate a narrative summary of prior sessions for the system prompt."""
    _ensure_dir()
    files = sorted(Path(LOG_DIR).glob("session_*.json"))
    if not files:
        return ""

    # Load the last N sessions
    sessions = []
    for f in files[-max_sessions:]:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                sessions.append(json.load(fh))
        except (json.JSONDecodeError, IOError):
            pass

    if not sessions:
        return ""

    parts = [f"## Prior Adventures ({len(sessions)} sessions)\n"]

    for s in sessions:
        parts.append(f"### Session {s.get('session', '?')} — {s.get('date', '?')} ({s.get('duration_minutes', 0):.0f} min)")
        parts.append(f"Goal: {s.get('goal', 'Unknown')}")

        knowledge = s.get("knowledge", {})

        # Show journal entries first (the narrative)
        journal = knowledge.get("journal", {})
        if journal:
            parts.append("Journal:")
            for key, val in journal.items():
                parts.append(f"  {val[:300]}")

        # Summarize other categories briefly
        for cat, entries in knowledge.items():
            if cat == "journal":
                continue
            if entries:
                keys = list(entries.keys())[:5]
                parts.append(f"{cat.capitalize()}: {', '.join(keys)}")

        parts.append("")

    return "\n".join(parts)
