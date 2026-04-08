# OpenMW Bridge — Conventions

## Improvement Tracking

All improvements are tracked in `improvement_tracker.json` via the observer tool.

### View status
```bash
python observe_and_improve.py --status
```

### Mark an improvement as implemented
```bash
python observe_and_improve.py --done 17
```

### How improvements are found
The observer agent (`observe_and_improve.py`) watches gameplay logs and identifies issues. In watch mode, it auto-adds new improvements to the tracker and sends a shutdown signal to the bridge after finding 5 new issues.

### Improvement lifecycle
1. **Found** — Observer identifies an issue during gameplay analysis and adds it to `improvement_tracker.json` with status `open`
2. **Implemented** — Developer implements the fix and runs `--done ID` to mark it
3. **Verified** — Next gameplay session confirms the fix works (no formal tracking yet)

### Tracker file format (`improvement_tracker.json`)
```json
{
  "improvements": [
    {
      "id": 1,
      "title": "Goal skipping — orchestrator ignores goal queue",
      "impact": "CRITICAL",
      "fix_type": "Prompt Change + Bug Fix",
      "recommendation": "Added goal discipline section...",
      "status": "implemented",
      "found_at": "2026-04-07 19:30",
      "implemented_at": "2026-04-07 20:42"
    }
  ]
}
```

### Impact levels
- **CRITICAL** — Agent freezes, crashes, or can't play at all
- **HIGH** — Major gameplay capability missing or broken
- **MEDIUM** — Suboptimal behavior, missing quality-of-life feature
- **LOW** — Minor issue, cosmetic, or rare edge case

### Fix types
- **Bug Fix** — Something broken that needs code repair
- **New Tool** — New C++ binding + Lua action + Python handler
- **New Agent** — New sub-agent with prompt and orchestrator integration
- **Prompt Change** — Modify agent prompts to change behavior
- **Knowledge Entry** — Add game data to the knowledge base

---

## Running the System

### Play with observer
```bash
python run.py --multi-agent --goal-file goals.txt --budget 50 --observe-interval 5
```

### Play without observer
```bash
python run.py --multi-agent --goal-file goals.txt --no-observer
```

### Manual shutdown (saves game + writes resume file)
```bash
python observe_and_improve.py --shutdown "Reason for stopping"
```

### Resume after shutdown
```bash
python run.py --multi-agent --goal-file goals_resume.txt
```

### Post-session analysis
```bash
python observe_and_improve.py --all --output report.md
python review_session.py --latest
```

---

## Adding New Capabilities

Every new game action follows the 4-layer pattern:

1. **C++ binding** in `bridgebindings.cpp` — Direct engine API call
2. **Lua action** in `actions.lua` — Handler in `processCommand` that calls the C++ binding
3. **Python tool** in `claude_agent.py` — Handler in `execute_tool` that sends the action over the bridge
4. **Agent integration** — Add tool to relevant sub-agent or orchestrator, update prompts

### Sub-agent pattern
- `agents/<name>.py` — Follows `shopping.py` pattern (load prompt, define tools, `run_<name>` function)
- `agents/prompts/<name>.txt` — Workflow, rules, tips
- Wire into `orchestrator.py` — Import, add to `ORCHESTRATOR_TOOLS`, add dispatch in `_execute_orchestrator_tool`

### C++ rebuild required for
- New bindings in `bridgebindings.cpp`
- Changes to `actors.cpp`, `aisequence.cpp`, `aicombat.cpp`, `engine.cpp`

### No rebuild needed for
- Lua changes (`actions.lua`, `player.lua`) — loaded from disk on OpenMW restart
- Python changes — loaded on bridge restart
- Prompt changes — loaded on bridge restart

---

## Goal File Format (`goals.txt`)

```
# Comments start with #
1. First goal description
2. Second goal description
```

- One goal per line, executed sequentially
- Optional numeric prefix (stripped automatically)
- Agent calls `goal_complete` to advance
- Failed goals get a prerequisite inserted by the goal generator
- On death, loads last-completed-goal save and suggests a replacement
- When queue is empty, agent stops (with `--goal-file`) or generates goals (without)

---

## Structured Logs

| Log | Location | Format | When Written |
|-----|----------|--------|-------------|
| Debug log | `bridge_debug.log` | Text | Every tool call (via Python logging) |
| Gameplay log | `gameplay_logs/*.jsonl` | JSONL | Every action, goal, death, snapshot (flushed immediately) |
| Session log | `session_logs/*.json` | JSON | On session exit |
| Adventure log | `adventures/*.json` | JSON | On session exit |
| Observer report | `live_report.md` | Markdown | Every observer analysis |
