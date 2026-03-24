"""Game state tracker for the OpenMW bridge."""

from typing import Optional


STANCE_NAMES = {0: "None", 1: "Weapon", 2: "Spell"}


class GameState:
    """Tracks game state from observation messages."""

    def __init__(self):
        self.current: Optional[dict] = None
        self.previous: Optional[dict] = None
        self.observation_count = 0

    def update(self, observation: dict):
        """Ingest a new observation."""
        if observation.get("type") != "observation":
            return
        self.previous = self.current
        self.current = observation
        self.observation_count += 1

    @property
    def player(self) -> Optional[dict]:
        return self.current.get("player") if self.current else None

    @property
    def nearby(self) -> Optional[dict]:
        return self.current.get("nearby") if self.current else None

    @property
    def inventory(self) -> Optional[list]:
        return self.current.get("inventory") if self.current else None

    @property
    def equipment(self) -> Optional[dict]:
        return self.current.get("equipment") if self.current else None

    @property
    def quests(self) -> Optional[list]:
        return self.current.get("quests") if self.current else None

    @property
    def current_action(self) -> Optional[str]:
        return self.current.get("currentAction") if self.current else None

    def summarize(self) -> str:
        """Produce a human-readable summary of current game state for Claude."""
        if not self.current:
            return "No observation data yet."

        parts = []
        p = self.player
        if p:
            stance = STANCE_NAMES.get(p.get("stance", 0), "Unknown")
            cell = p.get("cell", "Unknown")
            pos = p.get("position", {})
            parts.append(f"=== Player ===")
            parts.append(f"Location: {cell} ({pos.get('x', 0):.0f}, {pos.get('y', 0):.0f}, {pos.get('z', 0):.0f})")
            parts.append(f"Level: {p.get('level', '?')} | Stance: {stance}")

            for stat in ("health", "magicka", "fatigue"):
                s = p.get(stat, {})
                parts.append(f"  {stat.capitalize()}: {s.get('current', 0):.0f}/{s.get('base', 0):.0f}")

            flags = []
            if p.get("swimming"):
                flags.append("swimming")
            if not p.get("onGround"):
                flags.append("airborne")
            action = self.current_action
            if action:
                flags.append(f"action={action}")
            if flags:
                parts.append(f"  State: {', '.join(flags)}")

        # Equipment
        eqp = self.equipment
        if eqp:
            items = [f"{v.get('name', v.get('recordId', '?'))}" for v in eqp.values() if v.get("name")]
            if items:
                parts.append(f"\n=== Equipment ===")
                parts.append(", ".join(items))

        # Nearby
        nb = self.nearby
        if nb:
            parts.append(f"\n=== Nearby ===")
            # Actors
            actors = nb.get("actors", [])
            if actors:
                actor_strs = []
                for a in actors:
                    name = a.get("name", a.get("recordId", "?"))
                    dist = a.get("distance", "?")
                    tags = []
                    if a.get("hostile"):
                        tags.append("HOSTILE")
                    if a.get("dead"):
                        tags.append("dead")
                    hp = a.get("health")
                    if hp and not a.get("dead"):
                        tags.append(f"HP:{hp.get('current', 0):.0f}/{hp.get('base', 0):.0f}")
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    actor_strs.append(f"  {name} ({dist}m){tag_str}")
                parts.append("NPCs/Creatures:")
                parts.extend(actor_strs)

            doors = nb.get("doors", [])
            if doors:
                parts.append("Doors:")
                for d in doors:
                    parts.append(f"  {d.get('name', d.get('recordId', '?'))} ({d.get('distance', '?')}m)")

            items = nb.get("items", [])
            if items:
                parts.append("Items:")
                for i in items:
                    name = i.get("name", i.get("recordId", "?"))
                    count = i.get("count", 1)
                    count_str = f" x{count}" if count > 1 else ""
                    parts.append(f"  {name}{count_str} ({i.get('distance', '?')}m)")

            containers = nb.get("containers", [])
            if containers:
                parts.append("Containers:")
                for c in containers:
                    parts.append(f"  {c.get('name', c.get('recordId', '?'))} ({c.get('distance', '?')}m)")

            activators = nb.get("activators", [])
            if activators:
                parts.append("Activators:")
                for a in activators:
                    parts.append(f"  {a.get('name', a.get('recordId', '?'))} ({a.get('distance', '?')}m)")

        # Inventory summary (compact)
        inv = self.inventory
        if inv:
            parts.append(f"\n=== Inventory ({len(inv)} items) ===")
            # Show first 15 items
            for item in inv[:15]:
                name = item.get("name", item.get("recordId", "?"))
                count = item.get("count", 1)
                count_str = f" x{count}" if count > 1 else ""
                parts.append(f"  {name}{count_str}")
            if len(inv) > 15:
                parts.append(f"  ... and {len(inv) - 15} more")

        # Quests
        quests = self.quests
        if quests:
            active = [q for q in quests if not q.get("finished")]
            if active:
                parts.append(f"\n=== Active Quests ({len(active)}) ===")
                for q in active[:10]:
                    parts.append(f"  {q.get('id', '?')} (stage {q.get('stage', '?')})")

        # Dialogue state
        if self.current:
            dlg = self.current.get("dialogue")
            if dlg and dlg.get("active"):
                parts.append(f"\n=== In Dialogue with {dlg.get('npc', '?')} ===")
                history = dlg.get("history", [])
                for h in history:
                    parts.append(f"  [{h.get('dialogueType', '?')}] {h.get('text', '')[:200]}")
                topics = dlg.get("topics", [])
                if topics:
                    parts.append(f"Available topics: {', '.join(topics[:20])}")

        # Journal text
        if self.current:
            jt = self.current.get("journalTexts", [])
            if jt:
                parts.append(f"\n=== Recent Journal ({len(jt)} entries) ===")
                for entry in jt[-5:]:
                    parts.append(f"  [{entry.get('questId', '?')}] {entry.get('text', '')[:150]}")

        return "\n".join(parts)

    def summarize_changes(self) -> str:
        """Summarize what changed since the previous observation."""
        if not self.previous or not self.current:
            return ""

        changes = []
        prev_p = self.previous.get("player", {})
        curr_p = self.current.get("player", {})

        # Cell change
        if prev_p.get("cell") != curr_p.get("cell"):
            changes.append(f"Moved to: {curr_p.get('cell')}")

        # Health changes
        for stat in ("health", "magicka", "fatigue"):
            prev_val = prev_p.get(stat, {}).get("current", 0)
            curr_val = curr_p.get(stat, {}).get("current", 0)
            diff = curr_val - prev_val
            if abs(diff) > 1:
                direction = "gained" if diff > 0 else "lost"
                changes.append(f"{stat.capitalize()}: {direction} {abs(diff):.0f} (now {curr_val:.0f})")

        # Stance change
        if prev_p.get("stance") != curr_p.get("stance"):
            changes.append(f"Stance changed to: {STANCE_NAMES.get(curr_p.get('stance', 0), '?')}")

        # New nearby actors
        prev_actors = {a.get("id") for a in self.previous.get("nearby", {}).get("actors", [])}
        curr_actors = self.current.get("nearby", {}).get("actors", [])
        new_actors = [a for a in curr_actors if a.get("id") not in prev_actors]
        for a in new_actors:
            tags = []
            if a.get("hostile"):
                tags.append("HOSTILE")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            changes.append(f"New nearby: {a.get('name', '?')}{tag_str}")

        # Quest stage changes
        prev_quests = {q.get("id"): q.get("stage") for q in self.previous.get("quests", [])}
        for q in self.current.get("quests", []):
            qid = q.get("id")
            curr_stage = q.get("stage")
            prev_stage = prev_quests.get(qid)
            if prev_stage is not None and prev_stage != curr_stage:
                changes.append(f"Quest '{qid}' advanced to stage {curr_stage}")
            elif prev_stage is None:
                changes.append(f"New quest: '{qid}' (stage {curr_stage})")

        return "\n".join(changes)
