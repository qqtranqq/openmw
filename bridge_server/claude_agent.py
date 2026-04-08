"""Claude agent loop for controlling OpenMW."""

import asyncio
import json
import logging
import os
from typing import Optional

from anthropic import AsyncAnthropic

from connection import BridgeConnection
from game_state import GameState
import action_builder
from knowledge import KnowledgeBase
from goal_generator import generate_goal, suggest_replacement_goal
from session_tracker import SessionTracker
from gameplay_log import GameplayLog

logger = logging.getLogger(__name__)


def _auto_save_doors(state, knowledge):
    """Auto-save teleport door locations to knowledge base for navigation memory."""
    nb = state.nearby
    if not nb:
        return
    doors = nb.get("doors", [])
    cell = state.player.get("cell", "") if state.player else ""
    if not cell:
        return
    for door in doors:
        dest = door.get("destination", "")
        if not dest:
            continue
        name = door.get("name", "")
        dist = door.get("distance", "?")
        direction = door.get("direction", "")
        key = f"door:{dest}"
        # Only save if not already known
        existing = knowledge.load("locations", key)
        if not existing:
            value = f"Door to '{dest}' found in {cell}, {dist}m {direction}"
            knowledge.save("locations", key, value)
            logger.info(f"Auto-saved door: {key} -> {value}")


# Tool definitions for Claude — navigation-focused
TOOLS = [
    {
        "name": "look_around",
        "description": "Get a detailed observation of your surroundings including nearby NPCs, creatures, doors, items, and activators with their distances and compass directions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "search_area",
        "description": "Scan surroundings in all directions to find doors, NPCs, items, and containers. Does a 360-degree turn with observations. Good for finding specific buildings in cities.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "navigate_to",
        "description": "Auto-walk to a named city/town using navmesh pathfinding. ONLY use for traveling between cities. Once in a city, use look_around + approach to find specific buildings and doors. Known locations: Seyda Neen, Balmora, Vivec, Ald-Ruhn, Caldera, Suran, Pelagiad, Gnisis, Molag Mar, Sadrith Mora, Ebonheart, Hla Oad, Tel Mora, Maar Gan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "City/town name (e.g. 'Balmora'). Do NOT pass coordinates.",
                },
            },
            "required": ["destination"],
        },
    },
    {
        "name": "navigate_to_npc",
        "description": "Auto-walk to a nearby NPC using navmesh pathfinding. The NPC must be visible in your observations (use look_around first). Uses name matching — partial names work.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {
                    "type": "string",
                    "description": "Name of the NPC to walk to (e.g. 'Fargoth', 'Caius')",
                },
            },
            "required": ["npc"],
        },
    },
    {
        "name": "approach",
        "description": "Walk toward a nearby object, NPC, or door. Automatically faces and walks to the target. Stops when close enough to interact (~150 units). Good for short-range movement to things you can see.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the object/NPC/door to walk toward",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "activate",
        "description": "Interact with a nearby object — open doors, talk to NPCs, pick up items. Target must be close (within ~200 units). Use 'approach' first if the target is far.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the object/NPC/door to interact with",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "stop",
        "description": "Immediately stop any current movement or navigation and stand still.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_travel_destinations",
        "description": "Get available fast travel destinations from a nearby travel NPC (silt strider operator, boat captain, or Mages Guild guide). Shows destination names and prices. The NPC must be nearby (~1000 units).",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {
                    "type": "string",
                    "description": "Name of the travel NPC (e.g. 'Darvame Hleran', 'Selvil Sareloth')",
                },
            },
            "required": ["npc"],
        },
    },
    {
        "name": "travel",
        "description": "Use a fast travel service to teleport to a destination. Costs gold and advances game time. The travel NPC must be nearby. Use get_travel_destinations first to see available destinations and prices.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {
                    "type": "string",
                    "description": "Name of the travel NPC",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination name (must match one from get_travel_destinations)",
                },
            },
            "required": ["npc", "destination"],
        },
    },
    {
        "name": "talk_to",
        "description": "Start a conversation with a nearby NPC. Opens dialogue, gets their greeting, and lists available topics. You MUST be close to the NPC (use approach first). After talking, you MUST use close_dialogue before you can move.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {
                    "type": "string",
                    "description": "Name of the NPC to talk to",
                },
            },
            "required": ["npc"],
        },
    },
    {
        "name": "select_topic",
        "description": "During a conversation, ask about a specific topic. Use the exact topic name from the available topics list returned by talk_to. Returns the NPC's response text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The dialogue topic to ask about (must match available topics exactly)",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "close_dialogue",
        "description": "End the current conversation. You MUST call this after talking to an NPC before you can move or do anything else.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_contents",
        "description": "See what items are inside a nearby container (chest, crate, barrel) or dead body. Must be close — use approach first.",
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
        "description": "Take a specific item from a nearby container or dead body.",
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
        "name": "drop_item",
        "description": "Drop an item from your inventory onto the ground. Use to make room when over-encumbered.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Name of the item to drop"},
                "count": {"type": "integer", "description": "How many to drop (default: 1)"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "take_all",
        "description": "Take ALL items from a nearby container or dead body. Must be close — use approach first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Name of the container or dead NPC"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "check_journal",
        "description": "Read your quest journal. Without a quest ID, returns all journal entries. With a quest ID, returns only entries for that quest. Use this to review quest objectives and figure out what to do next.",
        "input_schema": {
            "type": "object",
            "properties": {
                "quest": {
                    "type": "string",
                    "description": "Quest ID to filter by (e.g. 'a1_1_findspymaster'). Leave empty to see all entries.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "lookup",
        "description": "Search the game knowledge base for information about travel routes, NPC locations, city services, quest details, items, and strategies. Use this BEFORE navigating to plan your route — e.g. lookup 'travel from Seyda Neen' or lookup 'Caius Cosades' or lookup 'Balmora'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — NPC name, city name, 'travel from X', item name, quest name, etc.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "execute_plan",
        "description": (
            "Execute a sequence of actions back-to-back without waiting between steps. "
            "Use this when you have a clear multi-step plan. Steps use the same tool names "
            "as individual tools. Execution stops on the first failure unless on_failure='skip'. "
            "Good examples: approach NPC → talk_to → select_topic → close_dialogue, or "
            "navigate_to city → look_around → approach door → activate. "
            "Don't include lookup as a plan step (you need to read results before acting). "
            "Keep plans under 10 steps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered list of actions to execute",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "Tool name (e.g. 'approach', 'activate', 'talk_to', 'navigate_to')",
                            },
                            "input": {
                                "type": "object",
                                "description": "Tool input parameters",
                            },
                            "on_failure": {
                                "type": "string",
                                "enum": ["abort", "skip"],
                                "description": "abort (default) stops the plan on failure, skip continues to next step",
                            },
                        },
                        "required": ["tool"],
                    },
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "pick_lock",
        "description": "Attempt to pick the lock on a nearby container or door using your best lockpick. Requires a lockpick in inventory. Success depends on your Security skill, the lock level, and lockpick quality. Must be close to the target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the locked container or door",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "disarm_trap",
        "description": "Attempt to disarm a trap on a nearby container or door using your best probe. Requires a probe in inventory. Success depends on your Security skill. Always disarm traps BEFORE picking locks or opening containers to avoid taking damage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the trapped container or door",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "goal_complete",
        "description": (
            "Signal that your current goal is done. Set success=true if accomplished, "
            "success=false if it failed (too hard, not enough gold, can't find target, etc.). "
            "Failed goals will be retried later after a prerequisite goal is completed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {
                    "type": "boolean",
                    "description": "True if goal was accomplished, false if it failed",
                },
                "summary": {
                    "type": "string",
                    "description": "What you accomplished, or why the goal failed (e.g. 'Not enough gold to buy potions', 'Enemy too strong at level 1')",
                },
            },
            "required": ["success", "summary"],
        },
    },
]

