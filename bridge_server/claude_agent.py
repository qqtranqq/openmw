"""Claude agent loop for controlling OpenMW."""

import asyncio
import logging
import os
from typing import Optional

from anthropic import AsyncAnthropic

from connection import BridgeConnection
from game_state import GameState
import action_builder

logger = logging.getLogger(__name__)

# Tool definitions for Claude
TOOLS = [
    {
        "name": "look_around",
        "description": "Get a detailed observation of your surroundings including nearby NPCs, creatures, doors, items, containers, and activators with their distances. Also shows your current status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "move",
        "description": "Move in a direction for a specified duration.",
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
        "name": "turn",
        "description": "Turn the player character left or right.",
        "input_schema": {
            "type": "object",
            "properties": {
                "angle": {
                    "type": "number",
                    "description": "Radians to turn. Positive = right, negative = left. ~1.57 = 90 degrees.",
                },
                "duration": {
                    "type": "number",
                    "description": "Seconds over which to turn (default 0.5)",
                },
            },
            "required": ["angle"],
        },
    },
    {
        "name": "activate",
        "description": "Interact with a nearby object. Opens doors, talks to NPCs, picks up items, opens containers. Uses name matching.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Name of the object/NPC/door to interact with. Must match a name from your observations.",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "check_status",
        "description": "Check your current health, magicka, fatigue, level, equipment, and active effects.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_inventory",
        "description": "List all items in your inventory with counts.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_quests",
        "description": "Show your quest journal with active and completed quests.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "equip_item",
        "description": "Equip a weapon, armor, or clothing item from your inventory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": "Name of the item to equip",
                },
            },
            "required": ["item"],
        },
    },
    {
        "name": "attack",
        "description": "Attack with your currently equipped weapon. Make sure you have a weapon equipped first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "Seconds to hold the attack (default 1.0). Longer = more powerful swing.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "cast_spell",
        "description": "Cast a spell. Optionally specify which spell by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spell": {
                    "type": "string",
                    "description": "Spell record ID (optional, uses currently selected spell if omitted)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "jump",
        "description": "Jump.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "sneak",
        "description": "Toggle sneaking on or off.",
        "input_schema": {
            "type": "object",
            "properties": {
                "enable": {
                    "type": "boolean",
                    "description": "True to start sneaking, false to stop",
                },
            },
            "required": [],
        },
    },
    {
        "name": "wait_here",
        "description": "Wait in place for a duration. Useful to let time pass or wait for something.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {
                    "type": "number",
                    "description": "Seconds to wait (default 1.0)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "stop",
        "description": "Immediately stop any current action and stand still.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

SYSTEM_PROMPT = """You are playing The Elder Scrolls III: Morrowind through OpenMW. You observe the game world and take actions using the provided tools.

Your capabilities:
- Move around the world (forward/backward/left/right, turn, jump)
- Interact with objects, NPCs, and doors (activate)
- Manage inventory and equipment (check, equip)
- Engage in combat (attack, cast spells)
- Track quests and explore

Guidelines:
- Use look_around frequently to stay aware of your surroundings
- Use exact names from observations when activating objects
- Check your health regularly, especially in dangerous areas
- Explore systematically: enter buildings, talk to NPCs, pick up useful items
- Think step by step about your goals and next actions
"""


async def execute_tool(
    tool_name: str,
    tool_input: dict,
    conn: BridgeConnection,
    state: GameState,
) -> str:
    """Execute a tool call and return the result as a string."""

    try:
        if tool_name == "look_around":
            # Wait for a fresh observation
            obs = await conn.drain_observations()
            if obs is None:
                obs = await conn.recv_type("observation", timeout=3.0)
            if obs:
                state.update(obs)
            return state.summarize()

        elif tool_name == "check_status":
            p = state.player
            if not p:
                return "No player data available. Try look_around first."
            lines = []
            lines.append(f"Cell: {p.get('cell', '?')}")
            lines.append(f"Level: {p.get('level', '?')}")
            for stat in ("health", "magicka", "fatigue"):
                s = p.get(stat, {})
                lines.append(f"{stat.capitalize()}: {s.get('current', 0):.0f}/{s.get('base', 0):.0f}")
            eqp = state.equipment or {}
            if eqp:
                lines.append("Equipment:")
                for slot, item in eqp.items():
                    lines.append(f"  {item.get('name', item.get('recordId', '?'))}")
            return "\n".join(lines)

        elif tool_name == "check_inventory":
            inv = state.inventory
            if not inv:
                return "Inventory is empty or not yet loaded."
            lines = []
            for item in inv:
                name = item.get("name", item.get("recordId", "?"))
                count = item.get("count", 1)
                count_str = f" x{count}" if count > 1 else ""
                lines.append(f"  {name}{count_str}")
            return f"Inventory ({len(inv)} items):\n" + "\n".join(lines)

        elif tool_name == "check_quests":
            quests = state.quests
            if not quests:
                return "No quests in journal."
            lines = []
            active = [q for q in quests if not q.get("finished")]
            finished = [q for q in quests if q.get("finished")]
            if active:
                lines.append(f"Active ({len(active)}):")
                for q in active:
                    lines.append(f"  {q.get('id', '?')} - stage {q.get('stage', '?')}")
            if finished:
                lines.append(f"Completed ({len(finished)}):")
                for q in finished:
                    lines.append(f"  {q.get('id', '?')}")
            return "\n".join(lines)

        elif tool_name == "move":
            cmd = action_builder.move(
                direction=tool_input.get("direction", "forward"),
                duration=tool_input.get("duration", 1.0),
                run=tool_input.get("run", True),
            )
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            return _format_result(result)

        elif tool_name == "turn":
            cmd = action_builder.turn(
                angle=tool_input.get("angle", 0.5),
                duration=tool_input.get("duration", 0.5),
            )
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "activate":
            cmd = action_builder.activate(target=tool_input["target"])
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "equip_item":
            cmd = action_builder.equip(item=tool_input["item"])
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "attack":
            cmd = action_builder.attack(duration=tool_input.get("duration", 1.0))
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            return _format_result(result)

        elif tool_name == "cast_spell":
            cmd = action_builder.cast(spell=tool_input.get("spell"))
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            return _format_result(result)

        elif tool_name == "jump":
            cmd = action_builder.jump()
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            return _format_result(result)

        elif tool_name == "sneak":
            cmd = action_builder.sneak(enable=tool_input.get("enable", True))
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            return _format_result(result)

        elif tool_name == "wait_here":
            cmd = action_builder.wait(duration=tool_input.get("duration", 1.0))
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=15.0)
            return _format_result(result)

        elif tool_name == "stop":
            cmd = action_builder.stop()
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=3.0)
            return _format_result(result)

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        logger.error(f"Tool execution error: {tool_name}: {e}")
        return f"Error executing {tool_name}: {e}"


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


