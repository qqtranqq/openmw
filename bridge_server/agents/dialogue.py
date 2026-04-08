"""Dialogue sub-agent — handles NPC conversations."""

import os
from typing import Optional

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
from agents.base import SubAgentResult, run_sub_agent

from claude_agent import execute_tool as _base_execute_tool


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "dialogue.txt")
    with open(path, "r") as f:
        return f.read()


DIALOGUE_TOOLS = [
    {
        "name": "look_around",
        "description": "Get observation of surroundings — see nearby NPCs and their distances.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby NPC to get close enough to talk.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the NPC to walk toward"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "talk_to",
        "description": "Start a conversation with a nearby NPC. Shows greeting and available topics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the NPC to talk to"},
            },
            "required": ["npc"],
        },
    },
    {
        "name": "select_topic",
        "description": "Ask about a dialogue topic during conversation. Use exact topic name from available topics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic name to ask about"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "answer_choice",
        "description": "Answer a yes/no or multiple choice question during dialogue. The NPC is presenting choices — use the choice ID from the choices list. If unsure, use get_choices first to see available options.",
        "input_schema": {
            "type": "object",
            "properties": {
                "choice_id": {
                    "type": "integer",
                    "description": "The ID of the choice to select (from the choices list)",
                },
            },
            "required": ["choice_id"],
        },
    },
    {
        "name": "close_dialogue",
        "description": "End the current conversation. MUST call this before done.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "persuade",
        "description": "Persuade the NPC during dialogue to raise or lower their disposition. Actions: admire (raise disposition), intimidate (raise disposition via fear), taunt (lower disposition, may provoke attack), bribe10/bribe100/bribe1000 (raise disposition, costs gold).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["admire", "intimidate", "taunt", "bribe10", "bribe100", "bribe1000"],
                    "description": "The persuasion action to perform",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "lookup",
        "description": "Search game knowledge for NPC info, quest details.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
]


async def run_dialogue(
    goal: str,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
) -> SubAgentResult:
    """Run the dialogue sub-agent to completion."""
    return await run_sub_agent(
        goal=goal,
        system_prompt=_load_prompt(),
        tools=DIALOGUE_TOOLS,
        tool_executor=_base_execute_tool,
        conn=conn,
        state=state,
        knowledge=knowledge,
        model=model,
        max_turns=20,
    )