def _load_system_prompt():
    """Load system prompt from file, falling back to a default."""
    import os
    prompt_path = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "You are playing The Elder Scrolls III: Morrowind through OpenMW. Explore, talk to NPCs, and complete quests."

SYSTEM_PROMPT = _load_system_prompt()


async def _execute_plan(
    steps: list[dict],
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
) -> str:
    """Execute a multi-step plan sequentially, returning combined results."""
    results = []
    for i, step in enumerate(steps):
        tool_name = step["tool"]
        tool_input = step.get("input", {})
        on_failure = step.get("on_failure", "abort")

        logger.info(f"Plan step {i+1}/{len(steps)}: {tool_name}({tool_input})")

        try:
            result_text = await execute_tool(tool_name, tool_input, conn, state, knowledge)
        except Exception as e:
            result_text = f"Error: {e}"

        results.append(f"Step {i+1} [{tool_name}]: {result_text}")
        logger.info(f"Plan step {i+1} result: {result_text[:150]}")

        is_failure = result_text.startswith(("Failed:", "Error", "No response"))
        if is_failure and on_failure == "abort":
            results.append(f"Plan aborted at step {i+1}/{len(steps)}.")
            break

    # Append fresh observation summary after plan completes
    obs = await conn.drain_observations()
    if obs is None:
        obs = await conn.recv_type("observation", timeout=3.0)
    if obs:
        state.update(obs)
    results.append(f"\nCurrent state after plan:\n{state.summarize()}")

    return "\n\n".join(results)


