# OpenMW Bridge — Improvement Notes

## Session: 2026-04-07

### What We Built Today

**Core Architecture Improvements:**
- Plan-based execution (`execute_plan` tool) — agent executes multi-step plans without API round-trips between each action
- Prompt caching — system prompt + tools cached to reduce latency and cost
- Observation diffing — sends compact diffs instead of full state on follow-up turns
- Goal generation system — auto-generates goals, handles goal completion/failure/death
- Sequential goal files — `--goal-file goals.txt` for scripted playthroughs
- Cost tracking with `--budget` — saves game and writes resume file when $10 remains
- Structured session logging for post-game analysis (`review_session.py`)

**Combat System:**
- C++ changes to allow player to use AiCombat AI packages (movement, pathfinding, evasion)
- Hybrid combat: manual spell casting (agent picks spells strategically) + AI melee (engine swings weapon)
- Lua `controls.use` with `overrideCombatControls(true)` for reliable weapon swings
- Summon-first strategy with spell failure detection and retry
- Post-combat healing to 80% before reporting success
- Death detection with auto-reload and knowledge logging

**New Sub-Agents:**
- Shopping agent — buy/sell items with `get_merchant_inventory`, `buy_item`, `sell_item`
- Exploration agent — wander overworld, discover locations, collect resources

**New Tools:**
- `get_contents` / `take_item` / `take_all` — read and loot containers/corpses directly (bypasses UI)
- `drop_item` — manage encumbrance
- `check_journal` — read quest journal entries by quest ID
- `goal_complete(success=bool)` — distinguish success from failure
- `break_down_goal` — decompose complex goals into sub-goals when stuck

**Bug Fixes:**
- Morrowind combat scripts (`data-mw/`) weren't being loaded — added to `builtin.omwscripts` and copied to `data/`
- Stale AI combat packages blocking manual play — `isBridgeConnected()` guard
- Death screen blocking bridge — Lua auto-reload with AI cleanup
- Spell casting not firing — `overrideCombatControls(true)` during cast action
- Bounty awareness across all agent prompts

---

### Missing Agents / Capabilities Needed

#### 1. Alchemy Agent
- Morrowind alchemy is extremely powerful for making money and potions
- Agent needs to: identify valuable ingredient combinations, create potions, sell them
- Requires: `create_potion` Lua action, ingredient effect knowledge
- **Impact: High** — alchemy is the #1 money maker in Morrowind

#### 2. Enchanting Agent
- Enchanting items is key to mid/late game power
- Agent needs to: identify soul gems, fill them, use enchanting services
- Requires: `enchant_item` Lua action, soul trap spell awareness
- **Impact: Medium** — becomes important after level 5

#### 3. Training Agent
- Paying for skill training is the fastest way to level up
- Agent needs to: find trainers, identify which skills to train, manage gold
- Requires: knowledge base of trainer locations and max skill levels
- **Impact: High** — critical for progressing past early game

#### 4. Lockpicking / Trap Detection
- Many containers and doors are locked
- Agent currently can't pick locks or detect traps
- Requires: `pick_lock` Lua action using Security skill, trap detection
- **Impact: Medium** — lots of loot locked behind doors

#### 5. Persuasion / Speechcraft Agent
- Many quests require persuading NPCs (admire, intimidate, taunt)
- Agent has no persuasion tools
- Requires: `persuade` Lua action with disposition tracking
- **Impact: High** — needed for Hortator quests and many side quests

#### 6. Rest / Wait System
- Agent can't rest to restore health/magicka or wait for shops to restock
- Morrowind requires resting in beds or wilderness
- Requires: `rest` Lua action (rest for N hours)
- **Impact: High** — agent runs out of magicka and has no way to recover between fights

#### 7. Map / Navigation Memory
- Agent doesn't remember where it's been or build a mental map
- Gets lost in cities and can't find buildings it visited before
- Could track visited cells and door locations in knowledge base
- **Impact: Medium** — would reduce wasted navigation time

