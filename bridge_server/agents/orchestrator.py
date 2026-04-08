"""Orchestrator agent — high-level game-playing decisions."""

import asyncio
import logging
import os
from typing import Optional

from anthropic import AsyncAnthropic

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
import action_builder
from claude_agent import execute_tool as _base_execute_tool, wait_for_observation
from goal_generator import generate_goal, suggest_replacement_goal, break_down_goal
from agents.navigation import run_navigation
from agents.dialogue import run_dialogue
from agents.combat import run_combat
from agents.loot import run_loot
from agents.shopping import run_shopping
from agents.explore import run_explore
from agents.stealth import run_stealth
from agents.alchemy import run_alchemy

logger = logging.getLogger(__name__)


def _load_prompt() -> str:
    path = os.path.join(os.path.dirname(__file__), "prompts", "orchestrator.txt")
    with open(path, "r") as f:
        return f.read()


ORCHESTRATOR_TOOLS = [
    {
        "name": "look_around",
        "description": "Get observation of current surroundings.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "lookup",
        "description": "Search game knowledge for NPC locations, travel routes, quest info, items.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "navigate",
        "description": "Send the navigation agent to move you somewhere. It handles pathfinding, fast travel, entering buildings. Give a clear goal in natural language.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Where to go, e.g. 'Travel to Balmora and enter Caius Cosades house'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "talk",
        "description": "Send the dialogue agent to talk to a nearby NPC. It handles the full conversation. The NPC must be nearby — navigate first if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "Who to talk to and what about, e.g. 'Talk to Caius Cosades about orders and duties'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "combat",
        "description": "Send the combat agent to fight an enemy or handle a dangerous situation. It handles weapon equipping, attacking, healing, and fleeing. Describe what to fight or how to handle the threat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to fight and how, e.g. 'Kill the mudcrab' or 'Flee from the bandit'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "loot",
        "description": "Send the looting agent to pick up nearby items, loot dead bodies, and search containers. Use after combat or when exploring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to loot, e.g. 'Loot all dead enemies and search containers' or 'Pick up the weapon on the ground'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "save_game",
        "description": "Save the game with a description. Use before dangerous situations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Save description, e.g. 'Before entering cave'",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "load_game",
        "description": "Load a previously saved game. Use this after dying or getting into an unrecoverable situation. Searches saves by description.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Save description to search for (e.g. 'Before entering cave')",
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "shop",
        "description": "Send the shopping agent to buy or sell items at a nearby merchant NPC. The merchant must be nearby — navigate first if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to buy/sell and from whom, e.g. 'Buy health potions from Arrille' or 'Sell excess weapons to the trader'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "explore",
        "description": "Send the exploration agent to wander the overworld, discover caves/ruins/camps, and collect resources (ingredients, items). Good for finding new adventure locations and gathering supplies when you need resources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to explore and look for, e.g. 'Explore the coast south of Seyda Neen for caves and resources' or 'Forage ingredients near Balmora'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "stealth",
        "description": "Send the stealth agent to steal items, pick locks, or complete objectives that require sneaking. It handles sneaking, checking for witnesses, and taking owned items. Use for quests that require theft or when items are marked [OWNED].",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to steal or sneak-accomplish, e.g. 'Steal the diamond from the chest in this room' or 'Take the owned potion without being seen'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "alchemy",
        "description": "Send the alchemy agent to brew potions from ingredients in your inventory. It previews combinations and brews the best potions. You need a Mortar & Pestle and at least 2 ingredients with shared effects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What potions to brew, e.g. 'Brew Restore Health potions from available ingredients' or 'Make the best potions from all my ingredients'",
                },
            },
            "required": ["goal"],
        },
    },
    {
        "name": "rest",
        "description": "Rest for hours to restore health, magicka, and fatigue. Use after combat when wounded. 1-8 hours.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Hours to rest (default: 1)"},
            },
            "required": [],
        },
    },
    {
        "name": "get_training_services",
        "description": "Check what training a nearby NPC trainer offers. Shows skills, prices, and whether you can train each one. The NPC must be nearby.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the trainer NPC"},
            },
            "required": ["npc"],
        },
    },
    {
        "name": "train_skill",
        "description": "Pay a nearby trainer NPC to increase a skill by one level. Can repeat multiple times with count. The NPC must be nearby and offer training.",
        "input_schema": {
            "type": "object",
            "properties": {
                "npc": {"type": "string", "description": "Name of the trainer NPC"},
                "skill": {"type": "string", "description": "Name of the skill to train (e.g. 'Long Blade', 'Athletics')"},
                "count": {"type": "integer", "description": "Number of times to train (default 1, max 10)"},
            },
            "required": ["npc", "skill"],
        },
    },
    {
        "name": "wait_and_observe",
        "description": "Wait a moment and get a fresh observation of surroundings.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "break_down_goal",
        "description": (
            "Break your current goal into smaller sub-goals when it's too complex or you're struggling. "
            "The current goal is replaced by 2-5 sequential sub-goals. Use when you've tried multiple "
            "approaches and the goal isn't progressing, or when the goal clearly requires multiple distinct steps."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the goal needs to be broken down (e.g. 'Too complex, need to visit multiple locations' or 'Failed twice, need a step-by-step approach')",
                },
            },
            "required": ["reason"],
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
                    "description": "What you accomplished, or why the goal failed",
                },
            },
            "required": ["success", "summary"],
        },
    },
]


