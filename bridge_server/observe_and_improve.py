#!/usr/bin/env python3
"""Observation & Improvement Agent — watches session logs and bridge debug logs,
identifies patterns of failure, and produces actionable improvement recommendations.

Can run post-session on logs, or periodically during a session.

Usage:
    # Post-session analysis
    python observe_and_improve.py --session-log session_logs/session_2026-04-07_19-43-00.json

    # Analyze debug log from a session
    python observe_and_improve.py --debug-log bridge_debug.log

    # Full analysis: session log + debug log + knowledge base
    python observe_and_improve.py --all

    # Watch mode: analyze every N minutes during a live session
    python observe_and_improve.py --watch --interval 5
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

SESSION_LOG_DIR = Path(__file__).parent / "session_logs"
GAMEPLAY_LOG_DIR = Path(__file__).parent / "gameplay_logs"
IMPROVEMENTS_FILE = Path(__file__).parent / "IMPROVEMENTS.md"
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

OBSERVER_PROMPT = """You are an expert gameplay analyst and software architect reviewing an AI agent system that plays Morrowind (OpenMW).

You analyze gameplay data to identify:

1. **CAPABILITY GAPS** — Things the agent tries to do but can't because it lacks a tool, action, or sub-agent.
   Examples: trying to pick a lock with no lockpick tool, trying to rest with no rest action, trying to persuade with no persuasion mechanic.

2. **BEHAVIORAL PATTERNS** — Recurring mistakes or inefficiencies in how the agent plays.
   Examples: always fleeing from combat it could win, never equipping found armor, ignoring quest items, getting lost in cities.

3. **STUCK LOOPS** — Situations where the agent repeats the same failing action.
   Examples: approaching the same door that doesn't open, attacking through a gate, casting spells that always fail.

4. **GOAL SYSTEM ISSUES** — Problems with how goals are managed, completed, or failed.
   Examples: skipping goals, not calling goal_complete, breaking down goals unnecessarily, getting stuck on impossible goals.

5. **COST INEFFICIENCIES** — API calls that waste budget without progressing.
   Examples: excessive look_around calls, sub-agents that fail and retry identically, long combat sequences at low skill levels.

6. **MISSING GAME KNOWLEDGE** — Things the agent doesn't know about Morrowind mechanics.
   Examples: not knowing about Morrowind's rest mechanic, not understanding enchantment charges, not knowing trainer locations.

For each issue found, produce a structured recommendation:
- **Issue**: What's happening
- **Evidence**: Specific data from the logs
- **Impact**: High/Medium/Low
- **Fix Type**: New Tool | New Agent | Prompt Change | Bug Fix | Knowledge Entry
- **Recommendation**: Specific actionable fix