async def execute_tool(
    tool_name: str,
    tool_input: dict,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
) -> str:
    """Execute a tool call and return the result as a string."""

    try:
        if tool_name == "look_around":
            obs = await conn.drain_observations()
            if obs is None:
                obs = await conn.recv_type("observation", timeout=5.0)
            if obs is None:
                # Retry once — observations may be delayed after teleport/cell change
                await asyncio.sleep(1.0)
                obs = await conn.recv_type("observation", timeout=5.0)
            if obs:
                state.update(obs)
            # Auto-save discovered doors to knowledge
            if knowledge and state.nearby:
                _auto_save_doors(state, knowledge)
            return state.summarize()

        elif tool_name == "search_area":
            # Systematic 360-degree scan: 4 turns of 90 degrees, look_around at each
            all_actors = {}
            all_doors = {}
            all_items = {}
            all_containers = {}

            for i in range(4):
                # Turn 90 degrees right
                if i > 0:
                    turn_cmd = action_builder._action("turn", {"angle": 1.5708, "duration": 0.5})
                    await conn.send(turn_cmd)
                    await conn.recv_by_id(turn_cmd["id"], timeout=3.0)
                    await asyncio.sleep(0.5)

                # Look around
                obs = await conn.drain_observations()
                if obs is None:
                    obs = await conn.recv_type("observation", timeout=3.0)
                if obs:
                    state.update(obs)
                    nb = obs.get("nearby", {})
                    for a in nb.get("actors", []):
                        all_actors[a.get("name", a.get("id", ""))] = a
                    for d in nb.get("doors", []):
                        key = d.get("destination", d.get("name", d.get("id", "")))
                        all_doors[key] = d
                    for item in nb.get("items", []):
                        all_items[item.get("name", item.get("id", ""))] = item
                    for c in nb.get("containers", []):
                        all_containers[c.get("name", c.get("id", ""))] = c

            # Auto-save doors
            if knowledge:
                _auto_save_doors(state, knowledge)

            # Format consolidated results
            lines = ["=== Area Scan Results ==="]
            if all_doors:
                lines.append(f"Doors ({len(all_doors)}):")
                for d in sorted(all_doors.values(), key=lambda x: x.get("distance", 9999)):
                    dest = d.get("destination", "")
                    name = d.get("name", "?")
                    dist = d.get("distance", "?")
                    direction = d.get("direction", "")
                    display = f"{dest}" if dest else name
                    lines.append(f"  {display} ({dist}m {direction})")
            if all_actors:
                lines.append(f"NPCs/Creatures ({len(all_actors)}):")
                for a in sorted(all_actors.values(), key=lambda x: x.get("distance", 9999)):
                    lines.append(f"  {a.get('name', '?')} ({a.get('distance', '?')}m)")
            if all_items:
                lines.append(f"Items ({len(all_items)}):")
                for item in sorted(all_items.values(), key=lambda x: x.get("distance", 9999))[:10]:
                    lines.append(f"  {item.get('name', '?')} ({item.get('distance', '?')}m)")
            if all_containers:
                lines.append(f"Containers ({len(all_containers)}):")
                for c in sorted(all_containers.values(), key=lambda x: x.get("distance", 9999))[:10]:
                    lines.append(f"  {c.get('name', '?')} ({c.get('distance', '?')}m)")
            return "\n".join(lines)

        elif tool_name == "navigate_to":
            destination = tool_input["destination"]
            params = {"destination": destination}
            cmd = action_builder._action("navigate_to", params)
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            initial = _format_result(result)
            if result and result.get("success"):
                progress_lines = [initial]
                while True:
                    msg = await conn.recv(timeout=60.0)
                    if msg is None:
                        progress_lines.append("Navigation timed out.")
                        break
                    if msg.get("type") == "action_complete":
                        progress_lines.append(_format_result(msg))
                        break
                    elif msg.get("type") == "navigation_progress":
                        wp = msg.get("waypointsRemaining", "?")
                        dist = msg.get("distanceToGoal", "?")
                        progress_lines.append(f"Progress: {wp} waypoints remaining, {dist}m to goal")
                    elif msg.get("type") == "observation":
                        state.update(msg)
                return "\n".join(progress_lines)
            return initial

        elif tool_name == "navigate_to_npc":
            cmd = action_builder._action("navigate_to_npc", {"npc": tool_input["npc"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            initial = _format_result(result)
            if result and result.get("success"):
                progress_lines = [initial]
                while True:
                    msg = await conn.recv(timeout=60.0)
                    if msg is None:
                        progress_lines.append("Navigation timed out.")
                        break
                    if msg.get("type") == "action_complete":
                        progress_lines.append(_format_result(msg))
                        break
                    elif msg.get("type") == "navigation_progress":
                        wp = msg.get("waypointsRemaining", "?")
                        dist = msg.get("distanceToGoal", "?")
                        progress_lines.append(f"Progress: {wp} waypoints remaining, {dist}m to goal")
                    elif msg.get("type") == "observation":
                        state.update(msg)
                return "\n".join(progress_lines)
            return initial

        elif tool_name == "approach":
            cmd = action_builder._action("approach", {"target": tool_input["target"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            initial = _format_result(result)
            if result and result.get("success"):
                # Wait for completion — could be navmesh navigation or timed walk
                while True:
                    msg = await conn.recv(timeout=30.0)
                    if msg is None:
                        return initial + "\nApproach timed out."
                    if msg.get("type") == "action_complete":
                        return _format_result(msg)
                    elif msg.get("type") == "navigation_progress":
                        pass
                    elif msg.get("type") == "observation":
                        state.update(msg)
            return initial

        elif tool_name == "open_and_enter":
            cmd = action_builder._action("open_and_enter", {"target": tool_input.get("target", "Door")})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                # Wait for walk-through to complete
                complete = await conn.recv_by_id(cmd["id"], msg_types=["action_complete"], timeout=5.0)
                if complete:
                    return _format_result(complete)
            return _format_result(result)

        elif tool_name == "activate":
            cmd = action_builder.activate(target=tool_input["target"])
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "stop":
            cmd = action_builder.stop()
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            return _format_result(result)

        elif tool_name == "talk_to":
            # Activate the NPC to open dialogue
            cmd = action_builder.activate(target=tool_input["npc"])
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if not result or not result.get("success"):
                return _format_result(result)

            # Wait for dialogue to open and get the greeting
            await asyncio.sleep(0.5)
            dialogue_msg = await conn.recv_type("dialogue", timeout=3.0)

            # Verify we're talking to the right NPC — not someone who got in the way
            if dialogue_msg:
                actual_npc = dialogue_msg.get("npc", "")
                requested_npc = tool_input["npc"].lower()
                if actual_npc and requested_npc not in actual_npc.lower():
                    # Wrong NPC — close dialogue and report
                    close_cmd = action_builder._action("close_dialogue", {})
                    await conn.send(close_cmd)
                    await conn.recv_by_id(close_cmd["id"], timeout=3.0)
                    return f"Failed: Talked to {actual_npc} instead of {tool_input['npc']}. Approach the correct NPC and try again."

            # Get available topics
            topics_cmd = action_builder._action("get_available_topics", {})
            await conn.send(topics_cmd)
            topics_result = await conn.recv_by_id(topics_cmd["id"], timeout=5.0)

            lines = []
            if dialogue_msg:
                lines.append(f"Talking to: {dialogue_msg.get('npc', tool_input['npc'])}")
                text = dialogue_msg.get('text', '')
                if text:
                    lines.append(f"Greeting: {text}")
            else:
                lines.append(f"Talking to: {tool_input['npc']}")

            if topics_result and topics_result.get("success"):
                topics = topics_result.get("topics", [])
                if topics:
                    lines.append(f"Available topics: {', '.join(topics)}")
                else:
                    msg = topics_result.get("message", "")
                    if msg:
                        lines.append(f"Available topics: {msg}")

            # Also check for choices (yes/no questions)
            choices_cmd = action_builder._action("get_choices", {})
            await conn.send(choices_cmd)
            choices_result = await conn.recv_by_id(choices_cmd["id"], timeout=3.0)
            if choices_result and choices_result.get("success"):
                choices = choices_result.get("choices", [])
                if choices:
                    choice_strs = [f"{c.get('text', '?')} (id:{c.get('id', '?')})" for c in choices]
                    lines.append(f"CHOICES: {', '.join(choice_strs)}")
                    lines.append("Use answer_choice with the choice id to respond.")

            lines.append("\nUse select_topic for topics, answer_choice for yes/no questions, then close_dialogue when done.")
            return "\n".join(lines)

        elif tool_name == "select_topic":
            cmd = action_builder._action("select_topic", {"topic": tool_input["topic"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                text = result.get("message", "")
                title = result.get("title", tool_input["topic"])
                response = f"[{title}]: {text}"
                # Check if this triggered a choice
                choices_cmd = action_builder._action("get_choices", {})
                await conn.send(choices_cmd)
                choices_result = await conn.recv_by_id(choices_cmd["id"], timeout=3.0)
                if choices_result and choices_result.get("success"):
                    choices = choices_result.get("choices", [])
                    if choices:
                        choice_strs = [f"{c.get('text', '?')} (id:{c.get('id', '?')})" for c in choices]
                        response += f"\n\nCHOICES: {', '.join(choice_strs)}\nUse answer_choice with the choice id."
                return response
            return _format_result(result)

        elif tool_name == "save_game":
            cmd = action_builder._action("save_game", {"description": tool_input.get("description", "Agent save")})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "load_game":
            cmd = action_builder._action("load_game", {"description": tool_input.get("description", "")})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            # Wait for the game to reload — Lua scripts reset and send game_loaded
            await asyncio.sleep(3.0)
            conn.flush_queue()  # discard stale messages from before the load
            # Wait for the game_loaded event and fresh observation from the new state
            loaded = await conn.recv_type("game_loaded", timeout=10.0)
            if loaded:
                logger.info("Game loaded successfully, Lua scripts re-initialized")
            obs = await conn.recv_type("observation", timeout=5.0)
            if obs:
                state.update(obs)
            return _format_result(result) + "\n\n" + state.summarize()

        elif tool_name == "list_saves":
            cmd = action_builder._action("list_saves", {})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "use_item":
            cmd = action_builder._action("use_item", {"item": tool_input["item"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "attack":
            duration = tool_input.get("duration", 15.0)  # sustained attack duration
            params = {"duration": duration}
            if "target" in tool_input:
                params["target"] = tool_input["target"]
            if tool_input.get("melee_only"):
                params["melee_only"] = True
            await conn.drain_observations()
            cmd = action_builder._action("attack", params)
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            if result and result.get("success"):
                # Wait for sustained attack to end (fatigue low, target dead, or max duration)
                while True:
                    msg = await conn.recv(timeout=duration + 5.0)
                    if msg is None:
                        break
                    if msg.get("type") == "player_died":
                        return "PLAYER DIED during combat. Game is auto-reloading last save."
                    if msg.get("type") == "action_complete" and msg.get("id") == cmd["id"]:
                        obs = await conn.drain_observations()
                        if obs:
                            state.update(obs)
                        return _format_result(msg) + "\n" + _combat_status(state)
                    elif msg.get("type") == "observation":
                        state.update(msg)
                obs = await conn.drain_observations()
                if obs:
                    state.update(obs)
            return (_format_result(result) if result else "Attack sent") + "\n" + _combat_status(state)

        elif tool_name == "cast_spell":
            await conn.drain_observations()
            params = {"spell": tool_input.get("spell", "")}
            if "target" in tool_input:
                params["target"] = tool_input["target"]
            cmd = action_builder._action("cast", params)
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            if result and result.get("success"):
                await asyncio.sleep(1.0)
                obs = await conn.drain_observations()
                if obs:
                    state.update(obs)
            return (_format_result(result) if result else "Spell cast sent") + "\n" + _combat_status(state)

        elif tool_name == "equip_item":
            cmd = action_builder.equip(item=tool_input["item"])
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "move":
            cmd = action_builder.move(
                direction=tool_input.get("direction", "forward"),
                duration=tool_input.get("duration", 1.0),
                run=tool_input.get("run", True),
            )
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            return _format_result(result)

        elif tool_name == "jump":
            cmd = action_builder.jump()
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            return _format_result(result)

        elif tool_name == "answer_choice":
            cmd = action_builder._action("answer_choice", {"choice_id": int(tool_input["choice_id"])})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                text = result.get("message", "")
                title = result.get("title", "")
                response = f"[{title}]: {text}" if title else text
                # Check if answering triggered more choices
                choices_cmd = action_builder._action("get_choices", {})
                await conn.send(choices_cmd)
                choices_result = await conn.recv_by_id(choices_cmd["id"], timeout=3.0)
                if choices_result and choices_result.get("success"):
                    choices = choices_result.get("choices", [])
                    if choices:
                        choice_strs = [f"{c.get('text', '?')} (id:{c.get('id', '?')})" for c in choices]
                        response += f"\n\nNEW CHOICES: {', '.join(choice_strs)}\nUse answer_choice again with the choice id."
                # Also check for new topics
                topics_cmd = action_builder._action("get_available_topics", {})
                await conn.send(topics_cmd)
                topics_result = await conn.recv_by_id(topics_cmd["id"], timeout=3.0)
                if topics_result and topics_result.get("success") and topics_result.get("message"):
                    response += f"\n\nAvailable topics: {topics_result['message']}"
                return response
            return _format_result(result)

        elif tool_name == "close_dialogue":
            cmd = action_builder._action("close_dialogue", {})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "get_travel_destinations":
            cmd = action_builder._action("get_travel_destinations", {"npc": tool_input["npc"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "travel":
            cmd = action_builder._action("travel", {
                "npc": tool_input["npc"],
                "destination": tool_input["destination"],
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            if result and result.get("success"):
                # Wait for the game to settle after teleportation
                await asyncio.sleep(2.0)
                # Get a fresh observation in the new location
                obs = await conn.drain_observations()
                if obs is None:
                    obs = await conn.recv_type("observation", timeout=5.0)
                if obs:
                    state.update(obs)
                return _format_result(result) + "\n\n" + state.summarize()
            return _format_result(result)

        elif tool_name == "lookup":
            if not knowledge:
                return "Knowledge base not available."
            results = knowledge.search(tool_input["query"], max_results=5)
            if not results:
                return f"No results for '{tool_input['query']}'."
            lines = []
            for r in results:
                lines.append(f"[{r['category']}] {r['key']}: {r['value']}")
            return "\n\n".join(lines)

        elif tool_name == "get_contents":
            cmd = action_builder._action("get_contents", {"target": tool_input["target"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                items = result.get("items", [])
                if not items:
                    return "Empty."
                lines = []
                for item in items:
                    name = item.get("name", "?")
                    count = item.get("count", 1)
                    value = item.get("value", 0)
                    count_str = f" x{count}" if count > 1 else ""
                    lines.append(f"  {name}{count_str} (value: {value})")
                return "\n".join(lines)
            return _format_result(result)

        elif tool_name == "take_item":
            cmd = action_builder._action("take_item", {
                "target": tool_input["target"],
                "item": tool_input["item"],
                "count": tool_input.get("count", 1),
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "drop_item":
            cmd = action_builder._action("drop_item", {
                "item": tool_input["item"],
                "count": tool_input.get("count", 1),
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "take_all":
            cmd = action_builder._action("take_all", {"target": tool_input["target"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "check_journal":
            quest = tool_input.get("quest", "")
            cmd = action_builder._action("check_journal", {"quest": quest})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                entries = result.get("entries", [])
                if not entries:
                    return f"No journal entries found{' for quest ' + quest if quest else ''}."
                lines = []
                for e in entries:
                    qid = e.get("questId", "")
                    text = e.get("text", "")
                    day = e.get("day", "?")
                    month = e.get("month", "?")
                    prefix = f"[{qid}] " if qid else ""
                    lines.append(f"{prefix}(day {day}, month {month}) {text}")
                return "\n\n".join(lines)
            return _format_result(result)

        elif tool_name == "get_merchant_inventory":
            cmd = action_builder._action("get_merchant_inventory", {"npc": tool_input["npc"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                items = result.get("items", [])
                gold = result.get("merchantGold", 0)
                lines = [f"Merchant gold: {gold}"]
                for item in items:
                    name = item.get("name", item.get("recordId", "?"))
                    count = item.get("count", 1)
                    value = item.get("value", 0)
                    weight = item.get("weight", 0)
                    count_str = f" x{count}" if count > 1 else ""
                    lines.append(f"  {name}{count_str} (value: {value}, weight: {weight:.1f})")
                return "\n".join(lines)
            return _format_result(result)

        elif tool_name == "buy_item":
            cmd = action_builder._action("buy_item", {
                "npc": tool_input["npc"],
                "item": tool_input["item"],
                "count": tool_input.get("count", 1),
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "sell_item":
            cmd = action_builder._action("sell_item", {
                "npc": tool_input["npc"],
                "item": tool_input["item"],
                "count": tool_input.get("count", 1),
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "rest":
            hours = tool_input.get("hours", 1)
            cmd = action_builder._action("rest", {"hours": hours})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            if result and result.get("success"):
                # Get fresh observation after resting
                await asyncio.sleep(1.0)
                obs = await conn.drain_observations()
                if obs:
                    state.update(obs)
                return _format_result(result) + "\n\n" + state.summarize()
            return _format_result(result)

        elif tool_name == "persuade":
            action_str = tool_input.get("action", "admire")
            cmd = action_builder._action("persuade", {"action": action_str})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                text = result.get("text", "")
                action_name = result.get("actionName", action_str)
                response = _format_result(result)
                if text:
                    response += f"\nNPC response: {text}"
                # Check for updated topics after persuasion
                topics_cmd = action_builder._action("get_available_topics", {})
                await conn.send(topics_cmd)
                topics_result = await conn.recv_by_id(topics_cmd["id"], timeout=3.0)
                if topics_result and topics_result.get("success") and topics_result.get("message"):
                    response += f"\n\nAvailable topics: {topics_result['message']}"
                return response
            return _format_result(result)

        elif tool_name == "pick_lock":
            cmd = action_builder._action("pick_lock", {"target": tool_input["target"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "disarm_trap":
            cmd = action_builder._action("disarm_trap", {"target": tool_input["target"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "execute_plan":
            steps = tool_input.get("steps", [])
            if not steps:
                return "No steps provided."
            if len(steps) > 15:
                return "Too many steps (max 15). Break into smaller plans."
            return await _execute_plan(steps, conn, state, knowledge)

        elif tool_name == "get_training_services":
            cmd = action_builder._action("get_training_services", {
                "npc": tool_input["npc"],
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "train_skill":
            npc = tool_input["npc"]
            skill = tool_input["skill"]
            count = tool_input.get("count", 1)
            count = max(1, min(count, 10))  # cap at 10 repetitions
            results = []
            for i in range(count):
                cmd = action_builder._action("train_skill", {
                    "npc": npc,
                    "skill": skill,
                })
                await conn.send(cmd)
                result = await conn.recv_by_id(cmd["id"], timeout=5.0)
                result_text = _format_result(result)
                results.append(result_text)
                if result and not result.get("success"):
                    break  # stop on failure
                if i < count - 1:
                    await asyncio.sleep(0.5)
            return "\n".join(results)

        elif tool_name == "sneak":
            enable = tool_input.get("enable", True)
            cmd = action_builder.sneak(enable)
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "brew_potion":
            cmd = action_builder._action("brew_potion", {
                "name": tool_input["name"],
                "ingredients": tool_input["ingredients"],
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "preview_potion":
            cmd = action_builder._action("preview_potion", {
                "ingredients": tool_input["ingredients"],
            })
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                effects = result.get("effects", [])
                if effects:
                    effect_names = [e.get("name", "?") for e in effects]
                    return f"Effects: {', '.join(effect_names)}"
                return result.get("message", "No shared effects")
            return _format_result(result)

        elif tool_name == "list_ingredients":
            # Filter inventory for ingredients and show their known effects
            obs = await conn.drain_observations()
            if obs is None:
                obs = await conn.recv_type("observation", timeout=5.0)
            if obs:
                state.update(obs)
            inventory = state.current.get("inventory", []) if state.current else []
            ingredients = []
            has_mortar = False
            apparatus_list = []
            for item in inventory:
                if item.get("itemType") == "ingredient":
                    name = item.get("name", item.get("recordId", "?"))
                    record_id = item.get("recordId", "?")
                    count = item.get("count", 1)
                    effects = item.get("effects", [])
                    effect_strs = []
                    for eff in effects:
                        effect_strs.append(eff.get("name", "???"))
                    count_str = f" x{count}" if count > 1 else ""
                    effects_str = ", ".join(effect_strs) if effect_strs else "no known effects"
                    ingredients.append(f"  {name}{count_str} [{record_id}] -- effects: {effects_str}")
                elif item.get("itemType") == "apparatus":
                    atype = item.get("apparatusType", "?")
                    name = item.get("name", "?")
                    quality = item.get("quality", 0)
                    apparatus_list.append(f"  {name} ({atype}, quality: {quality})")
                    if atype == "Mortar & Pestle":
                        has_mortar = True
            lines = []
            if apparatus_list:
                lines.append("Apparatus:")
                lines.extend(apparatus_list)
            else:
                lines.append("No apparatus in inventory (need Mortar & Pestle to brew)")
            if not has_mortar and apparatus_list:
                lines.append("WARNING: No Mortar & Pestle -- cannot brew potions")
            lines.append(f"\nIngredients ({len(ingredients)}):")
            if ingredients:
                lines.extend(ingredients)
            else:
                lines.append("  (none)")
            return "\n".join(lines)

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        logger.error(f"Tool execution error: {tool_name}: {e}")
        return f"Error executing {tool_name}: {e}"


def _combat_status(state: GameState) -> str:
    """Quick combat status — player health + nearby hostile actors."""
    lines = []
    p = state.player
    if p:
        for stat in ("health", "magicka", "fatigue"):
            s = p.get(stat, {})
            lines.append(f"{stat.capitalize()}: {s.get('current', 0):.0f}/{s.get('base', 0):.0f}")
    nb = state.nearby
    if nb:
        for a in nb.get("actors", []):
            if a.get("hostile") and not a.get("dead"):
                hp = a.get("health", {})
                lines.append(f"  HOSTILE: {a.get('name', '?')} HP:{hp.get('current', 0):.0f}/{hp.get('base', 0):.0f} ({a.get('distance', '?')} units)")
            elif a.get("dead"):
                lines.append(f"  DEAD: {a.get('name', '?')}")
    # Recent hits taken
    if state.current:
        hits = state.current.get("recentHits", [])
        if hits:
            lines.append("Recent hits taken:")
            for h in hits:
                attacker = h.get("attackerName", "unknown")
                dmg = h.get("damage", {})
                dmg_str = ", ".join(f"{k}:{v:.0f}" for k, v in dmg.items() if isinstance(v, (int, float)))
                hit_miss = "HIT" if h.get("successful") else "MISS"
                source = h.get("sourceType", "")
                weapon = h.get("weaponName", "")
                if weapon:
                    via = f" with {weapon}"
                elif source == "magic":
                    magic_effs = h.get("magicEffects", [])
                    if magic_effs:
                        via = f" ({', '.join(magic_effs)})"
                    else:
                        via = " (spell)"
                elif source == "ranged":
                    via = " (ranged)"
                else:
                    via = ""
                lines.append(f"  {attacker}{via} — {hit_miss} ({dmg_str})")
    return "\n".join(lines)


def _format_result(result: Optional[dict]) -> str:
    """Format an action result dict into a readable string."""
    if result is None:
        return "No response from game (timeout)."
    success = result.get("success", False)
    message = result.get("message", "")
    rtype = result.get("type", "")
    if rtype == "action_complete":
        return f"Action completed. {message}".strip()
    elif success:
        return f"OK: {message}" if message else "OK"
    else:
        return f"Failed: {message}" if message else "Failed"


async def wait_for_observation(conn: BridgeConnection, state: GameState, timeout: float = 5.0):
    """Wait for and process a fresh observation."""
    # First drain any queued observations
    obs = await conn.drain_observations()
    if obs is None:
        obs = await conn.recv_type("observation", timeout=timeout)
    if obs:
        state.update(obs)
    return obs


def _write_resume_file(goal_file: str, current_goal: str, goal_queue, state, cost_tracker):
    """Write a resume file with current context so the agent can pick up where it left off."""
    import datetime
    resume_path = goal_file.replace(".txt", "_resume.txt")
    with open(resume_path, "w", encoding="utf-8") as f:
        f.write(f"# Resume file — generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# Budget spent: ${cost_tracker.total_cost:.2f} | Calls: {cost_tracker.call_count}\n")
        if state and state.player:
            p = state.player
            cell = p.get("cell", "unknown")
            level = p.get("level", "?")
            hp = p.get("health", {})
            f.write(f"# Player: Level {level} in {cell} | HP: {hp.get('current', '?')}/{hp.get('base', '?')}\n")
        f.write(f"#\n")
        f.write(f"# Current goal (in progress when budget ran out):\n")
        f.write(f"1. {current_goal}\n")
        if goal_queue:
            f.write(f"#\n# Remaining goals:\n")
            for i, g in enumerate(goal_queue, 2):
                f.write(f"{i}. {g}\n")
    logger.info(f"Resume file written: {resume_path}")
    print(f"Resume file: {resume_path}")


async def run_agent(
    conn: BridgeConnection,
    state: GameState,
    model: str = "claude-sonnet-4-20250514",
    goal: Optional[str] = None,
    knowledge: Optional[KnowledgeBase] = None,
    time_limit_minutes: int = 0,
    system_prompt_override: Optional[str] = None,
    adventure_context: Optional[str] = None,
    action_log=None,
    director_queue: Optional[asyncio.Queue] = None,
    pace: float = 3.0,
    goal_queue: Optional[list] = None,
    cost_tracker=None,
    goal_file: Optional[str] = None,
):
    """Main agent loop. Connects Claude to OpenMW via tool use."""
    import time as _time

    client = AsyncAnthropic()  # Uses ANTHROPIC_API_KEY env var
    tracker = SessionTracker()
    tracker.set_model(model)
    glog = GameplayLog()

    base_system_text = system_prompt_override or SYSTEM_PROMPT
    current_goal = goal

    if knowledge:
        prior = knowledge.get_summary()
        if prior and prior != "No prior knowledge saved.":
            base_system_text += f"\n\n{prior}"

    # Build system prompt and tools with cache_control for prompt caching.
    # The system prompt and tool definitions are identical every turn,
    # so caching them avoids re-processing ~4k tokens on each API call.
    def _build_cached_system(goal_text):
        text = base_system_text
        if goal_text:
            text += f"\n\nYour current goal: {goal_text}"
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    cached_system = _build_cached_system(current_goal)
    cached_tools = []
    for i, tool in enumerate(TOOLS):
        t = dict(tool)
        if i == len(TOOLS) - 1:
            t["cache_control"] = {"type": "ephemeral"}
        cached_tools.append(t)

    messages = []
    start_time = _time.monotonic()

    logger.info(f"Starting agent loop with model={model}")
    if goal:
        logger.info(f"Goal: {goal}")
    if time_limit_minutes > 0:
        logger.info(f"Time limit: {time_limit_minutes} minutes")

    last_goal_save = ""  # save description from last completed goal

    # Get initial observation
    await wait_for_observation(conn, state, timeout=10.0)

    # Generate an initial goal if none was provided
    if not current_goal:
        logger.info("No goal provided, generating initial goal...")
        current_goal = await generate_goal(state, knowledge)
        cached_system = _build_cached_system(current_goal)
        print(f"Goal: {current_goal}")

    tracker.start_goal(current_goal)
    glog.goal_start(current_goal)
    tracker.record_snapshot(state)

    initial_context = state.summarize()
    messages.append({"role": "user", "content": f"You have just loaded into the game. Here is what you see:\n\n{initial_context}\n\nWhat would you like to do?"})

    SHUTDOWN_FILE = os.path.join(os.path.dirname(__file__), ".shutdown")

    while True:
        # Check for external shutdown signal (from improvement agent or other tools)
        if os.path.exists(SHUTDOWN_FILE):
            reason = ""
            try:
                with open(SHUTDOWN_FILE, "r") as f:
                    reason = f.read().strip()
                os.remove(SHUTDOWN_FILE)
            except Exception:
                pass
            print(f"\nShutdown requested: {reason or 'external signal'}")
            logger.info(f"Shutdown requested: {reason}")
            # Save game
            save_desc = f"Shutdown: {(current_goal or 'unknown')[:50]}"
            save_cmd = action_builder._action("save_game", {"description": save_desc})
            await conn.send(save_cmd)
            await conn.recv_by_id(save_cmd["id"], timeout=5.0)
            # Write resume file
            if goal_file:
                _write_resume_file(goal_file, current_goal, goal_queue, state, cost_tracker or type('', (), {"total_cost": 0, "call_count": 0})())
            if cost_tracker:
                print(f"\n{cost_tracker.summary()}")
            tracker.save()
            break

        # Check time limit
        if time_limit_minutes > 0:
            elapsed = (_time.monotonic() - start_time) / 60.0
            if elapsed >= time_limit_minutes:
                print(f"\nTime's up! Session lasted {elapsed:.1f} minutes.")
                tracker.save()
                break

        try:
            tracker.record_api_call()
            response = await client.messages.create(
                model=model,
                max_tokens=2048,
                system=cached_system,
                tools=cached_tools,
                messages=messages,
            )

            # Track API cost
            if cost_tracker:
                call_cost = cost_tracker.record(response, model)
                usage = getattr(response, 'usage', None)
                if usage:
                    glog.api_call(model, getattr(usage, 'input_tokens', 0),
                        getattr(usage, 'output_tokens', 0), call_cost)
                warning = cost_tracker.check_and_warn()
                if warning:
                    print(f"\n{warning}")
                    logger.warning(warning)
                if cost_tracker.should_shutdown:
                    print(f"\nBudget limit reached. Saving game and writing context...")
                    # Save game
                    save_cmd = action_builder._action("save_game", {"description": f"Budget limit: {current_goal[:50]}"})
                    await conn.send(save_cmd)
                    await conn.recv_by_id(save_cmd["id"], timeout=5.0)
                    # Write remaining goals and context to goal file
                    if goal_file:
                        _write_resume_file(goal_file, current_goal, goal_queue, state, cost_tracker)
                    print(f"\n{cost_tracker.summary()}")
                    tracker.save()
                    return

            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if hasattr(block, "text"):
                    logger.info(f"Claude: {block.text}")
                    print(f"\nClaude: {block.text}")

            if response.stop_reason == "tool_use":
                tool_results = []
                used_plan = False
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"Tool: {block.name}({block.input})")
                        if block.name == "execute_plan":
                            used_plan = True
                        elif block.name == "goal_complete":
                            summary = block.input.get("summary", "")
                            goal_success = block.input.get("success", True)

                            if goal_success:
                                print(f"\nGoal complete: {summary}")
                                tracker.end_goal("completed", summary)
                                glog.goal_complete(current_goal, True, summary)
                                tracker.record_snapshot(state)
                                # Auto-save on goal completion
                                save_desc = f"Goal done: {(current_goal or 'unknown')[:60]}"
                                save_cmd = action_builder._action("save_game", {"description": save_desc})
                                await conn.send(save_cmd)
                                await conn.recv_by_id(save_cmd["id"], timeout=5.0)
                                last_goal_save = save_desc
                                logger.info(f"Auto-saved: {save_desc}")
                                await wait_for_observation(conn, state, timeout=5.0)
                                # Use next goal from queue if available
                                if goal_queue:
                                    new_goal = goal_queue.pop(0)
                                    remaining = len(goal_queue)
                                    logger.info(f"Next goal from queue ({remaining} remaining): {new_goal}")
                                elif goal_queue is not None:
                                    print(f"\nAll goals completed!")
                                    tracker.save()
                                    return
                                else:
                                    new_goal = await generate_goal(
                                        state, knowledge,
                                        completed_goal=current_goal,
                                        completed_summary=summary,
                                    )
                            else:
                                # Goal failed — suggest prerequisite, push failed goal back
                                print(f"\nGoal failed: {summary}")
                                tracker.end_goal("failed", summary)
                                glog.goal_complete(current_goal, False, summary)
                                await wait_for_observation(conn, state, timeout=5.0)
                                if goal_queue is not None:
                                    # Push failed goal back and get a prerequisite
                                    goal_queue.insert(0, current_goal)
                                    replacement = await suggest_replacement_goal(
                                        failed_goal=current_goal,
                                        death_summary=f"Goal failed: {summary}",
                                        remaining_goals=list(goal_queue),
                                        state=state,
                                        knowledge=knowledge,
                                    )
                                    new_goal = replacement
                                    print(f"Prerequisite goal: {new_goal}")
                                    print(f"(Failed goal will be retried after)")
                                else:
                                    new_goal = await generate_goal(
                                        state, knowledge,
                                        completed_goal=current_goal,
                                        completed_summary=f"FAILED: {summary}. Need a different approach.",
                                    )
                            current_goal = new_goal
                            cached_system = _build_cached_system(current_goal)
                            tracker.start_goal(current_goal)
                            glog.goal_start(current_goal)
                            print(f"New goal: {current_goal}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"Goal completed. Your new goal: {current_goal}",
                            })
                            continue
                        t0 = _time.monotonic()
                        result_text = await execute_tool(block.name, block.input, conn, state, knowledge)
                        duration = _time.monotonic() - t0
                        is_success = not result_text.startswith(("Failed:", "Error", "No response"))
                        tracker.record_action(block.name, block.input, result_text, is_success, duration)
                        glog.tool_call(block.name, block.input, result_text, is_success, duration)
                        if block.name == "load_game":
                            tracker.record_load()
                        logger.info(f"Result: {result_text[:200]}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                messages.append({"role": "user", "content": tool_results})
                # Skip pace delay after plan execution — the plan itself took time
                if not used_plan:
                    await asyncio.sleep(pace)

            elif response.stop_reason == "end_turn":
                await asyncio.sleep(pace)
                await wait_for_observation(conn, state, timeout=5.0)
                tracker.record_snapshot(state)
                glog.snapshot(state)
                # Send a compact diff when possible, full state periodically
                changes = state.summarize_changes()
                if changes:
                    messages.append({"role": "user", "content": f"{changes}\n\nWhat next?"})
                else:
                    messages.append({"role": "user", "content": f"{state.summarize()}\n\nWhat next?"})

            # Check for player death
            player = state.player
            if player:
                hp = player.get("health", {})
                if hp.get("current", 1) <= 0:
                    # Gather death context before loading
                    cell = player.get("cell", "unknown")
                    level = player.get("level", "?")
                    killers = []
                    nb = state.nearby or {}
                    for a in nb.get("actors", []):
                        if a.get("hostile") and not a.get("dead"):
                            a_hp = a.get("health", {})
                            killers.append(f"{a.get('name', '?')} (HP:{a_hp.get('current', '?')}/{a_hp.get('base', '?')})")
                    recent_hits = []
                    if state.current:
                        for h in state.current.get("recentHits", []):
                            attacker = h.get("attackerName", "unknown")
                            weapon = h.get("weaponName", "")
                            source = h.get("sourceType", "")
                            via = f" with {weapon}" if weapon else f" ({source})" if source else ""
                            recent_hits.append(f"{attacker}{via}")

                    killed_by = ", ".join(recent_hits) if recent_hits else ", ".join(killers) if killers else "unknown"
                    death_summary = f"Killed by {killed_by} in {cell} at level {level}. Goal: {current_goal}"
                    print(f"\nPlayer died! {death_summary}")

                    # Save death to persistent knowledge so future sessions can avoid/retry
                    if knowledge:
                        import datetime
                        death_key = f"death_{cell}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}"
                        death_note = json.dumps({
                            "cell": cell,
                            "level": level,
                            "killed_by": killed_by,
                            "goal": current_goal,
                            "enemies": killers,
                            "note": f"Died here at level {level}. Come back when stronger.",
                        })
                        knowledge.save("strategies", death_key, death_note)

                    tracker.record_death()
                    glog.death(cell, killed_by, current_goal)
                    tracker.end_goal("failed", death_summary)
                    # Load save from last completed goal (not most recent — that's mid-failed-goal)
                    load_desc = last_goal_save or ""
                    logger.info(f"Loading save from last completed goal: '{load_desc}'")
                    print(f"Loading save: {load_desc or 'most recent'}")
                    load_cmd = action_builder._action("load_game", {"description": load_desc})
                    await conn.send(load_cmd)
                    await conn.recv_by_id(load_cmd["id"], timeout=5.0)
                    await asyncio.sleep(3.0)
                    conn.flush_queue()
                    await conn.recv_type("game_loaded", timeout=10.0)
                    await wait_for_observation(conn, state, timeout=5.0)
                    # Suggest a replacement goal using high-reasoning model
                    if goal_queue is not None:
                        remaining = [current_goal] + list(goal_queue)
                        replacement = await suggest_replacement_goal(
                            failed_goal=current_goal,
                            death_summary=death_summary,
                            remaining_goals=remaining,
                            state=state,
                            knowledge=knowledge,
                        )
                        # Insert replacement at the front of the queue, push failed goal back
                        goal_queue.insert(0, current_goal)  # retry failed goal later
                        new_goal = replacement
                        print(f"Replacement goal: {new_goal}")
                        print(f"(Failed goal '{current_goal[:50]}...' moved to next in queue)")
                    else:
                        new_goal = await generate_goal(
                            state, knowledge,
                            completed_goal=current_goal,
                            completed_summary=f"DIED: {death_summary}. Need an easier or different approach.",
                        )
                    current_goal = new_goal
                    cached_system = _build_cached_system(current_goal)
                    tracker.start_goal(current_goal)
                    glog.goal_start(current_goal)
                    print(f"New goal: {current_goal}")
                    # Reset conversation — old context is stale after load
                    messages = [{"role": "user", "content": f"You just died and reloaded a save.\n\nDeath: {death_summary}\n\nHere is your current state:\n\n{state.summarize()}\n\nYour new goal: {current_goal}\n\nWhat would you like to do?"}]
                    continue

            # Trim conversation to avoid context overflow
            if len(messages) > 40:
                messages = messages[-20:]

        except KeyboardInterrupt:
            tracker.save()
            raise
        except TypeError as e:
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                logger.error(f"Authentication failed: {e}")
                print(f"\nFatal: ANTHROPIC_API_KEY not set. Export it and retry.")
                tracker.save()
                raise
            raise
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str or "overloaded" in error_str:
                logger.warning("Rate limited — waiting 30s...")
                await asyncio.sleep(30.0)
                continue
            logger.error(f"Agent loop error: {e}", exc_info=True)
            await asyncio.sleep(5.0)
            await wait_for_observation(conn, state, timeout=5.0)
            messages.append({"role": "user", "content": f"Error: {e}\n\n{state.summarize()}\n\nWhat next?"})

    # Save session log on exit (time limit or KeyboardInterrupt propagated from caller)
    tracker.save()
    glog.shutdown("session_end", cost_tracker.total_cost if cost_tracker else 0)
    glog.close()
