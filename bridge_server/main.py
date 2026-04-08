#!/usr/bin/env python3
"""
Claude-OpenMW Bridge Server

Connects to OpenMW's bridge socket and runs a Claude agent that can
observe and control the game world.

Usage:
    python main.py [--port PORT] [--model MODEL] [--goal GOAL] [--verbose]
"""

import argparse
import asyncio
import logging
import sys

from connection import BridgeConnection
from game_state import GameState
from claude_agent import run_agent
from knowledge import KnowledgeBase
from adventure_log import save_session, get_adventure_summary
from director import ActionLog, run_director


def setup_logging(verbose: bool = False):
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # File handler — always writes full debug logs
    fh = logging.FileHandler("bridge_debug.log", mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S",
    ))
    root.addHandler(fh)

    # Console handler — only warnings+ unless --verbose
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.WARNING)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S",
    ))
    root.addHandler(ch)

    # Quiet down httpx/httpcore logs from anthropic SDK
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def async_main(args):
    conn = BridgeConnection()
    state = GameState()

    print(f"Connecting to OpenMW bridge on 127.0.0.1:{args.port}...")
    await conn.connect("127.0.0.1", args.port)
    print("Connected!")

    # Verify connection with a ping
    if await conn.ping(timeout=3.0):
        print("Bridge is responding.")
    else:
        print("Warning: No ping response. The bridge mod may not be loaded.")

    log = logging.getLogger("bridge")

    knowledge = KnowledgeBase()
    prior_count = sum(len(knowledge.get_all(cat)) for cat in knowledge._cache)
    if prior_count > 0:
        log.info(f"Loaded {prior_count} prior knowledge entries.")

    # Load goals from file if specified (one goal per line, executed sequentially)
    # Format: "1. Goal text" or just "Goal text" — index prefix is stripped
    goal_queue = []
    if args.goal_file:
        import re
        with open(args.goal_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Strip optional index prefix like "1. " or "1) "
                text = re.sub(r'^\d+[\.\)]\s*', '', line)
                if text:
                    goal_queue.append(text)
        if goal_queue:
            print(f"Goals loaded ({len(goal_queue)}):")
            for i, g in enumerate(goal_queue, 1):
                print(f"  {i}. {g}")
            print()
            args.goal = goal_queue.pop(0)

    # Load custom system prompt if specified
    custom_prompt = None
    if args.system_prompt:
        with open(args.system_prompt, "r", encoding="utf-8") as f:
            custom_prompt = f.read()
        log.info(f"Loaded system prompt from {args.system_prompt}")

    # Load prior adventure context
    adventure_context = get_adventure_summary()
    if adventure_context:
        log.info("Loaded prior adventure history.")

    # Set up cost tracker
    from cost_tracker import CostTracker
    cost_tracker = CostTracker(budget=args.budget)
    if args.budget > 0:
        print(f"Budget: ${args.budget:.2f} (will save and exit at ${args.budget - 10:.2f} spent)")

    # Set up director
    action_log = None
    director_queue = None
    if not args.no_director:
        action_log = ActionLog()
        director_queue = asyncio.Queue()
        log.info(f"Director enabled (interval: {args.director_interval}s)")

    print(f"Starting agent (model: {args.model})")
    if args.goal:
        print(f"Goal: {args.goal}")
    print("Logs: bridge_debug.log | Ctrl+C to stop\n")

    import time as _time
    session_start = _time.monotonic()

    director_task = None
    try:
        if args.multi_agent:
            from agents.orchestrator import run_orchestrator
            log.info(f"Starting multi-agent orchestrator (sub-agent model: {args.model})")
            agent_task = asyncio.create_task(run_orchestrator(
                conn, state,
                model="claude-haiku-4-5-20251001",
                goal=args.goal,
                knowledge=knowledge,
                sub_agent_model=args.model,
                time_limit_minutes=args.time_limit,
                director_queue=director_queue,
                action_log=action_log,
                pace=args.pace,
                goal_queue=goal_queue,
                cost_tracker=cost_tracker,
                goal_file=args.goal_file,
            ))
        else:
            agent_task = asyncio.create_task(run_agent(
                conn, state,
                model=args.model,
                goal=args.goal,
                knowledge=knowledge,
                time_limit_minutes=args.time_limit,
                system_prompt_override=custom_prompt,
                adventure_context=adventure_context,
                action_log=action_log,
                director_queue=director_queue,
                pace=args.pace,
                goal_queue=goal_queue,
                cost_tracker=cost_tracker,
                goal_file=args.goal_file,
            ))

        if not args.no_director:
            director_task = asyncio.create_task(run_director(
                state=state,
                action_log=action_log,
                injection_queue=director_queue,
                interval_seconds=args.director_interval,
                model="claude-haiku-4-5-20251001",
            ))

        await agent_task
    except KeyboardInterrupt:
        log.info("Stopping agent...")
    finally:
        if director_task is not None:
            director_task.cancel()
            try:
                await director_task
            except asyncio.CancelledError:
                pass
        session_minutes = (_time.monotonic() - session_start) / 60.0
        save_session(args.goal or "No goal set", knowledge, session_minutes)
        print(f"\n{cost_tracker.summary()}")
        await conn.disconnect()
        print("Disconnected.")


def main():
    parser = argparse.ArgumentParser(
        description="Claude-OpenMW Bridge Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--port", type=int, default=21003,
        help="OpenMW bridge port (default: 21003)",
    )
    parser.add_argument(
        "--model", type=str, default="claude-sonnet-4-20250514",
        help="Claude model to use (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--goal", type=str, default=None,
        help="Optional goal for the agent (e.g., 'Find Caius Cosades in Balmora')",
    )
    parser.add_argument(
        "--goal-file", type=str, default=None,
        help="Path to a file containing the goal text (overrides --goal)",
    )
    parser.add_argument(
        "--system-prompt", type=str, default=None,
        help="Path to a custom system prompt file (overrides default)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--time-limit", type=int, default=0,
        help="Session time limit in minutes (0 = unlimited)",
    )
    parser.add_argument(
        "--pace", type=float, default=3.0,
        help="Seconds to wait between agent turns (default: 3.0, higher = slower/cheaper)",
    )
    parser.add_argument(
        "--no-director", action="store_true",
        help="Disable the live director agent",
    )
    parser.add_argument(
        "--multi-agent", action="store_true",
        help="Use multi-agent architecture (orchestrator + sub-agents)",
    )
    parser.add_argument(
        "--budget", type=float, default=0.0,
        help="API budget in dollars (0 = unlimited). Saves and exits when $10 remains.",
    )
    parser.add_argument(
        "--director-interval", type=int, default=45,
        help="Seconds between director check-ins (default: 45)",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
