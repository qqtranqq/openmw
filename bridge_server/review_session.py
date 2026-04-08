#!/usr/bin/env python3
"""Review agent — analyzes session logs and suggests gameplay improvements.

Usage:
    python review_session.py [session_log.json]
    python review_session.py --latest
    python review_session.py --all
"""

import argparse
import json
import sys
from pathlib import Path

from anthropic import Anthropic

LOG_DIR = Path(__file__).parent / "session_logs"

REVIEW_PROMPT = """You are a gameplay analyst reviewing an AI agent's Morrowind session logs.
Analyze the session data and provide actionable feedback to improve the agent's play.

Focus on:

1. **Efficiency**: Are goals being completed quickly? Is there wasted movement or repeated actions?
   - Look at action counts per goal and failure rates
   - Identify goals that took too many actions or too long

2. **Failures**: What actions fail most often? What patterns lead to failure?
   - Check which tools have high failure rates
   - Look for repeated failures on the same action (stuck loops)

3. **Combat**: Is the agent dying? Loading saves frequently?
   - Deaths and loads indicate difficulty or poor combat strategy
   - Suggest equipment, spell, or tactical improvements

4. **Goal Selection**: Are goals appropriate for the character's level and equipment?
   - Check if goals match the character's capabilities
   - Suggest better goal sequencing

5. **Resource Management**: Is the agent managing gold, health potions, equipment?
   - Check gold trajectory in snapshots
   - Look for patterns of running low on resources

6. **Navigation**: Is the agent navigating efficiently?
   - Look for excessive navigate_to/approach failures
   - Suggest better routing or use of fast travel

7. **Bounty/Crime**: Is the agent accumulating bounty?
   - Any bounty > 0 in snapshots is a problem — it blocks services and provokes guards
   - Identify what caused it (stealing, attacking NPCs)
   - Flag if bounty was never cleared

Output a structured review with:
- **Session Summary**: Key stats (duration, goals completed, failure rate)
- **What Went Well**: Successful patterns to keep
- **Problems Found**: Specific issues with evidence from the data
- **Recommendations**: Concrete, actionable improvements (ordered by impact)
"""


def load_session(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def review_session(session_data: dict, model: str = "claude-haiku-4-5-20251001") -> str:
    client = Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=REVIEW_PROMPT,
        messages=[{
            "role": "user",
            "content": json.dumps(session_data, indent=2),
        }],
    )
    return response.content[0].text


def review_multiple(sessions: list[dict], model: str = "claude-haiku-4-5-20251001") -> str:
    client = Anthropic()
    content = "Review these sessions together and identify trends:\n\n"
    for i, s in enumerate(sessions):
        content += f"=== Session {i+1} ===\n{json.dumps(s, indent=2)}\n\n"

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=REVIEW_PROMPT + "\n\nYou are reviewing MULTIPLE sessions. Look for trends across sessions — recurring failures, improving or degrading performance, persistent bad habits.",
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


def main():
    parser = argparse.ArgumentParser(description="Review Morrowind session logs")
    parser.add_argument("session_file", nargs="?", help="Path to a session log JSON file")
    parser.add_argument("--latest", action="store_true", help="Review the most recent session")
    parser.add_argument("--all", action="store_true", help="Review all sessions together (trends)")
    parser.add_argument("--last", type=int, default=0, help="Review the last N sessions together")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Model for analysis")
    args = parser.parse_args()

    if args.session_file:
        path = Path(args.session_file)
        if not path.exists():
            print(f"File not found: {path}")
            sys.exit(1)
        session = load_session(path)
        print(review_session(session, args.model))

    elif args.latest:
        files = sorted(LOG_DIR.glob("session_*.json"))
        if not files:
            print("No session logs found.")
            sys.exit(1)
        session = load_session(files[-1])
        print(f"Reviewing: {files[-1].name}\n")
        print(review_session(session, args.model))

    elif args.all or args.last:
        files = sorted(LOG_DIR.glob("session_*.json"))
        if not files:
            print("No session logs found.")
            sys.exit(1)
        if args.last:
            files = files[-args.last:]
        sessions = [load_session(f) for f in files]
        print(f"Reviewing {len(sessions)} sessions\n")
        print(review_multiple(sessions, args.model))

    else:
        # List available sessions
        files = sorted(LOG_DIR.glob("session_*.json"))
        if not files:
            print("No session logs found in session_logs/")
            sys.exit(0)
        print(f"Available sessions ({len(files)}):\n")
        for f in files:
            session = load_session(f)
            stats = session.get("stats", {})
            goals_completed = stats.get("goals_completed", 0)
            total_actions = stats.get("total_actions", 0)
            fail_rate = stats.get("failure_rate", 0)
            duration = session.get("duration_minutes", 0)
            print(f"  {f.name}  {duration:.0f}min  {goals_completed} goals  {total_actions} actions  {fail_rate:.0%} fail")
        print(f"\nUsage: python review_session.py --latest")


if __name__ == "__main__":
    main()
