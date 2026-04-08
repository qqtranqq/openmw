"""Stealth sub-agent — sneaks, steals items, and picks locks."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "stealth.txt")
    with open(path, "r") as f:
        return f.read()


STEALTH_TOOLS = [
    {
        "name": "look_around",
        "description": "See nearby NPCs, items, containers with distances, directions, and ownership tags.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby item, container, door, or NPC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target to walk toward"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "sneak",
        "description": "Toggle sneaking on or off. Enable before approaching steal targets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "enable": {"type": "boolean", "description": "True to start sneaking, False to stop (default: True)"},
            },
            "required": [],
        },
    },
    {
        "name": "activate",
        "description": "Interact with a nearby object, door, or pick up an item. Must be close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target to interact with"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "get_contents",
        "description": "See what items are inside a container or dead body. Must be close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the container or dead NPC"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "take_item",
        "description": "Take a specific item from a container or dead body.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the container or dead NPC"},
                "item": {"type": "string", "description": "Name of the item to take"},
                "count": {"type": "integer", "description": "How many to take (default: 1)"},
            },
            "required": ["target", "item"],
        },
    },
    {
        "name": "take_all",
        "description": "Take ALL items from a container or dead body. Must be close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the container or dead NPC"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "move",
        "description": "Move in a direction for a duration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "description": "Direction: forward, backward, left, right"},
                "duration": {"type": "number", "description": "Seconds to move (default: 1.0)"},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "open_and_enter",
        "description": "Open a door and walk through it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Door/gate name (default: 'Door')"},
            },
            "required": [],
        },
    },
    {
        "name": "stop",
        "description": "Stop moving.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


async def run_stealth(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the stealth sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=STEALTH_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=20,
    )
