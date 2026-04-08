"""Goal generator — produces the next goal based on game state and history."""

import logging
from typing import Optional

from anthropic import AsyncAnthropic

from game_state import GameState
from knowledge import KnowledgeBase

logger = logging.getLogger(__name__)

GOAL_PROMPT = """You are a goal planner for a character playing The Elder Scrolls III: Morrowind.

Based on the current game state, quest journal, and what has been accomplished so far,
pick the NEXT concrete goal for the character to pursue.

Rules:
- Pick ONE specific, actionable goal (not vague like "explore" — say WHERE and WHY)
- If the player has a BOUNTY > 0, the goal MUST be to clear it (find a guard and pay, or use Thieves Guild)
- Prioritize active quests that have clear next steps
- If no quests are active, suggest exploring a new area, finding work, or talking to interesting NPCs
- Consider the character's level, equipment, and location when picking difficulty
- Keep the goal achievable in 5-15 minutes of gameplay
- Return ONLY the goal text, nothing else — no explanation, no bullet points

Good examples:
- "Travel to Balmora and report to Caius Cosades at his house to get your first orders"
- "Go to the Balmora Fighters Guild and ask for work from Eydis Fire-Eye"
- "Explore the smuggler cave south of Seyda Neen and clear it out"
- "Visit Arrille's Tradehouse in Seyda Neen to sell loot and buy potions"
- "Find the Dwemer puzzle box in Arkngthand for Hasphat Antabolis"

Bad examples:
- "Explore the world" (too vague)
- "Become the Nerevarine" (too big)
- "Do some quests" (not specific)
"""


REVISE_PROMPT = """You are revising a goal list for an AI playing Morrowind. The player just died attempting a goal.

Given:
- The failed goal and how the player died
- The remaining goals in the queue
- The player's current state (level, equipment, location)

Return a SINGLE replacement goal that:
- Is achievable at the player's current level and equipment
- Helps the player get stronger before re-attempting the failed goal (e.g. buy better equipment, train skills, do easier quests first)
- Is specific and actionable

Return ONLY the replacement goal text, nothing else.
"""


async def suggest_replacement_goal(
    failed_goal: str,
    death_summary: str,
    remaining_goals: list[str],
    state,
    knowledge=None,
    model: str = "claude-sonnet-4-20250514",
) -> str:
    """Use a high-reasoning model to suggest a replacement goal after death."""
    client = AsyncAnthropic()

    context_parts = [
        f"FAILED GOAL: {failed_goal}",
        f"DEATH: {death_summary}",
    ]
    if remaining_goals:
        goals_text = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(remaining_goals))
        context_parts.append(f"REMAINING GOALS:\n{goals_text}")
    if state and state.current:
        context_parts.append(f"\nPLAYER STATE:\n{state.summarize()}")
    if knowledge:
        prior = knowledge.get_summary()
        if prior and prior != "No prior knowledge saved.":
            context_parts.append(f"\nKNOWLEDGE:\n{prior}")

    response = await client.messages.create(
        model=model,
        max_tokens=200,
        system=REVISE_PROMPT,
        messages=[{"role": "user", "content": "\n\n".join(context_parts)}],
    )

    goal = ""
    for block in response.content:
        if hasattr(block, "text"):
            goal += block.text
    goal = goal.strip()
    logger.info(f"Suggested replacement goal: {goal}")
    return goal


BREAKDOWN_PROMPT = """You are breaking down a complex Morrowind quest goal into smaller, sequential sub-goals.

Given:
- The complex goal that needs to be broken down
- The player's current state (level, location, equipment, quests)
- What has been attempted so far (if anything)

Return a numbered list of 2-5 specific, actionable sub-goals that together accomplish the original goal.
Each sub-goal should be completable in a few minutes of gameplay.
Include navigation, preparation, and execution steps as separate sub-goals.

Return ONLY the numbered list, one sub-goal per line. No explanations.

Example input: "Become Hortator of House Hlaalu"
Example output:
1. Travel to Balmora and find the Hlaalu Council Manor. Talk to the councilors to learn what they require.
2. Complete the tasks the councilors ask of you to gain their support.
3. Return to each councilor who supported you and ask them to name you Hortator.
"""


async def break_down_goal(
    goal: str,
    state,
    knowledge=None,
    attempt_summary: str = "",
    model: str = "claude-sonnet-4-20250514",
) -> list[str]:
    """Break a complex goal into smaller sequential sub-goals."""
    client = AsyncAnthropic()

    context_parts = [f"GOAL TO BREAK DOWN: {goal}"]
    if attempt_summary:
        context_parts.append(f"WHAT WAS ATTEMPTED: {attempt_summary}")
    if state and state.current:
        context_parts.append(f"\nPLAYER STATE:\n{state.summarize()}")
    if knowledge:
        prior = knowledge.get_summary()
        if prior and prior != "No prior knowledge saved.":
            context_parts.append(f"\nKNOWLEDGE:\n{prior}")

    response = await client.messages.create(
        model=model,
        max_tokens=500,
        system=BREAKDOWN_PROMPT,
        messages=[{"role": "user", "content": "\n\n".join(context_parts)}],
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    import re
    sub_goals = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        cleaned = re.sub(r'^\d+[\.\)]\s*', '', line)
        if cleaned:
            sub_goals.append(cleaned)

    logger.info(f"Broke down goal into {len(sub_goals)} sub-goals")
    for i, sg in enumerate(sub_goals, 1):
        logger.info(f"  Sub-goal {i}: {sg}")
    return sub_goals


async def generate_goal(
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    completed_goal: Optional[str] = None,
    completed_summary: Optional[str] = None,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """Generate the next goal based on current game state."""
    client = AsyncAnthropic()

    context_parts = []

    if completed_goal:
        context_parts.append(f"Just completed: {completed_goal}")
    if completed_summary:
        context_parts.append(f"What happened: {completed_summary}")

    context_parts.append(f"\n{state.summarize()}")

    if knowledge:
        prior = knowledge.get_summary()
        if prior and prior != "No prior knowledge saved.":
            context_parts.append(f"\nKnowledge:\n{prior}")

    context = "\n".join(context_parts)

    response = await client.messages.create(
        model=model,
        max_tokens=200,
        system=GOAL_PROMPT,
        messages=[{"role": "user", "content": context}],
    )

    goal = ""
    for block in response.content:
        if hasattr(block, "text"):
            goal += block.text

    goal = goal.strip()
    logger.info(f"Generated new goal: {goal}")
    return goal
