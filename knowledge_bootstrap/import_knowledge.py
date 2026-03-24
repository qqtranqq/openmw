#!/usr/bin/env python3
"""
Import parsed Morrowind guide data into the bridge's knowledge base.

Reads JSON files from knowledge_bootstrap/guides/ and loads them
into bridge_server/knowledge/ in the format expected by KnowledgeBase.

Usage:
    python import_knowledge.py [--guides-dir ./guides] [--dry-run]
"""

import argparse
import json
import os
import sys

# Add parent dir to path so we can import bridge_server modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'bridge_server'))
from knowledge import KnowledgeBase


def import_main_quest(kb: KnowledgeBase, data: list):
    """Import main quest walkthrough data."""
    count = 0
    for quest in data:
        name = quest.get("name", "Unknown Quest")
        content = {
            "quest_giver": quest.get("quest_giver", ""),
            "location": quest.get("quest_giver_location", ""),
            "prerequisites": quest.get("prerequisites", []),
            "steps": quest.get("steps", []),
            "rewards": quest.get("rewards", []),
            "next_quest": quest.get("next_quest", ""),
        }
        # Save key NPCs to npcs category too
        for npc in quest.get("key_npcs", []):
            if npc.get("name"):
                kb.save("npcs", npc["name"],
                    json.dumps({"location": npc.get("location", ""), "role": f"Main quest NPC ({name})"}))

        kb.save("quests", f"Main Quest: {name}", json.dumps(content))
        count += 1
    return count


def import_faction_quests(kb: KnowledgeBase, data: list):
    """Import faction quest data."""
    count = 0
    for faction_data in data:
        faction = faction_data.get("faction", "Unknown")
        for quest in faction_data.get("quests", []):
            name = quest.get("name", "Unknown")
            content = {
                "faction": faction,
                "quest_giver": quest.get("quest_giver", ""),
                "location": quest.get("location", ""),
                "rank_required": quest.get("rank_required", ""),
                "summary": quest.get("summary", ""),
                "reward": quest.get("reward", ""),
            }
            kb.save("quests", f"{faction}: {name}", json.dumps(content))
            count += 1
    return count


def import_locations(kb: KnowledgeBase, data: dict):
    """Import location and transportation data."""
    count = 0
    # Transport info
    transport = data.get("transport", {})
    if transport:
        kb.save("locations", "_transport_overview", json.dumps(transport))
        count += 1

    # Cities
    for city in data.get("cities", []):
        name = city.get("name", "Unknown")
        content = {
            "description": city.get("description", ""),
            "services": city.get("services", []),
            "travel_connections": city.get("travel_connections", []),
            "notes": city.get("notes", ""),
        }
        kb.save("locations", name, json.dumps(content))

        # Also save key NPCs from this city
        for npc in city.get("key_npcs", []):
            if npc.get("name"):
                kb.save("npcs", npc["name"],
                    json.dumps({"location": name, "role": npc.get("role", "")}))
        count += 1
    return count


def import_npcs(kb: KnowledgeBase, data: dict):
    """Import NPC, trainer, and merchant data."""
    count = 0

    for trainer in data.get("trainers", []):
        name = trainer.get("name", "Unknown")
        content = {
            "role": "Trainer",
            "skill": trainer.get("skill", ""),
            "max_level": trainer.get("max_level", ""),
            "location": trainer.get("location", ""),
            "building": trainer.get("building", ""),
        }
        kb.save("npcs", name, json.dumps(content))
        count += 1

    for merchant in data.get("spell_merchants", []):
        name = merchant.get("name", "Unknown")
        content = {
            "role": "Spell Merchant",
            "location": merchant.get("location", ""),
            "schools": merchant.get("schools", []),
        }
        # Merge with existing if trainer already saved
        existing = kb.load("npcs", name)
        if existing:
            try:
                existing_data = json.loads(existing)
                existing_data.update({"spell_merchant": True, "schools": merchant.get("schools", [])})
                kb.save("npcs", name, json.dumps(existing_data))
            except json.JSONDecodeError:
                kb.save("npcs", name, json.dumps(content))
        else:
            kb.save("npcs", name, json.dumps(content))
        count += 1

    for merchant in data.get("merchants", []):
        name = merchant.get("name", "Unknown")
        content = {
            "role": f"Merchant ({merchant.get('type', 'general')})",
            "location": merchant.get("location", ""),
            "gold": merchant.get("gold", ""),
        }
        kb.save("npcs", name, json.dumps(content))
        count += 1

    for npc in data.get("essential_npcs", []):
        name = npc.get("name", "Unknown")
        existing = kb.load("npcs", name)
        if existing:
            try:
                existing_data = json.loads(existing)
                existing_data["essential"] = True
                existing_data["essential_reason"] = npc.get("role", "")
                kb.save("npcs", name, json.dumps(existing_data))
            except json.JSONDecodeError:
                pass
        else:
            kb.save("npcs", name, json.dumps({
                "location": npc.get("location", ""),
                "role": npc.get("role", ""),
                "essential": True,
            }))
        count += 1

    return count


