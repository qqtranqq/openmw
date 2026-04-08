"""Navigation sub-agent — handles all movement and pathfinding."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

# Import the shared tool executor
from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "navigation.txt")
    with open(path, "r") as f:
        return f.read()


NAV_TOOLS = [
    {
        "name": "look_around",
        "description": "Get observation of surroundings — nearby NPCs, doors, items with distances and directions.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_area",
        "description": "Scan surroundings in all directions to find doors, NPCs, items, and containers. Does a 360-degree turn with observations. Good for finding specific buildings in cities.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "navigate_to",
        "description": "Auto-walk to a named city/town using navmesh pathfinding. ONLY for traveling between cities.",
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {"type": "string", "description": "City/town name (e.g. 'Balmora')"},
            },
            "required": ["destination"],
        },
    },
    {
        "name": "navigate_to_npc",
        "description": "Auto-walk to a nearby NPC using navmesh pathfinding. NPC must be visible in observations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the NPC to walk to"},
            },
            "required": ["npc"],
        },
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby object/NPC/door. Stops when close enough to interact. For short distances only.",
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
        "description": "Interact with a nearby object — open doors, pick up items. Use full name from observations. Do NOT use on NPCs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the object/door to interact with"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "open_and_enter",
        "description": "Open an interior door and walk through it. Use for physical doors inside caves, dungeons, and buildings that say just 'Door' in observations.",
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
        "description": "Stop any current movement.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_travel_destinations",
        "description": "Get fast travel destinations from a nearby travel NPC. Shows names and prices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the travel NPC"},
            },
            "required": ["npc"],
        },
    },
    {
        "name": "travel",
        "description": "Use fast travel service to teleport to a destination. Costs gold, advances game time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the travel NPC"},
                "destination": {"type": "string", "description": "Destination name"},
            },
            "required": ["npc", "destination"],
        },
    },
    {
        "name": "pick_lock",
        "description": "Attempt to pick the lock on a nearby door using your best lockpick. Requires a lockpick in inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the locked door"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "lookup",
        "description": "Search game knowledge for travel routes, NPC locations, city info.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
]


async def run_navigation(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the navigation sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=NAV_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=30,
    )
