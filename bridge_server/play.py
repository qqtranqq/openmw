#!/usr/bin/env python3
"""Interactive bridge client - send commands to OpenMW manually."""
import socket
import json
import sys
import time
import threading
import os

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 21003
cmd_counter = 0
sock = None
last_observation = None

def next_id():
    global cmd_counter
    cmd_counter += 1
    return f"cmd_{cmd_counter:04d}"

def send(msg):
    sock.sendall((json.dumps(msg, separators=(',',':')) + '\n').encode())

def action(name, params=None):
    msg = {"type": "action", "id": next_id(), "action": name}
    if params:
        msg["params"] = params
    send(msg)
    return msg["id"]

def reader_thread():
    global last_observation
    buffer = ""
    while True:
        try:
            data = sock.recv(65536).decode('utf-8')
            if not data:
                print("\n[Disconnected]")
                break
            buffer += data
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                    mtype = msg.get('type', '')
                    if mtype == 'observation':
                        last_observation = msg
                        # Silent - don't spam observations
                    elif mtype == 'pong':
                        print(f"  [pong]")
                    elif mtype == 'action_result':
                        ok = msg.get('success', False)
                        m = msg.get('message', '')
                        print(f"  [{'OK' if ok else 'FAIL'}] {m}")
                    elif mtype == 'action_complete':
                        m = msg.get('message', '')
                        print(f"  [complete] {m}")
                    elif mtype == 'dialogue':
                        npc = msg.get('npc', '?')
                        text = msg.get('text', '')
                        print(f"  [{npc}] {text}")
                    else:
                        print(f"  [{mtype}] {json.dumps(msg)[:200]}")
                except json.JSONDecodeError:
                    pass
        except socket.timeout:
            continue
        except Exception as e:
            print(f"\n[Reader error: {e}]")
            break

def show_status():
    if not last_observation:
        print("  No observation yet.")
        return
    p = last_observation.get('player', {})
    print(f"  Cell: {p.get('cell', '?')}")
    h = p.get('health', {})
    m = p.get('magicka', {})
    f = p.get('fatigue', {})
    print(f"  HP: {h.get('current',0):.0f}/{h.get('base',0):.0f}  MP: {m.get('current',0):.0f}/{m.get('base',0):.0f}  FP: {f.get('current',0):.0f}/{f.get('base',0):.0f}")
    print(f"  Level: {p.get('level', '?')}")

def show_nearby():
    if not last_observation:
        print("  No observation yet.")
        return
    nearby = last_observation.get('nearby', {})
    for cat in ['actors', 'doors', 'items', 'containers', 'activators']:
        objs = nearby.get(cat, [])
        if objs:
            print(f"  {cat.upper()}:")
            for o in objs[:10]:
                name = o.get('name', o.get('recordId', '?'))
                dist = o.get('distance', '?')
                extra = ""
                if cat == 'actors':
                    if o.get('hostile'): extra = " [HOSTILE]"
                    if o.get('dead'): extra = " [DEAD]"
                    hp = o.get('health', {})
                    extra += f" HP:{hp.get('current',0):.0f}/{hp.get('base',0):.0f}"
                if name:
                    print(f"    {name} ({dist}m){extra}")

def show_inventory():
    if not last_observation:
        print("  No observation yet.")
        return
    inv = last_observation.get('inventory', [])
    print(f"  Inventory ({len(inv)} items):")
    for item in inv:
        name = item.get('name', item.get('recordId', '?'))
        count = item.get('count', 1)
        c = f" x{count}" if count > 1 else ""
        print(f"    {name}{c}")

def show_quests():
    if not last_observation:
        print("  No observation yet.")
        return
    quests = last_observation.get('quests', {})
    texts = last_observation.get('journalTexts', [])
    if texts:
        print("  Journal:")
        for t in texts:
            print(f"    [{t.get('questId','')}] {t.get('text','')}")
    if isinstance(quests, list):
        for q in quests:
            print(f"    {q.get('id','?')} stage={q.get('stage','?')} {'[done]' if q.get('finished') else ''}")

HELP = """
Commands:
  look        - Show nearby objects/NPCs
  status      - Show player health/stats
  inv         - Show inventory
  quests      - Show quest journal
  w/a/s/d     - Move forward/left/back/right (1 sec)
  run <dir> <sec> - Move in direction for duration
  turn <angle>    - Turn (radians, + = right, - = left)
  activate <name> - Interact with object/NPC
  talk <name>     - Talk to NPC
  topic <topic>   - Select dialogue topic
  equip <item>    - Equip an item
  attack          - Attack
  jump            - Jump
  sneak           - Toggle sneak
  nav <place>     - Navigate to a location
  ping            - Ping the bridge
  raw <json>      - Send raw JSON
  quit            - Exit
"""

def main():
    global sock
    print(f"Connecting to 127.0.0.1:{PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(("127.0.0.1", PORT))
    except ConnectionRefusedError:
        print("Connection refused. Is OpenMW running?")
        return
    print("Connected!")

    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()

    # Wait for first observation
    time.sleep(1.0)

    print(HELP)

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue

        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        try:
            if cmd == 'quit' or cmd == 'exit':
                break
            elif cmd == 'help':
                print(HELP)
            elif cmd == 'look':
                show_nearby()
            elif cmd == 'status':
                show_status()
            elif cmd == 'inv':
                show_inventory()
            elif cmd == 'quests':
                show_quests()
            elif cmd == 'ping':
                send({"type": "ping", "id": next_id()})
            elif cmd == 'w':
                action("move", {"direction": "forward", "duration": 1.0, "run": True})
            elif cmd == 'a':
                action("move", {"direction": "left", "duration": 1.0, "run": True})
            elif cmd == 's':
                action("move", {"direction": "backward", "duration": 1.0, "run": True})
            elif cmd == 'd':
                action("move", {"direction": "right", "duration": 1.0, "run": True})
            elif cmd == 'run':
                rparts = arg.split()
                direction = rparts[0] if rparts else "forward"
                duration = float(rparts[1]) if len(rparts) > 1 else 1.0
                action("move", {"direction": direction, "duration": duration, "run": True})
            elif cmd == 'turn':
                angle = float(arg) if arg else 1.57
                action("turn", {"angle": angle, "duration": 0.5})
            elif cmd == 'activate':
                action("activate", {"target": arg})
            elif cmd == 'talk':
                action("activate", {"target": arg})
            elif cmd == 'topic':
                action("select_topic", {"topic": arg})
            elif cmd == 'equip':
                action("equip", {"item": arg})
            elif cmd == 'attack':
                dur = float(arg) if arg else 1.0
                action("attack", {"duration": dur})
            elif cmd == 'jump':
                action("jump", {})
            elif cmd == 'sneak':
                action("sneak", {"enable": True})
            elif cmd == 'nav':
                action("navigate_to", {"destination": arg})
            elif cmd == 'raw':
                send(json.loads(arg))
            else:
                print(f"  Unknown command: {cmd}. Type 'help' for commands.")
        except Exception as e:
            print(f"  Error: {e}")

    sock.close()
    print("Disconnected.")

if __name__ == "__main__":
    main()