#### 8. Inventory Management Agent
- Agent doesn't optimize equipment loadout
- No awareness of armor rating, weapon damage values, or best-in-slot
- Should compare new items against equipped items before equipping
- Requires: armor rating in observations, weapon damage stats
- **Impact: Medium** — currently equips things blindly

#### 9. Quest Log Intelligence
- Agent reads journal but doesn't track multi-step quest state well
- Could maintain a structured quest tracker in knowledge base
- Track: current objective, NPCs involved, locations to visit
- **Impact: Medium** — would help with complex quests

#### 10. Stealth / Thievery Agent
- Some quests require stealing or sneaking
- Agent has `sneak` but no pickpocket, steal-from-container awareness
- Would need crime/bounty risk assessment before acting
- **Impact: Low** — most quests don't require stealth

---

### Performance Observations

- **API cost per goal:** ~$1-3 depending on complexity (combat goals cost more due to sub-agent calls)
- **Biggest cost driver:** Combat sub-agent — multiple look_around + attack cycles
- **Spell failure rate:** ~50% at level 1 — wastes turns and magicka
- **Navigation reliability:** Good for cities, poor for finding specific buildings/doors
- **Combat effectiveness:** Works with summon + melee hybrid, but low-level misses are frustrating
- **Gate/door awareness:** Combat agent now knows about gates but still sometimes misses them

### Bugs Observed During Play

1. **Goal skipping** — The orchestrator skipped goals 2-6 (Arrille shopping, lighthouse, Hrisskar, Addamasartus) and jumped from goal 1 (Fargoth) straight to Balmora. The orchestrator seems to be acting on its own initiative rather than following the goal queue. It may not be calling `goal_complete` when it finishes a goal — instead it just keeps acting and the goal system never advances. Need to investigate whether `goal_complete` is being called or if the orchestrator is ignoring the current goal.

2. **Navigation struggles in cities** — The agent has trouble finding specific buildings in Balmora. It wanders between houses looking for Caius Cosades' house. The `navigate_to` tool only works for cities, not specific buildings within them. Could benefit from a building/door database in the knowledge base.

3. **Ping response warning** — "Warning: No ping response. The bridge mod may not be loaded." appears on startup even though the bridge works fine. The ping might be timing out before the Lua script initializes.

#### 11. Level-Up Handling (CRITICAL BLOCKER)
- When the player levels up, OpenMW shows a `GM_Levelup` dialog requiring 3 attribute selections
- This blocks the game the same way the death screen does — agent gets stuck
- The agent has zero level-up awareness — no detection, no attribute selection, no UI bypass
- Requires: C++ binding to either auto-level (pick best attributes based on multipliers) or programmatic attribute selection
- The Lua API exposes `skillIncreasesForAttribute` (multiplier data) and `level.progress` (how close to leveling)
- Key C++ file: `apps/openmw/mwgui/levelupdialog.cpp` — `onOkButtonClicked()` shows the attribute increment logic
- Options:
  A. **Auto-level in Lua** — detect `level.progress >= 10`, call a C++ binding that picks the 3 attributes with highest multipliers and calls `pcStats.levelUp()`
  B. **Agent-controlled** — add a `level_up` tool where the LLM picks attributes. More flexible but costs API calls
  C. **Hybrid** — auto-detect in Lua, send `level_up_available` event to Python with multiplier info, let LLM choose or auto-pick
- **Impact: CRITICAL** — without this the agent will freeze on level-up, losing all session progress

### Priority Improvements (Next Session)

1. **Goal skipping fix** — orchestrator must follow goal queue strictly
2. **Level-up handling** — CRITICAL blocker, agent freezes on level-up screen
3. **Rest system** — critical for magicka recovery between fights
4. **Armor/weapon stat awareness** — easy win, Lua only
5. **Persuasion tools** — needed for many main quest steps
6. **City navigation** — auto-save doors, search_area tool
7. **Training agent** — fastest path to becoming combat-effective
8. **Alchemy** — money printing machine
