"""Combat sub-agent — handles fighting, fleeing, and healing."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "combat.txt")
    with open(path, "r") as f:
        return f.read()


COMBAT_TOOLS = [
    {
        "name": "look_around",
        "description": "Get observation of surroundings — see enemies, health bars, distances, your own status.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "approach",
        "description": "Walk toward a target to get in melee range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the enemy to approach"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "attack",
        "description": "Start AI melee combat against a target. The engine handles movement, facing, and weapon swings. Defaults to melee_only (no spells) — cast spells manually first, then use this for melee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of enemy to attack",
                },
                "duration": {
                    "type": "number",
                    "description": "Max seconds to keep fighting (default 15)",
                },
                "melee_only": {
                    "type": "boolean",
                    "description": "Force melee weapons only — no spells. Use when magic skill is too low to hit.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "cast_spell",
        "description": "Cast a spell by name. Auto-faces the target if specified. Check your spells list for available spells — note the range type (Touch = must be in melee range, Ranged = can cast from distance, Self = affects you).",
        "input_schema": {
            "type": "object",
            "properties": {
                "spell": {
                    "type": "string",
                    "description": "Spell name (e.g. 'Frostbite', 'Restore Health')",
                },
                "target": {
                    "type": "string",
                    "description": "Name of enemy to face while casting (for offensive spells)",
                },
            },
            "required": ["spell"],
        },
    },
    {
        "name": "equip_item",
        "description": "Equip a weapon or armor from inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name of item to equip"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "use_item",
        "description": "Use a potion, scroll, or ingredient from inventory. Good for healing mid-combat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name of item to use"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "move",
        "description": "Move in a direction. Use for positioning and fleeing.",
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
                    "description": "Seconds to move (default 1.0)",
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
        "name": "jump",
        "description": "Jump. Can help escape or reposition.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "open_and_enter",
        "description": "Open an interior door and walk through it. Use for physical doors inside caves and dungeons.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Door name (default: 'Door')"},
            },
            "required": [],
        },
    },
    {
        "name": "stop",
        "description": "Stop all movement.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


async def run_combat(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the combat sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=COMBAT_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=30,
    )
