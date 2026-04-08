"""Shopping sub-agent — buys and sells items at merchant NPCs."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "shopping.txt")
    with open(path, "r") as f:
        return f.read()


SHOPPING_TOOLS = [
    {
        "name": "look_around",
        "description": "See nearby NPCs with their services, distances, and directions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby NPC or door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target to walk toward"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "get_merchant_inventory",
        "description": "List a nearby merchant's inventory with item names, values, weights, and the merchant's available gold.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the merchant NPC"},
            },
            "required": ["npc"],
        },
    },
    {
        "name": "buy_item",
        "description": "Buy an item from a nearby merchant. Costs the item's base value in gold.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the merchant NPC"},
                "item": {"type": "string", "description": "Name of the item to buy"},
                "count": {"type": "integer", "description": "How many to buy (default: 1)"},
            },
            "required": ["npc", "item"],
        },
    },
    {
        "name": "sell_item",
        "description": "Sell an item from your inventory to a nearby merchant. Sells at roughly half base value.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the merchant NPC"},
                "item": {"type": "string", "description": "Name of the item to sell"},
                "count": {"type": "integer", "description": "How many to sell (default: 1)"},
            },
            "required": ["npc", "item"],
        },
    },
    {
        "name": "activate",
        "description": "Interact with a nearby object or door.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the target"},
            },
            "required": ["target"],
        },
    },
]


async def run_shopping(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the shopping sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=SHOPPING_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=20,
    )
