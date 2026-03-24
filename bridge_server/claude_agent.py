"""Claude agent loop for controlling OpenMW."""

import asyncio
import logging
import os
from typing import Optional

from anthropic import AsyncAnthropic

from connection import BridgeConnection
from game_state import GameState
import action_builder
from knowledge import KnowledgeBase

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
    {
        "name": "talk_to",
        "description": "Start a conversation with a nearby NPC. Activates the NPC and waits for their greeting dialogue.",
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
        "description": "During a conversation, select a dialogue topic to ask the NPC about. Use the exact topic name from the available topics list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The dialogue topic to select",
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "navigate_to",
        "description": "Auto-walk to a named location or coordinates using pathfinding. Known locations: Seyda Neen, Balmora, Vivec, Ald-Ruhn, Caldera, Suran, Pelagiad, Gnisis, Molag Mar, Sadrith Mora, Ebonheart, Hla Oad, Tel Mora, Maar Gan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "destination": {
                    "type": "string",
                    "description": "Location name (e.g. 'Balmora') or coordinates as 'x,y,z'",
                },
            },
            "required": ["destination"],
        },
    },
    {
        "name": "read_journal",
        "description": "Read your quest journal with full text entries. Shows recent journal entries and active quest details.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "read_book",
        "description": "Read a book or scroll. Searches inventory first, then nearby items.",
        "input_schema": {
            "type": "object",
            "properties": {
                "book": {
                    "type": "string",
                    "description": "Name of the book or scroll to read",
                },
            },
            "required": ["book"],
        },
    },
    {
        "name": "remember",
        "description": "Save a note to your knowledge base for future sessions. Use this to record important discoveries, NPC locations, quest strategies, or anything you want to remember later.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["locations", "npcs", "quests", "strategies", "discoveries", "inventory"],
                    "description": "Category for the note",
                },
                "key": {
                    "type": "string",
                    "description": "Short identifier for this note (e.g. 'Caius Cosades', 'Balmora shops')",
                },
                "content": {
                    "type": "string",
                    "description": "The information to remember",
                },
            },
            "required": ["category", "key", "content"],
        },
    },
    {
        "name": "recall",
        "description": "Search your knowledge base for previously saved notes. Useful to remember what you learned in prior sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (matches against note keys and content)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "review_notes",
        "description": "List all notes in a category, or list all categories if no category specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category to review. Omit to list all categories.",
                },
            },
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
- Talk to NPCs and navigate dialogue topics (talk_to, select_topic)
- Navigate between cities using pathfinding (navigate_to)
- Read books, scrolls, and your quest journal for information

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
    knowledge: Optional[KnowledgeBase] = None,
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

        elif tool_name == "talk_to":
            # Activate the NPC to start dialogue, then wait for dialogue response
            cmd = action_builder.activate(target=tool_input["npc"])
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                # Wait for dialogue response
                dialogue_msg = await conn.recv_type("dialogue", timeout=5.0)
                if dialogue_msg:
                    lines = []
                    lines.append(f"Talking to: {dialogue_msg.get('npc', '?')}")
                    lines.append(f"Type: {dialogue_msg.get('dialogueType', '?')}")
                    lines.append(f"Text: {dialogue_msg.get('text', '(no text)')}")
                    return "\n".join(lines)
                else:
                    return "Activated NPC but no dialogue response received."
            return _format_result(result)

        elif tool_name == "select_topic":
            cmd = action_builder._action("select_topic", {"topic": tool_input["topic"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            # Also wait for the dialogue response with the topic text
            dialogue_msg = await conn.recv_type("dialogue", timeout=5.0)
            if dialogue_msg:
                return f"Topic '{tool_input['topic']}':\n{dialogue_msg.get('text', '(no text)')}"
            return _format_result(result)

        elif tool_name == "navigate_to":
            destination = tool_input["destination"]
            # Parse "x,y,z" format if provided
            params = {"destination": destination}
            if "," in destination:
                try:
                    parts = [float(p.strip()) for p in destination.split(",")]
                    if len(parts) == 3:
                        params = {"destination": {"x": parts[0], "y": parts[1], "z": parts[2]}}
                except ValueError:
                    pass
            cmd = action_builder._action("navigate_to", params)
            await conn.send(cmd)
            # Wait for initial result
            result = await conn.recv_by_id(cmd["id"], timeout=10.0)
            initial = _format_result(result)
            if result and result.get("success"):
                # Wait for completion or progress updates
                progress_lines = [initial]
                while True:
                    msg = await conn.recv(timeout=30.0)
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
                        state.update(msg)  # Keep state updated during navigation
                return "\n".join(progress_lines)
            return initial

        elif tool_name == "read_journal":
            # Get journal text from latest observation
            obs = await conn.drain_observations()
            if obs:
                state.update(obs)
            journal_texts = []
            if state.current:
                texts = state.current.get("journalTexts", [])
                for entry in texts:
                    quest_id = entry.get("questId", "")
                    text = entry.get("text", "")
                    journal_texts.append(f"[{quest_id}] {text}")
            quests = state.quests or []
            active = [q for q in quests if not q.get("finished")]
            lines = []
            if journal_texts:
                lines.append("=== Recent Journal Entries ===")
                lines.extend(journal_texts)
            if active:
                lines.append(f"\n=== Active Quests ({len(active)}) ===")
                for q in active:
                    lines.append(f"  {q.get('id', '?')} (stage {q.get('stage', '?')})")
            return "\n".join(lines) if lines else "Journal is empty."

        elif tool_name == "read_book":
            cmd = action_builder._action("read_book", {"target": tool_input["book"]})
            await conn.send(cmd)
            result = await conn.recv_by_id(cmd["id"], timeout=5.0)
            if result and result.get("success"):
                title = result.get("bookTitle", tool_input["book"])
                text = result.get("message", "(no text)")
                is_scroll = result.get("isScroll", False)
                kind = "Scroll" if is_scroll else "Book"
                return f"=== {kind}: {title} ===\n{text}"
            return _format_result(result)

        elif tool_name == "remember":
            if not knowledge:
                return "Knowledge base not available."
            result = knowledge.save(
                tool_input["category"],
                tool_input["key"],
                tool_input["content"],
            )
            return result

        elif tool_name == "recall":
            if not knowledge:
                return "Knowledge base not available."
            results = knowledge.search(tool_input["query"])
            if not results:
                return "No matching notes found."
            lines = []
            for r in results:
                lines.append(f"[{r['category']}] {r['key']}: {r['value']}")
            return "\n".join(lines)

        elif tool_name == "review_notes":
            if not knowledge:
                return "Knowledge base not available."
            category = tool_input.get("category")
            if category:
                entries = knowledge.get_all(category)
                if not entries:
                    return f"No notes in '{category}'."
                lines = [f"=== {category} ({len(entries)} notes) ==="]
                for key, value in entries.items():
                    lines.append(f"  {key}: {str(value)[:200]}")
                return "\n".join(lines)
            else:
                cats = knowledge.list_categories()
                if not cats:
                    return "Knowledge base is empty."
                return "Categories:\n" + "\n".join(f"  {c}" for c in cats)

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
    knowledge: Optional[KnowledgeBase] = None,
):
    """Main agent loop. Connects Claude to OpenMW via tool use."""

    client = AsyncAnthropic()  # Uses ANTHROPIC_API_KEY env var

    system = SYSTEM_PROMPT
    if goal:
        system += f"\n\nYour current goal: {goal}"

    if knowledge:
        prior = knowledge.get_summary()
        if prior and prior != "No prior knowledge saved.":
            system += f"\n\n{prior}"

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
                        result_text = await execute_tool(block.name, block.input, conn, state, knowledge)
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
