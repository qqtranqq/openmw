"""Action command builders for the OpenMW bridge protocol."""

from typing import Optional

_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return f"cmd_{_counter:04d}"


def _action(action: str, params: Optional[dict] = None, cmd_id: Optional[str] = None) -> dict:
    """Build a standard action command dict."""
    msg = {
        "type": "action",
        "id": cmd_id or _next_id(),
        "action": action,
    }
    if params:
        msg["params"] = params
    return msg


# --- Player actions (handled by player.lua actions module) ---

def move(direction: str = "forward", duration: float = 1.0, run: bool = True, cmd_id: Optional[str] = None) -> dict:
    """Move in a direction for a duration.

    direction: forward, backward, left, right
    duration: seconds
    run: whether to run (True) or walk (False)
    """
    return _action("move", {"direction": direction, "duration": duration, "run": run}, cmd_id)


def turn(angle: float = 0.5, duration: float = 0.5, cmd_id: Optional[str] = None) -> dict:
    """Turn the player.

    angle: radians, positive = right, negative = left
    duration: seconds over which to spread the turn
    """
    return _action("turn", {"angle": angle, "duration": duration}, cmd_id)


def look(angle: float = 0.0, cmd_id: Optional[str] = None) -> dict:
    """Look up/down.

    angle: radians, positive = down, negative = up
    """
    return _action("look", {"angle": angle}, cmd_id)


def jump(cmd_id: Optional[str] = None) -> dict:
    """Jump."""
    return _action("jump", cmd_id=cmd_id)


def activate(target: str, cmd_id: Optional[str] = None) -> dict:
    """Interact with a nearby object by name.

    target: name substring to match (case-insensitive)
    Works for: doors, NPCs, items, containers, activators
    """
    return _action("activate", {"target": target}, cmd_id)


def equip(item: str, slot: Optional[int] = None, cmd_id: Optional[str] = None) -> dict:
    """Equip an item from inventory.

    item: item name substring to match
    slot: optional equipment slot number (auto-detected if omitted)
    """
    params = {"item": item}
    if slot is not None:
        params["slot"] = slot
    return _action("equip", params, cmd_id)


def attack(duration: float = 1.0, cmd_id: Optional[str] = None) -> dict:
    """Attack with equipped weapon.

    duration: seconds to hold the attack
    """
    return _action("attack", {"duration": duration}, cmd_id)


def cast(spell: Optional[str] = None, cmd_id: Optional[str] = None) -> dict:
    """Cast a spell.

    spell: spell ID (optional, uses currently selected if omitted)
    """
    params = {}
    if spell:
        params["spell"] = spell
    return _action("cast", params, cmd_id)


def stop(cmd_id: Optional[str] = None) -> dict:
    """Stop current action and stand still."""
    return _action("stop", cmd_id=cmd_id)


def wait(duration: float = 1.0, cmd_id: Optional[str] = None) -> dict:
    """Wait in place for a duration.

    duration: seconds to wait
    """
    return _action("wait", {"duration": duration}, cmd_id)


def sneak(enable: bool = True, cmd_id: Optional[str] = None) -> dict:
    """Toggle sneaking.

    enable: True to start sneaking, False to stop
    """
    return _action("sneak", {"enable": enable}, cmd_id)


# --- Global actions (forwarded to global.lua via events) ---

def teleport(cell: str, x: float, y: float, z: float, cmd_id: Optional[str] = None) -> dict:
    """Teleport the player to a cell and position."""
    return _action("teleport", {"cell": cell, "position": {"x": x, "y": y, "z": z}}, cmd_id)


def create_object(record_id: str, count: int = 1, into_inventory: bool = False, cmd_id: Optional[str] = None) -> dict:
    """Create a game object.

    record_id: the object's record ID
    count: number to create
    into_inventory: True to add to player inventory, False to place in world
    """
    return _action("create_object", {
        "recordId": record_id,
        "count": count,
        "intoInventory": into_inventory,
    }, cmd_id)


def advance_time(hours: float, cmd_id: Optional[str] = None) -> dict:
    """Advance world time by a number of hours."""
    return _action("advance_time", {"hours": hours}, cmd_id)


def pause(unpause: bool = False, cmd_id: Optional[str] = None) -> dict:
    """Pause or unpause the game world."""
    return _action("pause", {"unpause": unpause}, cmd_id)


def get_world_info(cmd_id: Optional[str] = None) -> dict:
    """Request world information from the global script."""
    return _action("get_world_info", cmd_id=cmd_id)


# --- Utility ---

def ping(cmd_id: Optional[str] = None) -> dict:
    """Send a ping message."""
    return {"type": "ping", "id": cmd_id or _next_id()}
