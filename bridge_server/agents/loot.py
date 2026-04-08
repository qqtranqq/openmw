"""Looting sub-agent — picks up items, loots bodies, searches containers."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "loot.txt")
    with open(path, "r") as f:
        return f.read()


LOOT_TOOLS = [
    {
        "name": "look_around",
        "description": "See nearby items, dead bodies, containers, doors with distances and directions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby item, body, container, or door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target to walk toward"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "activate",
        "description": "Pick up a ground item or interact with an object. Must be close — use approach first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of item/object to interact with"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "get_contents",
        "description": "See what items are inside a container (chest, crate, barrel) or dead body. Must be close.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the container or dead NPC"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "take_all",
        "description": "Take ALL items from a container or dead body. Must be close — use approach first.",
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
        "name": "open_and_enter",
        "description": "Open an interior door and walk through it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Door/gate name (default: 'Door')"},
            },
            "required": [],
        },
    },
    {
        "name": "drop_item",
        "description": "Drop an item from inventory onto the ground. Use to make room for better loot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name of item to drop"},
                "count": {"type": "integer", "description": "How many to drop (default: 1)"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "pick_lock",
        "description": "Attempt to pick the lock on a nearby container or door using your best lockpick. Requires a lockpick in inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the locked container or door"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "disarm_trap",
        "description": "Attempt to disarm a trap on a nearby container or door using your best probe. Always disarm traps BEFORE picking locks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the trapped container or door"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "stop",
        "description": "Stop moving.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


async def run_loot(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the looting sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=LOOT_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=20,
    )
