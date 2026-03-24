"""Persistent knowledge base for cross-session learning.

Stores notes in categorized JSON files under bridge_server/knowledge/.
Claude can save discoveries, NPC info, quest strategies, locations, etc.
and recall them in future sessions.
"""

import json
import os
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Default categories
CATEGORIES = [
    "locations",      # Places discovered, directions, landmarks
    "npcs",           # NPC info: who they are, where they are, what they sell/train
    "quests",         # Quest walkthrough notes, objectives, what to do next
    "strategies",     # Combat tactics, useful spells, game mechanics learned
    "discoveries",    # General notes, lore, interesting findings
    "inventory",      # Notable items found, where to buy/sell things
]


class KnowledgeBase:
    """File-backed knowledge store with categorized entries."""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            # Default to bridge_server/knowledge/ relative to this file
            base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._load_all()

    def _file_path(self, category: str) -> Path:
        return self.base_dir / f"{category}.json"

    def _load_all(self):
        """Load all category files into cache."""
        for category in CATEGORIES:
            path = self._file_path(category)
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self._cache[category] = json.load(f)
                    logger.info(f"Loaded {len(self._cache[category])} entries from {category}")
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to load {category}: {e}")
                    self._cache[category] = {}
            else:
                self._cache[category] = {}

    def _save_category(self, category: str):
        """Save a category to disk."""
        path = self._file_path(category)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._cache.get(category, {}), f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save {category}: {e}")

    def save(self, category: str, key: str, value: str) -> str:
        """Save a note. Returns confirmation message."""
        if category not in CATEGORIES:
            # Auto-create new categories
            CATEGORIES.append(category)
        if category not in self._cache:
            self._cache[category] = {}
        self._cache[category][key] = value
        self._save_category(category)
        logger.info(f"Saved [{category}] {key}")
        return f"Saved to {category}/{key}"

    def load(self, category: str, key: str) -> Optional[str]:
        """Load a specific note. Returns None if not found."""
        return self._cache.get(category, {}).get(key)

    def delete(self, category: str, key: str) -> str:
        """Delete a note. Returns confirmation message."""
        if category in self._cache and key in self._cache[category]:
            del self._cache[category][key]
            self._save_category(category)
            return f"Deleted {category}/{key}"
        return f"Not found: {category}/{key}"

    def list_keys(self, category: str) -> list[str]:
        """List all keys in a category."""
        return list(self._cache.get(category, {}).keys())

    def list_categories(self) -> list[str]:
        """List all categories with entry counts."""
        return [f"{cat} ({len(self._cache.get(cat, {}))} entries)" for cat in CATEGORIES if self._cache.get(cat)]

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search across all categories. Returns matching entries."""
        query_lower = query.lower()
        results = []
        for category, entries in self._cache.items():
            for key, value in entries.items():
                # Match against key and value
                score = 0
                if query_lower in key.lower():
                    score += 2
                if query_lower in str(value).lower():
                    score += 1
                if score > 0:
                    results.append({
                        "category": category,
                        "key": key,
                        "value": value if len(str(value)) <= 300 else str(value)[:300] + "...",
                        "score": score,
                    })
        # Sort by relevance
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:max_results]

    def get_summary(self, max_per_category: int = 5) -> str:
        """Generate a summary of all knowledge for injection into system prompt."""
        parts = []
        total = 0
        for category in CATEGORIES:
            entries = self._cache.get(category, {})
            if not entries:
                continue
            total += len(entries)
            parts.append(f"\n### {category.capitalize()} ({len(entries)} notes)")
            # Show first N entries
            for i, (key, value) in enumerate(entries.items()):
                if i >= max_per_category:
                    parts.append(f"  ... and {len(entries) - max_per_category} more")
                    break
                val_preview = str(value)[:150]
                parts.append(f"  - **{key}**: {val_preview}")

        if not parts:
            return "No prior knowledge saved."

        header = f"## Prior Knowledge ({total} total notes)\n"
        return header + "\n".join(parts)

    def get_all(self, category: str) -> dict:
        """Get all entries in a category."""
        return dict(self._cache.get(category, {}))
