# CLAUDE.md — OpenMW Bridge Project

## Project Overview

Fork of OpenMW (v0.51.0) for building a Claude-OpenMW agentic bridge. See `CLAUDE_BRIDGE_PLAN.md` for the full architecture plan.

**Goal:** Allow Claude to observe and control OpenMW in real time via a 4-layer bridge: C++ TCP socket → Lua mod → Python bridge server → Claude API.

## Build

```bash
cmake -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build
```

**Key CMake options:**
- `-DBUILD_COMPONENTS_TESTS=ON` — enable component unit tests
- `-DBUILD_OPENMW_TESTS=ON` — enable engine tests
- `-DBUILD_OPENCS_TESTS=ON` — enable Construction Set tests
- `-DBUILD_LAUNCHER=OFF` — skip launcher (speeds up build)
- `-DBUILD_OPENCS=OFF` — skip Construction Set
- `-DBUILD_WIZARD=OFF` — skip installation wizard

**Required:** CMake 3.16+, C++20 compiler, OpenSceneGraph 3.6.5+, Boost 1.70+, Bullet 286+, SDL2 2.0.20+, OpenAL, LuaJIT/Lua 5.1, MyGUI 3.4.3+, RecastNavigation, LZ4, ICU, yaml-cpp, FFmpeg, TinyXML.

**Windows note:** Link `ws2_32` for socket support (relevant for bridge C++ extension).

## Tests

```bash
# After building with test flags enabled:
ctest --test-dir build

# Integration tests (requires example-suite repo):
python3 scripts/integration_tests.py <example_suite_path> --omw build/openmw --verbose
```

Test sources:
- `apps/components_tests/` — GTest/GMock component tests
- `apps/openmw_tests/` — engine tests
- `apps/opencs_tests/` — Construction Set tests

## Code Style

- **C++20**, 120 char line limit, 4-space indent (no tabs)
- Pointer alignment: left (`int* ptr`)
- Braces on new lines for functions, classes, control statements
- `.clang-format` and `.clang-tidy` enforce style — run `clang-format` before committing
- Small, focused commits that build independently
- Reference: `CONTRIBUTING.md`

## Directory Structure

```
apps/openmw/mwlua/     — Lua scripting bindings (bridge C++ changes go here)
components/lua/         — Lua state management (bridge socket class goes here)
files/data/scripts/     — Built-in Lua scripts (bridge Lua mod goes here)
files/lua_api/openmw/   — Lua API documentation stubs
apps/openmw/mwinput/    — Input handling
apps/openmw/mwworld/    — World/cell management
apps/openmw/mwmechanics/ — Game mechanics (combat, AI, spells)
cmake/                  — CMake modules and macros
extern/sol3/            — Sol2/Sol3 Lua C++ binding library
```

## Bridge Implementation Reference

**C++ files to create:**
- `components/lua/bridgesocket.hpp/.cpp` — non-blocking TCP server socket
- `apps/openmw/mwlua/bridgebindings.hpp/.cpp` — sol2 bindings for `openmw.bridge`

**C++ files to modify:**
- `apps/openmw/CMakeLists.txt` — add `bridgebindings` to `add_openmw_dir(mwlua ...)`
- `components/CMakeLists.txt` — add `bridgesocket` to lua component
- `apps/openmw/mwlua/luabindings.cpp` — register `openmw.bridge` in Global + Player packages

**Lua mod files to create (under `files/data/scripts/claude_bridge/`):**
- `player.lua` — bridge connection owner, observations, player actions
- `global.lua` — world-scope operations via events
- `claude_bridge.omwscripts` — script registration

**Python bridge (separate directory at repo root: `bridge_server/`):**
- `connection.py`, `game_state.py`, `action_builder.py`, `claude_agent.py`, `main.py`

**Key reference files for implementation:**
- `apps/openmw/mwlua/luabindings.cpp` — how Lua packages are registered (initGlobalPackages, initPlayerPackages)
- `apps/openmw/mwlua/localscripts.cpp` — ActorControls bindings (movement, yaw, jump, use, sneak)
- `apps/openmw/mwlua/worldbindings.cpp` — world manipulation APIs
- `files/data/scripts/omw/input/playercontrols.lua` — reference for player control from Lua
- `apps/openmw/mwlua/luamanagerimp.cpp` — LuaManager lifecycle and threading