Prioritize by impact. Be concrete — name specific files, functions, and code changes.
Output as markdown.
"""


def load_session_logs(max_sessions: int = 5) -> list[dict]:
    """Load recent session logs."""
    files = sorted(SESSION_LOG_DIR.glob("session_*.json"))
    sessions = []
    for f in files[-max_sessions:]:
        try:
            with open(f, "r") as fh:
                sessions.append(json.load(fh))
        except (json.JSONDecodeError, IOError):
            pass
    return sessions


def load_debug_log(path: str = "bridge_debug.log", max_lines: int = 500) -> str:
    """Load and summarize the debug log."""
    log_path = Path(__file__).parent / path
    if not log_path.exists():
        return ""
    with open(log_path, "r") as f:
        lines = f.readlines()

    # Filter to INFO lines, skip connection/anthropic noise
    filtered = []
    for line in lines:
        if "INFO" in line and not any(skip in line for skip in ["connection", "anthropic", "httpx", "httpcore"]):
            filtered.append(line.strip())

    # Take last N lines
    return "\n".join(filtered[-max_lines:])


def load_knowledge_summary() -> str:
    """Load a summary of the knowledge base."""
    if not KNOWLEDGE_DIR.exists():
        return ""
    parts = []
    for f in sorted(KNOWLEDGE_DIR.glob("*.json")):
        try:
            with open(f, "r") as fh:
                data = json.load(fh)
                parts.append(f"{f.stem}: {len(data)} entries")
        except (json.JSONDecodeError, IOError):
            pass
    return "\n".join(parts) if parts else ""


def load_gameplay_log(max_events: int = 200) -> str:
    """Load the most recent gameplay JSONL log."""
    if not GAMEPLAY_LOG_DIR.exists():
        return ""
    files = sorted(GAMEPLAY_LOG_DIR.glob("gameplay_*.jsonl"))
    if not files:
        return ""
    lines = []
    with open(files[-1], "r") as f:
        for line in f:
            lines.append(line.strip())
    # Take last N events
    recent = lines[-max_events:]
    return "\n".join(recent)


def load_existing_improvements() -> str:
    """Load existing improvements file."""
    if IMPROVEMENTS_FILE.exists():
        with open(IMPROVEMENTS_FILE, "r") as f:
            return f.read()
    return ""


def analyze(
    session_data: Optional[list[dict]] = None,
    debug_log: str = "",
    knowledge_summary: str = "",
    existing_improvements: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> str:
    """Run the observation agent analysis."""
    client = Anthropic()

    context_parts = []

    if session_data:
        for i, session in enumerate(session_data):
            # Compact the session data
            compact = {
                "duration_minutes": session.get("duration_minutes"),
                "model": session.get("model"),
                "stats": session.get("stats"),
                "goals": [
                    {
                        "goal": g.get("goal"),
                        "outcome": g.get("outcome"),
                        "summary": g.get("summary"),
                        "duration_minutes": g.get("duration_minutes"),
                        "action_count": g.get("action_count"),
                        "failures": g.get("failures"),
                        # Include first 5 actions for pattern analysis
                        "sample_actions": [
                            {"tool": a["tool"], "success": a["success"], "result": a["result"][:100]}
                            for a in g.get("actions", [])[:5]
                        ],
                    }
                    for g in session.get("goals", [])
                ],
                "snapshots": session.get("snapshots", [])[:5],
            }
            context_parts.append(f"=== SESSION {i+1} ===\n{json.dumps(compact, indent=2)}")

    if debug_log:
        context_parts.append(f"=== DEBUG LOG (recent) ===\n{debug_log[-5000:]}")

    if knowledge_summary:
        context_parts.append(f"=== KNOWLEDGE BASE ===\n{knowledge_summary}")

    if existing_improvements:
        context_parts.append(f"=== EXISTING IMPROVEMENT NOTES ===\n{existing_improvements[-3000:]}")

    # Include live gameplay log if available
    gameplay_log = load_gameplay_log()
    if gameplay_log:
        context_parts.append(f"=== LIVE GAMEPLAY LOG (JSONL) ===\n{gameplay_log[-5000:]}")

    if not context_parts:
        return "No data to analyze."

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=OBSERVER_PROMPT,
        messages=[{"role": "user", "content": "\n\n".join(context_parts)}],
    )

    return response.content[0].text


SHUTDOWN_FILE = Path(__file__).parent / ".shutdown"
TRACKER_FILE = Path(__file__).parent / "improvement_tracker.json"


def load_tracker() -> dict:
    """Load the improvement tracker."""
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    return {"improvements": []}


def save_tracker(tracker: dict):
    """Save the improvement tracker."""
    with open(TRACKER_FILE, "w") as f:
        json.dump(tracker, f, indent=2, ensure_ascii=False)


def mark_implemented(issue_id: int):
    """Mark an improvement as implemented."""
    tracker = load_tracker()
    for imp in tracker["improvements"]:
        if imp["id"] == issue_id:
            imp["status"] = "implemented"
            imp["implemented_at"] = time.strftime("%Y-%m-%d %H:%M")
            save_tracker(tracker)
            print(f"Marked #{issue_id} as implemented: {imp['title']}")
            return
    print(f"Issue #{issue_id} not found")


def show_tracker():
    """Print the improvement tracker status."""
    tracker = load_tracker()
    if not tracker["improvements"]:
        print("No improvements tracked yet.")
        return
    open_count = sum(1 for i in tracker["improvements"] if i["status"] == "open")
    done_count = sum(1 for i in tracker["improvements"] if i["status"] == "implemented")
    print(f"Improvements: {open_count} open, {done_count} implemented\n")
    for imp in tracker["improvements"]:
        status = "DONE" if imp["status"] == "implemented" else "OPEN"
        print(f"  [{status}] #{imp['id']} ({imp['impact']}) {imp['title']}")
        if imp["status"] == "implemented":
            print(f"         implemented {imp.get('implemented_at', '?')}")


def add_improvement(title: str, impact: str, fix_type: str, recommendation: str):
    """Add a new improvement to the tracker."""
    tracker = load_tracker()
    new_id = max([i["id"] for i in tracker["improvements"]], default=0) + 1
    tracker["improvements"].append({
        "id": new_id,
        "title": title,
        "impact": impact,
        "fix_type": fix_type,
        "recommendation": recommendation[:500],
        "status": "open",
        "found_at": time.strftime("%Y-%m-%d %H:%M"),
    })
    save_tracker(tracker)
    return new_id


def send_shutdown(reason: str = "Improvement agent requested shutdown"):
    """Signal the running bridge to save and quit gracefully."""
    with open(SHUTDOWN_FILE, "w") as f:
        f.write(reason)
    print(f"Shutdown signal sent: {reason}")
    print(f"The bridge will save the game and write a resume file on its next loop iteration.")


def main():
    parser = argparse.ArgumentParser(description="Observation & Improvement Agent")
    parser.add_argument("--session-log", type=str, help="Path to a specific session log JSON file")
    parser.add_argument("--debug-log", type=str, default="bridge_debug.log", help="Path to debug log")
    parser.add_argument("--shutdown", type=str, nargs="?", const="Improvement agent requested shutdown",
        help="Signal the bridge to save and quit. Optional reason string.")
    parser.add_argument("--status", action="store_true",
        help="Show improvement tracker status")
    parser.add_argument("--done", type=int, metavar="ID",
        help="Mark an improvement as implemented by ID")
    parser.add_argument("--all", action="store_true", help="Analyze everything: recent sessions + debug log + knowledge")
    parser.add_argument("--watch", action="store_true", help="Watch mode: analyze periodically during a live session")
    parser.add_argument("--interval", type=int, default=5, help="Minutes between analyses in watch mode (default: 5)")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-20250514", help="Model for analysis")
    parser.add_argument("--output", type=str, default=None, help="Write output to file instead of stdout")
    args = parser.parse_args()

    if args.shutdown is not None:
        send_shutdown(args.shutdown)
        return

    if args.status:
        show_tracker()
        return

    if args.done is not None:
        mark_implemented(args.done)
        return

    if args.watch:
        print(f"Watch mode: analyzing every {args.interval} minutes. Auto-shutdown after 5 NEW improvements found. Ctrl+C to stop.\n")
        total_new_improvements = 0

        # Wait for the first interval before analyzing — let the bridge play first
        print(f"Waiting {args.interval} minutes for gameplay data to accumulate...")
        time.sleep(args.interval * 60)

        while True:
            try:
                print(f"\n{'='*60}")
                print(f"Analysis at {time.strftime('%H:%M:%S')}")
                print(f"{'='*60}\n")

                # Only analyze gameplay log (live data), not existing improvements
                gameplay_log = load_gameplay_log()
                if not gameplay_log:
                    print("No gameplay data yet, waiting...")
                    time.sleep(args.interval * 60)
                    continue

                result = analyze(
                    session_data=load_session_logs(1),
                    debug_log=load_debug_log(args.debug_log),
                    knowledge_summary="",
                    existing_improvements=load_existing_improvements(),
                    model=args.model,
                )
                print(result)
                if args.output:
                    with open(args.output, "a") as f:
                        f.write(f"\n\n## Analysis at {time.strftime('%Y-%m-%d %H:%M')}\n\n")
                        f.write(result)

                # Parse and track improvements from this analysis
                import re
                # Find structured issues: "### N. Title" or "**Issue**: text"
                issues = re.findall(
                    r'###\s+\d+\.\s+(.+?)(?:\n|$).*?\*\*Impact\*\*:\s*(\w+).*?\*\*Fix Type\*\*:\s*(.+?)(?:\n|$).*?\*\*Recommendation\*\*:\s*(.+?)(?:\n\n|$)',
                    result, re.DOTALL
                )
                tracker_data = load_tracker()
                existing_titles = {i["title"].lower() for i in tracker_data["improvements"]}
                count_this_round = 0
                for title, impact, fix_type, rec in issues:
                    title = title.strip()
                    if title.lower() not in existing_titles:
                        new_id = add_improvement(title, impact.strip(), fix_type.strip(), rec.strip())
                        print(f"  NEW #{new_id}: [{impact.strip()}] {title}")
                        count_this_round += 1
                        existing_titles.add(title.lower())

                total_new_improvements += count_this_round
                print(f"\nNew improvements this round: {count_this_round}")
                print(f"Total improvements found: {total_new_improvements}/5")
                show_tracker()

                if total_new_improvements >= 5:
                    print(f"\n{'='*60}")
                    print(f"5 improvements identified — sending shutdown signal to bridge")
                    print(f"{'='*60}")
                    send_shutdown(
                        f"Improvement agent found {total_new_improvements} issues to fix. "
                        f"Saving game so improvements can be implemented."
                    )
                    if args.output:
                        with open(args.output, "a") as f:
                            f.write(f"\n\n## AUTO-SHUTDOWN at {time.strftime('%Y-%m-%d %H:%M')}\n")
                            f.write(f"Triggered after finding {total_new_improvements} improvements.\n")
                    break

                time.sleep(args.interval * 60)
            except KeyboardInterrupt:
                print("\nStopped.")
                break
        return

    session_data = None
    debug_log = ""
    knowledge_summary = ""
    existing_improvements = ""

    if args.session_log:
        with open(args.session_log, "r") as f:
            session_data = [json.load(f)]
    elif args.all:
        session_data = load_session_logs(5)
        debug_log = load_debug_log(args.debug_log)
        knowledge_summary = load_knowledge_summary()
        existing_improvements = load_existing_improvements()
    else:
        # Default: analyze debug log + recent sessions
        session_data = load_session_logs(3)
        debug_log = load_debug_log(args.debug_log)
        existing_improvements = load_existing_improvements()

    result = analyze(
        session_data=session_data,
        debug_log=debug_log,
        knowledge_summary=knowledge_summary,
        existing_improvements=existing_improvements,
        model=args.model,
    )

    print(result)

    if args.output:
        with open(args.output, "w") as f:
            f.write(f"# Observation & Improvement Report\n")
            f.write(f"## Generated: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
            f.write(result)
        print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
