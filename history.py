"""Clipboard history store: ordering, dedupe, persistence, filtering.

Kept free of GTK so it can be unit-tested on its own.
"""
import hashlib
import json
import os
import time

MAX_ITEMS = 300
MAX_ITEM_BYTES = 512 * 1024
MIN_ITEM_CHARS = 1


class Item:
    __slots__ = ("text", "added_at", "pinned", "logged")

    def __init__(self, text, added_at=None, pinned=False):
        self.text = text
        self.added_at = added_at if added_at is not None else time.time()
        self.pinned = pinned
        # Set once the UI has been told about this entry, so a repeated poll
        # of the same clipboard value does not re-announce it.
        self.logged = False

    @property
    def key(self):
        return hashlib.sha1(
            self.text.encode("utf-8", "replace")).hexdigest()

    def to_json(self):
        return {
            "text": self.text,
            "added_at": self.added_at,
            "pinned": self.pinned,
        }

    @staticmethod
    def from_json(d):
        return Item(d.get("text", ""), d.get("added_at"), d.get("pinned", False))


class History:
    def __init__(self, max_items=MAX_ITEMS):
        self.max_items = max_items
        self.items = []          # newest first
        self._index = {}         # key -> Item

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def add(self, text):
        """Insert text at the top. Returns the Item, or None if rejected."""
        if not text or not text.strip():
            return None
        if len(text.strip()) < MIN_ITEM_CHARS:
            return None
        if len(text.encode("utf-8", "replace")) > MAX_ITEM_BYTES:
            return None

        key = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()
        existing = self._index.get(key)
        if existing is not None:
            # Re-copying something already seen: move it to the top and
            # refresh its timestamp, but keep its pin.
            was_pinned = existing.pinned
            self.items.remove(existing)
            existing.added_at = time.time()
            existing.pinned = was_pinned
            self.items.insert(0, existing)
            return existing

        item = Item(text)
        self.items.insert(0, item)
        self._index[key] = item
        self._trim()
        return item

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
            self._index.pop(item.key, None)
            return True
        return False

    def clear(self, keep_pinned=True):
        if keep_pinned:
            keep = [i for i in self.items if i.pinned]
            self.items = keep
            self._index = {i.key: i for i in keep}
        else:
            self.items = []
            self._index = {}

    def set_pinned(self, item, pinned):
        item.pinned = pinned

    def _trim(self):
        if len(self.items) <= self.max_items:
            return
        kept, dropped = [], 0
        for item in self.items:
            if len(kept) < self.max_items or item.pinned:
                kept.append(item)
            else:
                dropped += 1
        self.items = kept
        self._index = {i.key: i for i in kept}

    def search(self, query):
        """Case-insensitive substring match, pinned first, then newest."""
        hits = self.items
        if query:
            q = query.lower()
            hits = [i for i in hits if q in i.text.lower()]
        return sorted(hits, key=lambda i: (not i.pinned, -i.added_at))

    # ---- persistence -------------------------------------------------

    def save(self, path):
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump([i.to_json() for i in self.items], fh)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return False
        self.items = []
        self._index = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            item = Item.from_json(entry)
            if not item.text:
                continue
            if item.key in self._index:
                continue
            self.items.append(item)
            self._index[item.key] = item
        # Restore recency order; the file is not guaranteed to be sorted.
        self.items.sort(key=lambda i: (not i.pinned, -i.added_at))
        self._index = {i.key: i for i in self.items}
        self._trim()
        return True