async def run_agent(
    conn: BridgeConnection,
    state: GameState,
    model: str = "claude-sonnet-4-20250514",
    goal: Optional[str] = None,
):
    """Main agent loop. Connects Claude to OpenMW via tool use."""

    client = AsyncAnthropic()  # Uses ANTHROPIC_API_KEY env var

    system = SYSTEM_PROMPT
    if goal:
        system += f"\n\nYour current goal: {goal}"

    messages = []

    logger.info(f"Starting agent loop with model={model}")
    if goal:
        logger.info(f"Goal: {goal}")

    # Get initial observation
    await wait_for_observation(conn, state, timeout=10.0)
    initial_context = state.summarize()
    messages.append({"role": "user", "content": f"You have just loaded into the game. Here is what you see:\n\n{initial_context}\n\nWhat would you like to do?"})

    while True:
        try:
            # Call Claude
            response = await client.messages.create(
                model=model,
                max_tokens=2048,
                system=system,
                tools=TOOLS,
                messages=messages,
            )

            # Add assistant response to messages
            messages.append({"role": "assistant", "content": response.content})

            # Log any text output
            for block in response.content:
                if hasattr(block, "text"):
                    logger.info(f"Claude: {block.text}")
                    print(f"\n🤖 Claude: {block.text}")

            # Handle tool use
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"Tool: {block.name}({block.input})")
                        print(f"  🔧 {block.name}({block.input})")
                        result_text = await execute_tool(block.name, block.input, conn, state)
                        logger.info(f"Result: {result_text[:200]}")
                        print(f"  📋 {result_text[:200]}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                messages.append({"role": "user", "content": tool_results})

            elif response.stop_reason == "end_turn":
                # Claude finished its turn, wait for next observation and prompt again
                await asyncio.sleep(1.0)
                await wait_for_observation(conn, state, timeout=5.0)
                changes = state.summarize_changes()
                summary = state.summarize()

                prompt = summary
                if changes:
                    prompt = f"=== Changes ===\n{changes}\n\n{summary}"
                prompt += "\n\nWhat would you like to do next?"
                messages.append({"role": "user", "content": prompt})

            # Trim conversation to avoid context overflow
            if len(messages) > 50:
                # Keep system context fresh: trim older exchanges but keep last 30
                messages = messages[-30:]

        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error(f"Agent loop error: {e}", exc_info=True)
            print(f"\n❌ Error: {e}")
            await asyncio.sleep(2.0)
            # Try to recover with a fresh observation
            await wait_for_observation(conn, state, timeout=5.0)
            messages.append({"role": "user", "content": f"An error occurred: {e}\n\nCurrent state:\n{state.summarize()}\n\nWhat would you like to do?"})
