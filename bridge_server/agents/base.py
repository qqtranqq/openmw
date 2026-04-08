"""Base infrastructure for sub-agents."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

from anthropic import AsyncAnthropic

from connection import BridgeConnection
from game_state import GameState
from knowledge import KnowledgeBase
import action_builder

logger = logging.getLogger(__name__)


@dataclass
class SubAgentResult:
    """Result returned by a sub-agent to the orchestrator."""
    success: bool
    summary: str
    actions_taken: int = 0
    error: Optional[str] = None
    state_snapshot: str = ""

    def to_tool_result(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        parts = [f"[{status}] {self.summary}"]
        if self.error:
            parts.append(f"Error: {self.error}")
        if self.state_snapshot:
            parts.append(f"\nCurrent state:\n{self.state_snapshot}")
        return "\n".join(parts)


# Tool executor signature
ToolExecutor = Callable[
    [str, dict, BridgeConnection, GameState, Optional[KnowledgeBase]],
    Awaitable[str],
]

# The "done" tool definition — added to every sub-agent
DONE_TOOL = {
    "name": "done",
    "description": "Signal that your task is complete (or has failed). You MUST call this when finished.",
    "input_schema": {
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "True if the goal was achieved, False if it failed.",
            },
            "summary": {
                "type": "string",
                "description": "Brief description of what happened and where you are now.",
            },
        },
        "required": ["success", "summary"],
    },
}


async def _ensure_dialogue_closed(conn: BridgeConnection, state: GameState):
    """Safety: close dialogue if it's open. Prevents getting stuck."""
    dialogue = state.current.get("dialogue") if state.current else None
    if dialogue and dialogue.get("active"):
        try:
            cmd = action_builder._action("close_dialogue", {})
            await conn.send(cmd)
            await conn.recv_by_id(cmd["id"], timeout=3.0)
            logger.info("Sub-agent safety: auto-closed dialogue on exit")
        except Exception:
            pass


async def run_sub_agent(
    goal: str,
    system_prompt: str,
    tools: list[dict],
    tool_executor: ToolExecutor,
    conn: BridgeConnection,
    state: GameState,
    knowledge: Optional[KnowledgeBase] = None,
    model: str = "claude-sonnet-4-20250514",
    max_turns: int = 25,
    initial_context: Optional[str] = None,
) -> SubAgentResult:
    """Generic sub-agent loop. Runs a focused agent to completion."""

    client = AsyncAnthropic()
    messages = []
    actions_taken = 0

    # Ensure tools include the done tool
    all_tools = tools + [DONE_TOOL]

    # Build initial message
    first_msg = f"Goal: {goal}"
    ctx = initial_context or state.summarize()
    first_msg += f"\n\nCurrent situation:\n{ctx}"
    messages.append({"role": "user", "content": first_msg})

    try:
        for turn in range(max_turns):
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=system_prompt,
                    tools=all_tools,
                    messages=messages,
                )
            except Exception as e:
                error_str = str(e).lower()
                if "rate" in error_str or "429" in error_str or "overloaded" in error_str:
                    logger.warning("Sub-agent rate limited, waiting 30s...")
                    await asyncio.sleep(30.0)
                    continue
                return SubAgentResult(
                    success=False,
                    summary=f"API error after {actions_taken} actions",
                    actions_taken=actions_taken,
                    error=str(e),
                    state_snapshot=state.summarize(),
                )

            messages.append({"role": "assistant", "content": response.content})

            # Log text
            for block in response.content:
                if hasattr(block, "text"):
                    logger.info(f"    sub-agent: {block.text[:200]}")

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        # Check for done tool
                        if block.name == "done":
                            return SubAgentResult(
                                success=block.input.get("success", True),
                                summary=block.input.get("summary", "Task completed."),
                                actions_taken=actions_taken,
                                state_snapshot=state.summarize(),
                            )

                        logger.info(f"    sub-agent tool: {block.name}({block.input})")
                        result_text = await tool_executor(
                            block.name, block.input, conn, state, knowledge
                        )
                        actions_taken += 1
                        logger.info(f"    sub-agent result: {result_text[:150]}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                messages.append({"role": "user", "content": tool_results})
                await asyncio.sleep(1.0)  # pace between tool rounds

            elif response.stop_reason == "end_turn":
                # Sub-agent stopped without calling done
                last_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        last_text = block.text
                return SubAgentResult(
                    success=True,
                    summary=last_text or "Task completed (no explicit summary).",
                    actions_taken=actions_taken,
                    state_snapshot=state.summarize(),
                )

        # Max turns reached
        return SubAgentResult(
            success=False,
            summary=f"Reached max turns ({max_turns}) without completing goal.",
            actions_taken=actions_taken,
            error="max_turns_exceeded",
            state_snapshot=state.summarize(),
        )

    finally:
        # Safety: ensure dialogue is closed when sub-agent exits
        await _ensure_dialogue_closed(conn, state)
