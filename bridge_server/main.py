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


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
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

    knowledge = KnowledgeBase()
    prior_count = sum(len(knowledge.get_all(cat)) for cat in knowledge._cache)
    if prior_count > 0:
        print(f"Loaded {prior_count} prior knowledge entries.")

    print(f"Starting Claude agent (model: {args.model})...")
    if args.goal:
        print(f"Goal: {args.goal}")
    print("Press Ctrl+C to stop.\n")

    try:
        await run_agent(conn, state, model=args.model, goal=args.goal, knowledge=knowledge)
    except KeyboardInterrupt:
        print("\nStopping agent...")
    finally:
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
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