def import_items(kb: KnowledgeBase, data: dict):
    """Import item, artifact, and alchemy data."""
    count = 0

    for artifact in data.get("artifacts", []):
        name = artifact.get("name", "Unknown")
        content = {
            "type": artifact.get("type", ""),
            "enchantment": artifact.get("enchantment", ""),
            "location": artifact.get("location", ""),
            "quest": artifact.get("quest", ""),
        }
        kb.save("inventory", name, json.dumps(content))
        count += 1

    # Alchemy tips as strategies
    alchemy = data.get("alchemy_tips", {})
    if alchemy:
        kb.save("strategies", "Alchemy Mechanics", json.dumps(alchemy))
        count += 1

    # Ingredients (save as batch - too many for individual entries)
    ingredients = data.get("ingredients", [])
    if ingredients:
        # Group by first effect for easier lookup
        by_effect = {}
        for ing in ingredients:
            for key in ["effect1", "effect2", "effect3", "effect4"]:
                effect = ing.get(key, "")
                if effect and effect != "None" and effect != "":
                    if effect not in by_effect:
                        by_effect[effect] = []
                    by_effect[effect].append(ing.get("name", ""))
        kb.save("inventory", "_alchemy_ingredients_by_effect", json.dumps(by_effect))
        kb.save("inventory", "_alchemy_ingredients_full", json.dumps(ingredients[:100]))  # Cap size
        count += 2

    # Enchanting
    enchanting = data.get("enchanting", {})
    if enchanting:
        kb.save("strategies", "Enchanting Mechanics", json.dumps(enchanting))
        count += 1

    return count


def import_strategies(kb: KnowledgeBase, data: dict):
    """Import gameplay strategies and tips."""
    count = 0

    for key in ["combat", "character_creation", "leveling"]:
        section = data.get(key, {})
        if section:
            kb.save("strategies", key.replace("_", " ").title(), json.dumps(section))
            count += 1

    money = data.get("money_making", [])
    if money:
        kb.save("strategies", "Money Making", json.dumps(money))
        count += 1

    diseases = data.get("diseases", [])
    if diseases:
        kb.save("strategies", "Diseases and Cures", json.dumps(diseases))
        count += 1

    tips = data.get("general_tips", [])
    if tips:
        kb.save("strategies", "General Tips", json.dumps(tips))
        count += 1

    return count


IMPORTERS = {
    "main_quest.json": import_main_quest,
    "faction_quests.json": import_faction_quests,
    "locations.json": import_locations,
    "npcs.json": import_npcs,
    "items.json": import_items,
    "strategies.json": import_strategies,
}


def main():
    parser = argparse.ArgumentParser(description="Import Morrowind guide data into knowledge base")
    parser.add_argument("--guides-dir", default=os.path.join(os.path.dirname(__file__), "guides"),
                        help="Directory containing guide JSON files")
    parser.add_argument("--knowledge-dir", default=None,
                        help="Knowledge base directory (default: bridge_server/knowledge/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be imported without writing")
    args = parser.parse_args()

    if args.knowledge_dir:
        kb = KnowledgeBase(args.knowledge_dir)
    else:
        kb = KnowledgeBase()

    total = 0
    for filename, importer in IMPORTERS.items():
        filepath = os.path.join(args.guides_dir, filename)
        if not os.path.exists(filepath):
            print(f"  Skipping {filename} (not found)")
            continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"  Error reading {filename}: {e}")
            continue

        if args.dry_run:
            print(f"  Would import {filename}")
            continue

        count = importer(kb, data)
        print(f"  Imported {count} entries from {filename}")
        total += count

    print(f"\nTotal: {total} knowledge entries imported.")
    print(f"Knowledge base: {kb.base_dir}")


if __name__ == "__main__":
    main()
