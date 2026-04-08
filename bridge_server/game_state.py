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

            facing = p.get("facing", "")
            if facing:
                parts.append(f"  Facing: {facing}")
            bounty = p.get("bounty", 0)
            if bounty > 0:
                parts.append(f"  BOUNTY: {bounty} gold")
            enc = p.get("encumbrance", 0)
            cap = p.get("capacity", 0)
            if cap > 0:
                parts.append(f"  Carry: {enc}/{cap} ({cap - enc} free)")

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

        # Environment
        env = self.current.get("environment", {}) if self.current else {}
        if env:
            time_str = env.get("time", "")
            tod = env.get("timeOfDay", "")
            weather = env.get("weather", "")
            env_parts = []
            if time_str:
                env_parts.append(f"{time_str} ({tod})")
            if weather:
                env_parts.append(f"Weather: {weather}")
            if env_parts:
                parts.append(f"  {', '.join(env_parts)}")

        # Attributes
        attrs = self.current.get("attributes", {}) if self.current else {}
        if attrs:
            attr_strs = []
            for name in ["strength", "intelligence", "willpower", "agility", "speed", "endurance", "personality", "luck"]:
                a = attrs.get(name)
                if a:
                    attr_strs.append(f"{name[:3].capitalize()}:{a.get('modified', a.get('base', '?'))}")
            if attr_strs:
                parts.append(f"\n=== Attributes ===")
                parts.append("  " + ", ".join(attr_strs))

        # Skills (only show notable ones)
        skills = self.current.get("skills", {}) if self.current else {}
        if skills:
            skill_strs = []
            for name, s in sorted(skills.items(), key=lambda x: x[1].get("modified", x[1].get("base", 0)), reverse=True):
                val = s.get("modified", s.get("base", 0))
                if val > 15:
                    skill_strs.append(f"{name}:{val}")
            if skill_strs:
                parts.append(f"\n=== Skills ===")
                parts.append("  " + ", ".join(skill_strs))

        # Active Effects
        effects = self.current.get("activeEffects", []) if self.current else []
        if effects:
            parts.append(f"\n=== Active Effects ({len(effects)}) ===")
            for e in effects[:10]:
                eid = e.get("id", "?")
                mag = e.get("magnitude", 0)
                parts.append(f"  {eid} (magnitude: {mag:.0f})")

        # Spells
        RANGE_NAMES = {0: "Self", 1: "Touch", 2: "Ranged"}
        spells = self.current.get("spells", []) if self.current else []
        if spells:
            spell_lines = []
            selected = ""
            spell_list = spells if isinstance(spells, list) else []
            if isinstance(spells, dict):
                selected = spells.get("_selected", "")
                spell_list = [v for k, v in spells.items() if not k.startswith("_") and isinstance(v, dict)]

            for s in spell_list:
                if not isinstance(s, dict):
                    continue
                name = s.get("name", s.get("id", "?"))
                cost = s.get("cost", 0)
                stype = s.get("spellType", "spell")
                effects = s.get("effects", [])
                eff_parts = []
                for e in effects:
                    eff_name = e.get("name", "?")
                    rng = RANGE_NAMES.get(e.get("range", 0), "?")
                    mag_min = e.get("magnitudeMin", 0)
                    mag_max = e.get("magnitudeMax", 0)
                    mag_str = f" {mag_min}-{mag_max}" if mag_max > 0 else ""
                    eff_parts.append(f"{eff_name}{mag_str} ({rng})")
                eff_str = ", ".join(eff_parts) if eff_parts else ""
                can_use = s.get("canUse")
                power_str = ""
                if stype == "power":
                    power_str = " [POWER" + (", ready" if can_use else ", used today") + "]"
                spell_lines.append(f"  {name} (cost:{cost}) — {eff_str}{power_str}")

            if spell_lines:
                parts.append(f"\n=== Spells ({len(spell_lines)}) ===")
                for line in spell_lines[:15]:
                    parts.append(line)
                if selected:
                    parts.append(f"  Selected: {selected}")

        # Factions
        factions = self.current.get("factions", {}) if self.current else {}
        if factions:
            parts.append(f"\n=== Factions ===")
            for fid, fdata in factions.items():
                parts.append(f"  {fid}: rank {fdata.get('rank', 0)}, rep {fdata.get('reputation', 0)}")

        # Equipment
        eqp = self.equipment
        if eqp:
            item_strs = []
            for v in eqp.values():
                name = v.get('name', v.get('recordId', '?'))
                if not name:
                    continue
                ar = v.get('armorRating')
                chop = v.get('chopDamage')
                if ar:
                    item_strs.append(f"{name} (AR:{ar})")
                elif chop:
                    best_dmg = max(
                        v.get('chopDamage', [0, 0])[1],
                        v.get('slashDamage', [0, 0])[1],
                        v.get('thrustDamage', [0, 0])[1],
                    )
                    item_strs.append(f"{name} (dmg:1-{best_dmg})")
                else:
                    item_strs.append(name)
            if item_strs:
                parts.append(f"\n=== Equipment ===")
                parts.append(", ".join(item_strs))

        # Nearby
        # Note: distances are in game units. ~128 units = 1 meter. We convert for readability.
        def _fmt_dist(d):
            """Format distance in game units to human-readable."""
            if not isinstance(d, (int, float)):
                return str(d)
            meters = d / 128.0
            if meters < 5:
                return f"{meters:.1f}m (close)"
            elif meters < 20:
                return f"{meters:.0f}m (nearby)"
            elif meters < 100:
                return f"{meters:.0f}m"
            else:
                return f"{meters:.0f}m (far)"

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
                    direction = a.get("direction", "")
                    dir_str = f" {direction}" if direction else ""
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    # Services
                    services = a.get("services", [])
                    svc_str = f" services:{','.join(services)}" if services else ""
                    # Disposition
                    disp = a.get("disposition")
                    disp_str = f" disp:{disp}" if disp is not None else ""
                    # Faction
                    faction = a.get("faction", "")
                    fac_str = f" ({faction})" if faction else ""
                    actor_strs.append(f"  {name} ({_fmt_dist(dist)}{dir_str}){fac_str}{tag_str}{disp_str}{svc_str}")
                parts.append("NPCs/Creatures:")
                parts.extend(actor_strs)

            doors = nb.get("doors", [])
            if doors:
                parts.append("Doors:")
                for d in doors:
                    direction = d.get("direction", "")
                    dir_str = f" {direction}" if direction else ""
                    dest = d.get("destination", "")
                    name = d.get("name", d.get("recordId", "?"))
                    # Use destination as the display name if the door is generic
                    # Strip cell prefix (e.g. "Balmora, Caius Cosades' House" -> "Caius Cosades' House")
                    clean_dest = dest
                    if ", " in dest:
                        clean_dest = dest.split(", ", 1)[1]
                    if dest and name.lower() in ("door", ""):
                        display = clean_dest
                    elif dest:
                        display = f"{name} -> {clean_dest}"
                    else:
                        display = name
                    tags = []
                    if d.get("locked"):
                        lock_lvl = d.get("lockLevel", "?")
                        tags.append(f"LOCKED:{lock_lvl}")
                    if d.get("trapped"):
                        tags.append("TRAPPED")
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    parts.append(f"  {display} ({_fmt_dist(d.get('distance', '?'))}{dir_str}){tag_str}")

            items = nb.get("items", [])
            if items:
                parts.append("Items:")
                for i in items:
                    name = i.get("name", i.get("recordId", "?"))
                    count = i.get("count", 1)
                    count_str = f" x{count}" if count > 1 else ""
                    owned_str = " [OWNED]" if i.get("owned") else ""
                    parts.append(f"  {name}{count_str} ({_fmt_dist(i.get('distance', '?'))}){owned_str}")

            containers = nb.get("containers", [])
            if containers:
                parts.append("Containers:")
                for c in containers:
                    tags = []
                    if c.get("owned"):
                        tags.append("OWNED")
                    if c.get("locked"):
                        lock_lvl = c.get("lockLevel", "?")
                        tags.append(f"LOCKED:{lock_lvl}")
                    if c.get("trapped"):
                        tags.append("TRAPPED")
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    parts.append(f"  {c.get('name', c.get('recordId', '?'))} ({_fmt_dist(c.get('distance', '?'))}){tag_str}")

            activators = nb.get("activators", [])
            if activators:
                parts.append("Activators:")
                for a in activators:
                    parts.append(f"  {a.get('name', a.get('recordId', '?'))} ({_fmt_dist(a.get('distance', '?'))})")

        # Inventory summary (compact)
        inv = self.inventory
        if inv:
            parts.append(f"\n=== Inventory ({len(inv)} items) ===")
            # Show first 15 items
            for item in inv[:15]:
                name = item.get("name", item.get("recordId", "?"))
                count = item.get("count", 1)
                count_str = f" x{count}" if count > 1 else ""
                # Show potion effects inline
                effects = item.get("effects", [])
                ar = item.get("armorRating")
                chop = item.get("chopDamage")
                if effects:
                    eff_strs = [e.get("name", "?") for e in effects]
                    parts.append(f"  {name}{count_str} [{', '.join(eff_strs)}]")
                elif ar:
                    parts.append(f"  {name}{count_str} [AR:{ar}]")
                elif chop:
                    best_dmg = max(
                        item.get('chopDamage', [0, 0])[1],
                        item.get('slashDamage', [0, 0])[1],
                        item.get('thrustDamage', [0, 0])[1],
                    )
                    parts.append(f"  {name}{count_str} [dmg:1-{best_dmg}]")
                else:
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
                parts.append(f"\n=== WARNING: In Dialogue with {dlg.get('npc', '?')} ===")
                parts.append(f"You MUST call close_dialogue before you can move or do anything else!")
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
