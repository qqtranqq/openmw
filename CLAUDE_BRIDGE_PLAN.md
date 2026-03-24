# Claude-OpenMW Agentic Bridge Plan

A bridge that allows Claude (Anthropic's AI) to observe and control OpenMW in real time, enabling agentic gameplay of Morrowind.

## Architecture

```
Claude (Anthropic API / MCP)
    │  JSON over HTTP
Bridge Server (Python, asyncio)
    │  TCP socket (JSON Lines protocol)
C++ IPC Extension (new Lua package: openmw.bridge)
    │  Lua API calls
OpenMW Lua Mod (Global + Player scripts)
```

**Core design decision: thin C++ layer, thick Lua layer.** The only engine modification is a TCP socket binding. All game-state gathering and action execution lives in Lua scripts, which use the full existing `openmw.*` API surface. This minimizes fork divergence from upstream OpenMW and makes the system easy to iterate on without recompilation.

---

## Phase 1: C++ IPC Extension (`openmw.bridge`)

Adds a single new Lua package, `openmw.bridge`, that provides a non-blocking TCP socket. This is the **only** C++ change required.

### Design Decisions

- **TCP over named pipes**: Works cross-platform and from WSL. Named pipes would be faster but add platform-specific complexity.
- **Non-blocking with poll-per-frame**: The socket must never block the game loop. The Lua side calls `bridge.poll()` each frame. Sending is also non-blocking (buffered).
- **Single connection, localhost only**: The server listens on `127.0.0.1:port`. Only one client (the Python bridge) connects.
- **JSON Lines framing**: Newline-delimited JSON messages. Simple and sufficient since game state JSON will not contain raw newlines.
- **Registered as Global+Player package**: Needs both Global context (for world manipulation) and Player context (for input/camera/UI).

### New Files

#### `components/lua/bridgesocket.hpp` / `bridgesocket.cpp`

Non-blocking TCP server socket wrapper. Interface:

- `BridgeSocket(uint16_t port)` — binds and listens
- `void update()` — accepts pending connections, reads available data
- `std::optional<std::string> recv()` — returns next complete message (newline-delimited)
- `void send(std::string_view msg)` — queues a message for sending
- `bool isConnected()` — whether a client is connected

Uses platform sockets (`<sys/socket.h>` on Linux, `<winsock2.h>` on Windows). No external dependencies.

#### `apps/openmw/mwlua/bridgebindings.hpp` / `bridgebindings.cpp`

sol2 bindings exposing `openmw.bridge` to Lua:

- `bridge.start(port)` — starts listening
- `bridge.stop()` — closes connection
- `bridge.isConnected()` — boolean
- `bridge.send(jsonString)` — sends a message
- `bridge.poll()` — returns table of received message strings (0 or more)
- `bridge.getPort()` — returns configured port

### Modified Files

- **`apps/openmw/CMakeLists.txt`** — Add `bridgebindings` to the `add_openmw_dir(mwlua ...)` list. On Windows, link `ws2_32`.
- **`components/CMakeLists.txt`** — Add `bridgesocket` to the lua component sources.
- **`apps/openmw/mwlua/luabindings.cpp`** — Register `openmw.bridge` into both `initGlobalPackages` and `initPlayerPackages`.

### Threading Safety

The socket is only accessed from the Lua thread (single-threaded within one frame's update). The `BridgeSocket` object should be owned by the `LuaManager` with its lifetime managed there.

---

## Phase 2: OpenMW Lua Mod

Two Lua scripts under `files/data/scripts/claude_bridge/`:

### `player.lua` (PLAYER script)

Owns the bridge connection:

- **`onFrame(dt)`**: Polls bridge for commands, executes action queue, sends observations
- **Gathers**: position, rotation, cell, health/magicka/fatigue, inventory, equipment, nearby actors/objects/doors, active effects, quest state
- **Executes**: movement (`self.controls.movement/sideMovement/yawChange`), jump, activate, equip, use item, attack, sneak

### `global.lua` (GLOBAL script)

Handles world-scope operations:

- Teleport objects, create objects, query world state, cell info
- Receives commands from player script via OpenMW's event system

### `claude_bridge.omwscripts`

```
GLOBAL: scripts/claude_bridge/global.lua
PLAYER: scripts/claude_bridge/player.lua
```

### Message Protocol (JSON Lines over TCP)

#### Observation (game → bridge, every N frames)

```json
{
  "type": "observation",
  "timestamp": 12345.6,
  "player": {
    "position": [1234.5, 5678.9, 100.0],
    "rotation": [0.0, 0.0, 1.57],
    "cell": "Balmora",
    "health": {"current": 50, "max": 100},
    "magicka": {"current": 30, "max": 80},
    "fatigue": {"current": 100, "max": 100},
    "level": 5,
    "activeEffects": [],
    "equipment": {"right_hand": "iron dagger"},
    "inventory": [{"id": "gold_001", "count": 500}]
  },
  "nearby": {
    "actors": [{"id": "ref_001", "name": "Caius Cosades", "position": [1230, 5670, 100], "health": {"current": 100, "max": 100}, "hostile": false}],
    "doors": [{"id": "ref_002", "name": "Caius Cosades' House", "position": [1240, 5680, 100]}],
    "items": [{"id": "ref_003", "name": "Scroll of Icarian Flight", "position": [1235, 5675, 100]}],
    "activators": []
  },
  "quests": {"active": [], "journal_entries": []},
  "dialogue": null
}
```

#### Action Command (bridge → game)

```json
{
  "id": "cmd_123",
  "type": "action",
  "action": "move",
  "params": {"direction": "forward", "duration": 1.0, "run": true}
}
```

#### Action Result (game → bridge)

```json
{
  "type": "action_result",
  "id": "cmd_123",
  "success": true
}
```

### Supported Actions

#### Player Script Actions

| Action | Description | Implementation |
|--------|-------------|----------------|
| `move` | Walk/run in a direction | `self.controls.movement/sideMovement` for N frames |
| `turn` | Rotate the player | `self.controls.yawChange` for N frames |
| `look` | Look up/down | `self.controls.pitchChange` |
| `jump` | Jump | `self.controls.jump = true` for 1 frame |
| `use` | Attack/cast | `self.controls.use` |
| `sneak` | Toggle sneak | `self.controls.sneak` |
| `activate` | Interact with object | `target:activateBy(self)` on nearby object |
| `equip` | Equip item | `types.Actor.setEquipment(self, ...)` |
| `drop_item` | Drop item | Inventory management API |
| `navigate_to` | Pathfind to location | `nearby.castNavigationRay` + movement loop |
| `wait` | Do nothing | No-op for N frames |

#### Global Script Actions (via events)

| Action | Description | Implementation |
|--------|-------------|----------------|
| `teleport` | Teleport player/object | `object:teleport(cell, position)` |
| `create_object` | Spawn an object | `world.createObject(recordId, count)` |
| `get_world_info` | Query world state | Return via event |

### Frame-Based Action Execution

Movement actions span multiple frames. The player script maintains an **action queue**. Each `onFrame(dt)`:

1. Poll bridge for new commands
2. Execute current action (set controls)
3. Check if action is complete (duration elapsed, target reached)
4. Send observation data every N frames (configurable, default ~5-10 frames = ~3-6 times/sec)

---

## Phase 3: Python Bridge Server

```
bridge_server/
    __init__.py
    connection.py       # Async TCP client to OpenMW
    game_state.py       # Parses observations, maintains state, computes deltas
    action_builder.py   # Typed action constructors with validation
    claude_agent.py     # Agent loop using Anthropic SDK + tool use
    mcp_server.py       # Alternative: MCP server for Claude Desktop
    main.py             # Entry point
```

### `connection.py`

- Async TCP client using `asyncio`
- Connects to `127.0.0.1:port`
- JSON Lines framing
- Request/response correlation via `id` field
- Timeout handling for action commands

### `game_state.py`

- Maintains latest observation
- Tracks history (recent observations for context)
- Computes deltas (what changed since last observation)
- Summarizes world state for Claude's context window

### `action_builder.py`

- Typed Python functions generating action JSON
- Validates parameters before sending

### `claude_agent.py`

Agent loop using Anthropic Python SDK with tool use:

1. Get observation from game
2. Format as system/user message
3. Call Claude API with tools
4. Execute tool calls → send actions to game
5. Wait for results / next observation
6. Repeat

### `mcp_server.py` (Alternative)

MCP server implementation allowing Claude Desktop or any MCP client to control the game. Each game action becomes an MCP tool, game state available as MCP resources.

---

## Phase 4: Claude Tool Definitions

Mid-level tools — not individual frame controls, not complete-quest-level:

| Tool | Description |
|------|-------------|
| `look_around` | Observe nearby NPCs, objects, doors, items with positions |
| `move_to` | Walk/run toward a named target or coordinates |
| `activate` | Interact with object (open door, talk to NPC, pick up item) |
| `check_status` | Player health, magicka, fatigue, active effects |
| `check_inventory` | List all inventory items with counts |
| `equip_item` | Equip weapon, armor, or clothing |
| `use_item` | Consume potion or scroll |
| `attack` | Attack a target, approach if not in range |
| `check_journal` | Read quest log and active quests |
| `wait_time` | Wait/rest for game hours |

### Agent System Prompt

```
You are playing Morrowind through OpenMW. You can observe the game world and take
actions using the provided tools. Your goal is to explore, complete quests, and
survive. Think step by step about what you see and what to do next.
```

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Socket blocking game loop | High | All ops non-blocking with 0-timeout `select()`/`poll()` |
| Thread safety of socket access | High | Socket only accessed from Lua thread, single-threaded within frame |
| Observation data too large for context | Medium | Summarize aggressively, radius-limited nearby queries, cap lists |
| Action timing mismatches | Medium | Duration-based actions with completion callbacks, configurable obs rate |
| Fork divergence from upstream | Medium | Minimal C++ changes (~5 files modified, all new code isolated) |
| Windows/Linux socket portability | Low | Standard BSD sockets + Winsock, `#ifdef _WIN32` wrappers |

---

## Implementation Order

1. **C++ IPC Extension** — `BridgeSocket` + sol2 bindings + CMake wiring. Verify: Lua ↔ Python message exchange.
2. **Lua Mod: Observations** — Player script serializes game state. Verify: Python receives world state JSON.
3. **Lua Mod: Actions** — Action queue + all player/world actions. Verify: Python commands move the player.
4. **Python Bridge + Claude** — Agent loop with tool use. Verify: Claude walks around Seyda Neen and interacts with NPCs.

---

## Key Reference Files

- `apps/openmw/mwlua/luabindings.cpp` — Where `openmw.bridge` must be registered
- `apps/openmw/mwlua/localscripts.cpp` — `ActorControls` bindings (movement, yaw, jump, use, sneak)
- `apps/openmw/CMakeLists.txt` — Build configuration for mwlua
- `files/data/scripts/omw/input/playercontrols.lua` — Reference for how player controls work from Lua
- `apps/openmw/mwlua/worldbindings.cpp` — World manipulation API bindings

---

## Phase 5: Autonomous Learning Extensions

Extensions required for Claude to independently play and learn Morrowind. Organized into three sub-phases by dependency and complexity.

### Phase 5A: Core Gameplay (Lua + Python, no C++ changes)

These three extensions are required for Claude to actually play the game. Without them, it can move around but can't follow quests, navigate, or talk to NPCs.

#### Extension 1: Dialogue System

Morrowind is 90% dialogue. The Lua API already exposes dialogue text — this just needs wiring.

**Existing APIs:**
- `DialogueResponse` engine event fires when NPC speaks (provides `actor`, `type`, `infoId`, `recordId`)
- `core.dialogue.topic.records[id].infos[i].text` — raw dialogue text
- `core.dialogue.greeting`, `core.dialogue.journal`, `core.dialogue.persuasion` — other dialogue types
- `ui._getUiModeStack()` — detect when `"Dialogue"` UI is open
- `types.Player.journal(player).topics` — known conversation topics

**New file: `files/data/scripts/claude_bridge/dialogue.lua`**
- Event handler for `DialogueResponse`: looks up text via `core.dialogue`, sends over bridge
- Detects dialogue UI open/closed via `ui._getUiModeStack()`
- Gathers available topics the player knows
- Sends: `{"type": "dialogue", "npc": "Caius Cosades", "text": "...", "availableTopics": ["little secret", "duties", ...]}`

**Modify: `actions.lua`**
- `select_topic` action: select a dialogue topic during conversation
- `end_dialogue` action: close the dialogue window

**Modify: `claude_agent.py`**
- `talk_to` tool: activate an NPC and wait for dialogue response
- `select_topic` tool: choose a topic during conversation
- `end_conversation` tool: close dialogue

#### Extension 2: Journal & Book Text Reading

The APIs exist — `types.Player.journal()` and `types.Book.records[id].text` — just not wired to the bridge.

**Modify: `player.lua` observations**
- Expand quest observation: include `types.Player.journal(self.object).journalTextEntries[i].text` for the last 10 entries
- Include `topics` with conversation text from `types.Player.journal(self.object).topics`

**Modify: `actions.lua`**
- `read_book` action: given a book name, find in inventory or nearby, return `types.Book.record(item).text`
- Send: `{"type": "book_content", "title": "...", "text": "...", "isScroll": bool}`

**Modify: `claude_agent.py`**
- `read_journal` tool: returns full journal text (not just quest IDs)
- `read_book` tool: returns text content of a book/scroll

#### Extension 3: Navigation & Pathfinding

The `nearby.findPath()` API already provides full navmesh pathfinding. This extension wraps it as a high-level action.

**New file: `files/data/scripts/claude_bridge/navigation.lua`**
- `navigate_to(position)` action: calls `nearby.findPath(self.position, dest)`
- Returns list of waypoints; each `onFrame`, faces and moves toward next waypoint
- Reports progress: `{"type": "navigation_progress", "waypointsRemaining": N, "distance": D}`
- Reports completion: `{"type": "action_complete", "id": "...", "success": true}`
- Stuck detection: if position unchanged for 2s, try jump/sidestep, then retry path. After 3 failures, report stuck.

**Named location registry** (hardcoded table in navigation.lua):
```lua
local LOCATIONS = {
    ["Seyda Neen"] = {x = ..., y = ..., z = ...},
    ["Balmora"] = {x = ..., y = ..., z = ...},
    ["Vivec"] = {x = ..., y = ..., z = ...},
    -- etc.
}
```
Claude can say `navigate_to("Balmora")` instead of raw coordinates.

**Modify: `claude_agent.py`**
- `navigate_to` tool: accepts a location name or `{x, y, z}` coordinates
- Waits for `navigation_progress` and `action_complete` messages
- Reports progress to Claude during long walks

### Phase 5B: Persistent Memory (Python only)

Allows Claude to learn across sessions. No Lua or C++ changes.

**New file: `bridge_server/knowledge.py`**
- File-backed JSON store at `bridge_server/knowledge/`
- Categories: `locations.json`, `npcs.json`, `quests.json`, `strategies.json`, `discoveries.json`
- API:
  - `save(category, key, value)` — store a note
  - `load(category, key)` — retrieve a note
  - `search(query)` — fuzzy search across all categories
  - `list(category)` — list all keys in a category
- Auto-saves after writes, loads on startup

**Modify: `claude_agent.py`**
- `remember` tool: save a note (category + key + content)
- `recall` tool: search knowledge base by query
- `review_notes` tool: list all notes in a category

**Modify: `main.py` / agent startup**
- On startup, load all knowledge and inject summary into system prompt as "prior knowledge from previous sessions"
- Claude starts each session knowing what it learned before

### Phase 5C: Screen Capture / Vision (C++ + Lua + Python)

Gives Claude visual understanding of the game. The only extension requiring C++ changes.

**Existing C++ infrastructure:**
- `MWRender::ScreenshotManager::screenshot(osg::Image*, w, h)` — captures framebuffer
- Uses `ReadImageFromFramebufferCallback` on the render thread
- Not currently exposed to Lua

**New C++ bindings: extend `bridgebindings.cpp`**
- Add `bridge.screenshot(path)` function to existing `openmw.bridge` package
- Queues a screenshot request for the render thread
- Saves PNG to the specified file path
- Returns success/failure
- Threading: must synchronize with render thread (use callback similar to existing screenshot mechanism)

**Alternative approach (simpler):** Instead of Lua bindings, have the Python side send a keypress to trigger OpenMW's built-in screenshot (F12), then read the file. Less elegant but zero C++ changes.

**Modify: `claude_agent.py`**
- `look_around_visual` tool: triggers screenshot, reads PNG file, sends to Claude as an image content block via the Anthropic API's multimodal input
- Use sparingly — visual processing is slower and more expensive than structured data
- Best for: spatial orientation, finding paths, reading signs, understanding room layouts

---

## Full Implementation Order

| Step | Phase | What | Changes | Verify |
|------|-------|------|---------|--------|
| 1 | Phase 1 ✅ | C++ IPC Extension | C++ socket + sol2 bindings | Lua ↔ Python message exchange |
| 2 | Phase 2 ✅ | Lua Mod | player.lua + actions.lua + global.lua + json.lua | Python receives observations, sends actions |
| 3 | Phase 3 ✅ | Python Bridge | connection + game_state + action_builder + claude_agent | Claude walks around, interacts |
| 4 | Phase 5A.1 | Dialogue System | dialogue.lua + actions.lua + claude_agent.py | Claude talks to NPCs, reads responses, chooses topics |
| 5 | Phase 5A.2 | Journal/Book Text | player.lua + actions.lua + claude_agent.py | Claude reads journal text and books |
| 6 | Phase 5A.3 | Navigation | navigation.lua + claude_agent.py | Claude pathfinds between locations |
| 7 | Phase 5B | Persistent Memory | knowledge.py + claude_agent.py + main.py | Claude remembers across sessions |
| 8 | Phase 5C | Screen Capture | bridgebindings.cpp or F12 workaround + claude_agent.py | Claude sees the game visually |

**Milestone:** After steps 1–6, Claude can autonomously explore Morrowind, talk to NPCs, follow quest instructions, navigate between cities, and manage combat. Step 7 adds cross-session learning. Step 8 adds visual awareness.

---

## Phase 6: Knowledge Bootstrap from Game Guides

Pre-load the knowledge base with structured information from publicly available Morrowind strategy guides (e.g. GameFAQs text walkthroughs) so Claude starts each session with expert-level game knowledge.

### Architecture

```
knowledge_bootstrap/
    guides/                 # Raw downloaded guide text files
    parsed/                 # Structured JSON output
    parser.py               # Parse raw FAQ text into structured JSON
    claude_parser.py        # Alternative: use Claude to parse guides
    import_knowledge.py     # Load parsed data into knowledge base
```

### Step 1: Download Guides

Manually download the most useful text guides from GameFAQs (they block automated fetching). Save as `.txt` files in `knowledge_bootstrap/guides/`.

| Priority | Guide Type | What to Extract |
|----------|-----------|-----------------|
| 1 | Main Quest Walkthrough | Step-by-step quest instructions, NPC names, locations, dialogue choices |
| 2 | NPC/Location Guide | Every NPC: name, role, location, services |
| 3 | Item/Artifact Guide | Unique weapons, armor, their locations and stats |
| 4 | Alchemy Ingredient Guide | All ingredients, their 4 effects, where to find them |
| 5 | Trainer Guide | Skill trainers: name, skill, max level, location, cost |
| 6 | Faction Walkthroughs | Fighters/Mages/Thieves Guild, Temple, Imperial Legion quest steps |
| 7 | Map/Navigation Guide | Directions between cities, landmarks, silt strider/boat routes |

### Step 2: Parse Guides

Two approaches, use whichever fits:

#### Approach A: Regex Parser (`parser.py`)

GameFAQs text guides follow predictable formatting: section headers with `====`/`----` underlines, numbered steps, fixed-width tables.

```python
def parse_walkthrough(text) -> dict:
    """Split by section headers, extract quest steps, NPC refs, locations."""

def parse_npc_guide(text) -> dict:
    """Extract NPC name, location, services, inventory."""

def parse_item_guide(text) -> dict:
    """Extract item name, type, stats, location."""

def parse_alchemy_guide(text) -> dict:
    """Extract ingredient, effects (1-4), weight, value, sources."""

def parse_trainer_guide(text) -> dict:
    """Extract trainer name, skill, max level, location, cost."""
```

#### Approach B: Claude-Assisted Parser (`claude_parser.py`)

Use Claude to parse raw guide text into structured JSON. Slower/costlier but handles any format without custom parsers.

```python
async def parse_with_claude(guide_text: str, guide_type: str) -> dict:
    """Send guide chunk to Claude, get back structured JSON."""
    # Prompt Claude to extract into categories:
    #   locations, npcs, quests, items, strategies
    # Returns valid JSON matching knowledge base schema
```

For large guides (200KB+), split by section headers first, process each section independently, then merge results.

### Step 3: Import to Knowledge Base (`import_knowledge.py`)

Map parsed data into the existing `KnowledgeBase` categories:

```python
from bridge_server.knowledge import KnowledgeBase
kb = KnowledgeBase()

# Locations: name → {description, services, travel_connections, notable_npcs}
# NPCs: name → {location, role, services, quests}
# Quests: name → {faction, giver, location, steps[], reward}
# Items: name → {type, location, stats, how_to_get}
# Strategies: topic → advice text
# Inventory: item → {type, location, stats}
```

### Step 4: Verify & Curate

```bash
# Check what was imported
cd bridge_server
python -c "from knowledge import KnowledgeBase; kb = KnowledgeBase(); print(kb.get_summary())"

# Manually review/edit JSON files in bridge_server/knowledge/*.json
```

### Step 5: Run Bootstrap

```bash
# One-time setup
cd knowledge_bootstrap

# Option A: regex parsing
python import_knowledge.py --guides-dir ./guides/ --method regex

# Option B: Claude-assisted parsing
export ANTHROPIC_API_KEY=...
python import_knowledge.py --guides-dir ./guides/ --method claude
```

### Expected Output

| Category | ~Entries | Source |
|----------|---------|--------|
| `locations` | ~50 | Map guide, walkthrough |
| `npcs` | ~200 | NPC guide, trainer guide |
| `quests` | ~100 | Main + faction walkthroughs |
| `strategies` | ~30 | Character build, alchemy, combat guides |
| `inventory` | ~100 | Item/artifact guide |
| `discoveries` | ~50 | Lore, secrets, Easter eggs |

**Result:** ~530 knowledge entries before Claude starts playing — equivalent to a player who has read the strategy guide. Claude will know where to go, who to talk to, and what items to look for from the first session.