async def _execute_orchestrator_tool(
    tool_name: str,
    tool_input: dict,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase],
    sub_agent_model: str,
) -> str:
    """Execute an orchestrator tool — may dispatch a sub-agent."""

    if tool_name == "navigate":
        logger.info(f"  Dispatching navigation agent: {tool_input['goal']}")
        result = await run_navigation(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Navigation result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "talk":
        logger.info(f"  Dispatching dialogue agent: {tool_input['goal']}")
        result = await run_dialogue(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Dialogue result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "loot":
        logger.info(f"  Dispatching loot agent: {tool_input['goal']}")
        result = await run_loot(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Loot result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "stealth":
        logger.info(f"  Dispatching stealth agent: {tool_input['goal']}")
        result = await run_stealth(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Stealth result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "combat":
        logger.info(f"  Dispatching combat agent: {tool_input['goal']}")
        result = await run_combat(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Combat result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "explore":
        logger.info(f"  Dispatching exploration agent: {tool_input['goal']}")
        result = await run_explore(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Exploration result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "shop":
        logger.info(f"  Dispatching shopping agent: {tool_input['goal']}")
        result = await run_shopping(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Shopping result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "alchemy":
        logger.info(f"  Dispatching alchemy agent: {tool_input['goal']}")
        result = await run_alchemy(
            goal=tool_input["goal"],
            conn=conn, state=state, knowledge=knowledge,
            model=sub_agent_model,
        )
        logger.info(f"  Alchemy result: {result.summary[:150]}")
        return result.to_tool_result()

    elif tool_name == "save_game":
        return await _base_execute_tool("save_game", tool_input, conn, state, knowledge)

    elif tool_name == "load_game":
        return await _base_execute_tool("load_game", tool_input, conn, state, knowledge)

    elif tool_name == "look_around":
        return await _base_execute_tool("look_around", {}, conn, state, knowledge)

    elif tool_name == "lookup":
        return await _base_execute_tool("lookup", tool_input, conn, state, knowledge)

    elif tool_name == "rest":
        return await _base_execute_tool("rest", tool_input, conn, state, knowledge)

    elif tool_name == "get_training_services":
        return await _base_execute_tool("get_training_services", tool_input, conn, state, knowledge)

    elif tool_name == "train_skill":
        return await _base_execute_tool("train_skill", tool_input, conn, state, knowledge)

    elif tool_name == "wait_and_observe":
        await asyncio.sleep(3.0)
        await wait_for_observation(conn, state, timeout=5.0)
        return state.summarize()

    else:
        return f"Unknown tool: {tool_name}"


async def run_orchestrator(
    conn: BridgeConnection,
    state: GameState,
    model: str = "claude-sonnet-4-20250514",
    goal: Optional[str] = None,
    knowledge: Optional[KnowledgeBase] = None,
    sub_agent_model: Optional[str] = None,
    time_limit_minutes: int = 0,
    director_queue: Optional[asyncio.Queue] = None,
    action_log=None,
    pace: float = 2.0,
    # Accept but ignore these for compatibility with main.py's call signature
    system_prompt_override=None,
    adventure_context=None,
    goal_queue: Optional[list] = None,
    cost_tracker=None,
    goal_file: Optional[str] = None,
):
    """Main orchestrator loop."""
    import time as _time

    if sub_agent_model is None:
        sub_agent_model = model

    client = AsyncAnthropic()

    base_system_text = _load_prompt()
    current_goal = goal

    if knowledge:
        prior = knowledge.get_summary()
        if prior and prior != "No prior knowledge saved.":
            base_system_text += f"\n\n{prior}"

    def _build_cached_system(goal_text):
        text = base_system_text
        if goal_text:
            text += f"\n\nYour current goal: {goal_text}"
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    cached_system = _build_cached_system(current_goal)
    cached_tools = []
    for i, tool in enumerate(ORCHESTRATOR_TOOLS):
        t = dict(tool)
        if i == len(ORCHESTRATOR_TOOLS) - 1:
            t["cache_control"] = {"type": "ephemeral"}
        cached_tools.append(t)

    messages = []
    start_time = _time.monotonic()

    # Stuck detection: track recent tool calls per goal
    goal_tool_history = []  # list of (tool_name, success_bool)
    goal_turn_count = 0

    logger.info(f"Orchestrator started (model={model}, sub-agent={sub_agent_model})")
    if goal:
        logger.info(f"Goal: {goal}")

    # Get initial observation
    await wait_for_observation(conn, state, timeout=10.0)

    # Generate an initial goal if none was provided
    if not current_goal:
        logger.info("No goal provided, generating initial goal...")
        current_goal = await generate_goal(state, knowledge)
        cached_system = _build_cached_system(current_goal)
        print(f"Goal: {current_goal}")

    initial = state.summarize()
    messages.append({
        "role": "user",
        "content": f"[CURRENT GOAL: {current_goal}]\n\nYou have just loaded into the game.\n\n{initial}\n\nWhat would you like to do?",
    })

    SHUTDOWN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".shutdown")

    while True:
        # Check for external shutdown signal
        if os.path.exists(SHUTDOWN_FILE):
            reason = ""
            try:
                with open(SHUTDOWN_FILE, "r") as f:
                    reason = f.read().strip()
                os.remove(SHUTDOWN_FILE)
            except Exception:
                pass
            print(f"\nShutdown requested: {reason or 'external signal'}")
            save_desc = f"Shutdown: {(current_goal or 'unknown')[:50]}"
            save_cmd = action_builder._action("save_game", {"description": save_desc})
            await conn.send(save_cmd)
            await conn.recv_by_id(save_cmd["id"], timeout=5.0)
            if goal_file:
                from claude_agent import _write_resume_file
                _write_resume_file(goal_file, current_goal, goal_queue, state,
                    cost_tracker or type('', (), {"total_cost": 0, "call_count": 0})())
            if cost_tracker:
                print(f"\n{cost_tracker.summary()}")
            break

        # Check time limit
        if time_limit_minutes > 0:
            elapsed = (_time.monotonic() - start_time) / 60.0
            if elapsed >= time_limit_minutes:
                print(f"\nSession ended ({elapsed:.1f} minutes).")
                break

        # Check for director notes
        if director_queue is not None:
            while not director_queue.empty():
                try:
                    note = director_queue.get_nowait()
                    messages.append({"role": "user", "content": f"[Director's note]: {note}"})
                    print(f"\n  Director: {note}")
                except asyncio.QueueEmpty:
                    break

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=512,
                system=cached_system,
                tools=cached_tools,
                messages=messages,
            )

            # Track API cost
            if cost_tracker:
                cost_tracker.record(response, model)
                warning = cost_tracker.check_and_warn()
                if warning:
                    print(f"\n{warning}")
                    logger.warning(warning)
                if cost_tracker.should_shutdown:
                    print(f"\nBudget limit reached. Saving game and writing context...")
                    save_cmd = action_builder._action("save_game", {"description": f"Budget limit: {current_goal[:50]}"})
                    await conn.send(save_cmd)
                    await conn.recv_by_id(save_cmd["id"], timeout=5.0)
                    if goal_file:
                        from claude_agent import _write_resume_file
                        _write_resume_file(goal_file, current_goal, goal_queue, state, cost_tracker)
                    print(f"\n{cost_tracker.summary()}")
                    return

            messages.append({"role": "assistant", "content": response.content})

            for block in response.content:
                if hasattr(block, "text"):
                    logger.info(f"Orchestrator: {block.text}")
                    print(f"\nOrchestrator: {block.text}")

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"Orchestrator tool: {block.name}({block.input})")
                        if block.name == "break_down_goal":
                            reason = block.input.get("reason", "")
                            print(f"\nBreaking down goal: {reason}")
                            logger.info(f"Breaking down goal '{current_goal}': {reason}")
                            await wait_for_observation(conn, state, timeout=5.0)
                            sub_goals = await break_down_goal(
                                goal=current_goal,
                                state=state,
                                knowledge=knowledge,
                                attempt_summary=reason,
                            )
                            if sub_goals and len(sub_goals) > 1:
                                # Replace current goal with first sub-goal, queue the rest
                                new_goal = sub_goals[0]
                                if goal_queue is not None:
                                    # Insert remaining sub-goals at front of queue
                                    for sg in reversed(sub_goals[1:]):
                                        goal_queue.insert(0, sg)
                                else:
                                    goal_queue = list(sub_goals[1:])
                                current_goal = new_goal
                                goal_tool_history = []
                                goal_turn_count = 0
                                cached_system = _build_cached_system(current_goal)
                                print(f"Sub-goals created:")
                                print(f"  1. {new_goal} (now)")
                                for i, sg in enumerate(sub_goals[1:], 2):
                                    print(f"  {i}. {sg}")
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": f"Goal broken down into {len(sub_goals)} sub-goals. Your current sub-goal: {new_goal}",
                                })
                            else:
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": f"Could not break down further. Continue with current goal: {current_goal}",
                                })
                            if action_log is not None:
                                action_log.append({
                                    "type": "tool",
                                    "name": "break_down_goal",
                                    "input": block.input,
                                    "result": f"Sub-goals: {sub_goals}",
                                })
                            continue
                        if block.name == "goal_complete":
                            summary = block.input.get("summary", "")
                            goal_success = block.input.get("success", True)

                            if goal_success:
                                print(f"\nGoal complete: {summary}")
                                save_desc = f"Goal done: {(current_goal or 'unknown')[:60]}"
                                save_cmd = action_builder._action("save_game", {"description": save_desc})
                                await conn.send(save_cmd)
                                await conn.recv_by_id(save_cmd["id"], timeout=5.0)
                                logger.info(f"Auto-saved: {save_desc}")
                                await wait_for_observation(conn, state, timeout=5.0)
                                if goal_queue:
                                    new_goal = goal_queue.pop(0)
                                    remaining = len(goal_queue)
                                    logger.info(f"Next goal from queue ({remaining} remaining): {new_goal}")
                                elif goal_queue is not None:
                                    print(f"\nAll goals completed!")
                                    return
                                else:
                                    new_goal = await generate_goal(
                                        state, knowledge,
                                        completed_goal=current_goal,
                                        completed_summary=summary,
                                    )
                            else:
                                print(f"\nGoal failed: {summary}")
                                await wait_for_observation(conn, state, timeout=5.0)
                                if goal_queue is not None:
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
                            goal_tool_history = []
                            goal_turn_count = 0
                            cached_system = _build_cached_system(current_goal)
                            print(f"New goal: {current_goal}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"Goal completed. Your new goal: {current_goal}",
                            })
                            if action_log is not None:
                                action_log.append({
                                    "type": "tool",
                                    "name": "goal_complete",
                                    "input": block.input,
                                    "result": f"New goal: {current_goal}",
                                })
                            continue
                        result_text = await _execute_orchestrator_tool(
                            block.name, block.input,
                            conn, state, knowledge,
                            sub_agent_model=sub_agent_model,
                        )
                        logger.info(f"Orchestrator result: {result_text[:200]}")
                        if action_log is not None:
                            action_log.append({
                                "type": "tool",
                                "name": block.name,
                                "input": block.input,
                                "result": result_text[:500],
                            })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                messages.append({"role": "user", "content": tool_results})
                goal_turn_count += 1

                # Track tool calls for stuck detection
                for block in response.content:
                    if block.type == "tool_use" and block.name not in ("goal_complete", "break_down_goal"):
                        is_fail = any(
                            tr.get("tool_use_id") == block.id
                            and "FAILED" in tr.get("content", "").upper()
                            for tr in tool_results
                        )
                        goal_tool_history.append((block.name, not is_fail))

                # Stuck detection: check recent history
                if len(goal_tool_history) >= 6:
                    recent = goal_tool_history[-6:]
                    recent_failures = sum(1 for _, ok in recent if not ok)
                    recent_tools = [name for name, _ in recent]
                    # Detect: 4+ failures in last 6 calls
                    # Or: same tool called 4+ times in last 6 (looping)
                    from collections import Counter
                    tool_counts = Counter(recent_tools)
                    most_common_tool, most_common_count = tool_counts.most_common(1)[0]
                    is_stuck = recent_failures >= 4 or most_common_count >= 4 or goal_turn_count >= 15

                    if is_stuck:
                        stuck_reason = ""
                        if recent_failures >= 4:
                            stuck_reason = f"{recent_failures}/6 recent sub-agent calls failed"
                        elif most_common_count >= 4:
                            stuck_reason = f"called '{most_common_tool}' {most_common_count} times in last 6 turns without progress"
                        else:
                            stuck_reason = f"spent {goal_turn_count} turns on this goal without completing it"

                        print(f"\nStuck detected: {stuck_reason}")
                        logger.warning(f"Stuck detected: {stuck_reason}")
                        messages.append({
                            "role": "user",
                            "content": (
                                f"[CURRENT GOAL: {current_goal}]\n\n"
                                f"WARNING: You appear to be stuck. {stuck_reason}. "
                                f"You MUST either call break_down_goal to split this into smaller steps, "
                                f"or call goal_complete with success=false to move on. "
                                f"Do NOT repeat the same failing approach."
                            ),
                        })
                        # Reset to avoid spamming the warning every turn
                        goal_tool_history = goal_tool_history[-3:]
                        goal_turn_count = max(0, goal_turn_count - 5)

                await asyncio.sleep(pace)

            elif response.stop_reason == "end_turn":
                await asyncio.sleep(pace)
                await wait_for_observation(conn, state, timeout=5.0)
                changes = state.summarize_changes()
                if changes:
                    messages.append({
                        "role": "user",
                        "content": f"[CURRENT GOAL: {current_goal}]\n\n{changes}\n\nWhat next?",
                    })
                else:
                    messages.append({
                        "role": "user",
                        "content": f"[CURRENT GOAL: {current_goal}]\n\n{state.summarize()}\n\nWhat next?",
                    })

            # Trim context
            if len(messages) > 30:
                messages = messages[-15:]
                # Re-inject goal reminder after trimming
                messages.append({"role": "user", "content": f"REMINDER: Your current goal is: {current_goal}. Focus on this goal only. Call goal_complete when done."})

        except KeyboardInterrupt:
            raise
        except TypeError as e:
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                logger.error(f"Authentication failed: {e}")
                print(f"\nFatal: ANTHROPIC_API_KEY not set. Export it and retry.")
                raise
            raise
        except Exception as e:
            error_str = str(e).lower()
            if "rate" in error_str or "429" in error_str or "overloaded" in error_str:
                logger.warning("Rate limited — waiting 30s...")
                await asyncio.sleep(30.0)
                continue
            logger.error(f"Orchestrator error: {e}", exc_info=True)
            await asyncio.sleep(5.0)
            await wait_for_observation(conn, state, timeout=5.0)
            messages.append({
                "role": "user",
                "content": f"[CURRENT GOAL: {current_goal}]\n\nError: {e}\n\n{state.summarize()}\n\nWhat next?",
            })
