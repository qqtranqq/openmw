"""Alchemy sub-agent — brews potions from ingredients."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "alchemy.txt")
    with open(path, "r") as f:
        return f.read()


ALCHEMY_TOOLS = [
    {
        "name": "look_around",
        "description": "See nearby NPCs, items, and surroundings.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_ingredients",
        "description": "List all ingredients and apparatus in your inventory with their known effects. Use this first to see what you have to work with.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "preview_potion",
        "description": "Preview what effects a combination of ingredients would produce WITHOUT consuming them. Use this to test combinations before brewing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ingredients": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 2-4 ingredient record IDs (e.g. ['ingred_netch_leather_01', 'ingred_marshmerrow_01'])",
                },
            },
            "required": ["ingredients"],
        },
    },
    {
        "name": "brew_potion",
        "description": "Brew a potion from ingredients. This CONSUMES the ingredients. Preview first to check effects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the potion (e.g. 'Restore Health Potion')",
                },
                "ingredients": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of 2-4 ingredient record IDs",
                },
            },
            "required": ["name", "ingredients"],
        },
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby NPC or object.",
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
        "description": "Interact with a nearby object or NPC.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target"},
            },
            "required": ["target"],
        },
    },
]


async def run_alchemy(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the alchemy sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=ALCHEMY_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=25,
    )
