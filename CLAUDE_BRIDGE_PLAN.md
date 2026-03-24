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
