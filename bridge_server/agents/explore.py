"""Exploration sub-agent — explores the overworld, discovers locations, collects resources."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "explore.txt")
    with open(path, "r") as f:
        return f.read()


EXPLORE_TOOLS = [
    {
        "name": "look_around",
        "description": "See your surroundings — nearby creatures, items, doors, landmarks with distances and directions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "move",
        "description": "Walk or run in a direction to explore. Use longer durations to cover more ground.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "backward", "left", "right"],
                    "description": "Direction to move",
                },
                "duration": {
                    "type": "number",
                    "description": "Seconds to move (default 1.0, use 3-5 for exploration)",
                },
                "run": {
                    "type": "boolean",
                    "description": "Whether to run (default true)",
                },
            },
            "required": ["direction"],
        },
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby object, creature, door, or item to get close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "activate",
        "description": "Pick up an item, open a container, or interact with an object. Must be close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of item/object"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "take_all",
        "description": "Take all items from a container (barrel, crate, chest, dead body).",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the container"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "stop",
        "description": "Stop moving.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "turn",
        "description": "Turn to face a different direction. Use to scan around you.",
        "input_schema": {
            "type": "object",
            "properties": {
                "angle": {
                    "type": "number",
                    "description": "Radians to turn. Positive=right, negative=left. ~1.57=90 degrees.",
                },
            },
            "required": ["angle"],
        },
    },
]


async def run_explore(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the exploration sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=EXPLORE_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=30,
    )
