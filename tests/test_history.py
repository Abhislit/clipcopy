#!/usr/bin/env python3
"""Unit tests for the clipboard history store. No GTK required."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from history import History  # noqa: E402

fails = []


def by_text(h, t):
    for i in h:
        if i.text == t:
            return i
    raise AssertionError("not found: %r" % t)


def check(name, cond, detail=""):
    if cond:
        print("PASS %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        fails.append(name)


# ordering
h = History()
h.add("first")
h.add("second")
h.add("third")
check("newest first", [i.text for i in h] == ["third", "second", "first"],
      [i.text for i in h])

# dedupe promotes to top
h.add("second")
check("dedupe moves to top",
      [i.text for i in h] == ["second", "third", "first"], [i.text for i in h])
check("dedupe does not duplicate", len(h) == 3, len(h))

# rejected input
h2 = History()
check("rejects empty", h2.add("") is None)
check("rejects whitespace", h2.add("   \n ") is None)
check("rejects None", h2.add(None) is None)
check("rejects oversize", h2.add("x" * (600 * 1024)) is None)

# cap
h4 = History(max_items=10)
for i in range(30):
    h4.add("item-%02d" % i)
check("caps at max", len(h4) == 10, len(h4))
check("keeps newest under cap", h4.items[0].text == "item-29", h4.items[0].text)

# pinned survive trimming
h5 = History(max_items=5)
h5.add("pinned-one")
by_text(h5, "pinned-one").pinned = True
for i in range(20):
    h5.add("filler-%02d" % i)
check("pinned survives trim", any(i.text == "pinned-one" for i in h5))
check("still capped+1", len(h5) == 6, len(h5))

# search
h6 = History()
h6.add("Hello World")
h6.add("goodbye world")
h6.add("unrelated")
h6.add("HELLO AGAIN")
res = [i.text for i in h6.search("hello")]
check("search case-insensitive", set(res) == {"Hello World", "HELLO AGAIN"}, res)
check("search returns all on empty", len(h6.search("")) == 4)
by_text(h6, "unrelated").pinned = True
check("pinned first in search", h6.search("")[0].text == "unrelated")

# remove
item = h6.items[0]
check("remove works", h6.remove(item) is True)
check("removed gone", item.text not in [i.text for i in h6])
check("remove missing returns False", h6.remove(item) is False)

# clear
h7 = History()
h7.add("keepme")
h7.add("dropme")
by_text(h7, "keepme").pinned = True
h7.clear(keep_pinned=True)
check("clear keeps pinned", [i.text for i in h7] == ["keepme"], [i.text for i in h7])
h7.add("another")
h7.clear(keep_pinned=False)
check("clear all", len(h7) == 0, len(h7))

# persistence
d = tempfile.mkdtemp()
path = os.path.join(d, "history.json")
h8 = History()
h8.add("persist one")
h8.add("persist two")
by_text(h8, "persist one").pinned = True
h8.save(path)
h9 = History()
check("load returns True", h9.load(path) is True)
check("round trip count", len(h9) == 2, len(h9))
check("round trip order",
      [i.text for i in h9] == ["persist one", "persist two"], [i.text for i in h9])
check("round trip pin", h9.items[0].pinned is True)
h9.add("persist two")
check("dedupe works after load", len(h9) == 2, len(h9))

# corrupt / missing input
bad = os.path.join(d, "bad.json")
with open(bad, "w") as fh:
    fh.write("{not valid json")
h10 = History()
check("corrupt file load False", h10.load(bad) is False)
check("corrupt leaves empty", len(h10) == 0)
check("missing file load False", h10.load(os.path.join(d, "nope.json")) is False)

weird = os.path.join(d, "weird.json")
with open(weird, "w") as fh:
    fh.write('[{"text":"ok"},{"nope":1},{"text":""}]')
h11 = History()
h11.load(weird)
check("skips malformed entries", [i.text for i in h11] == ["ok"], [i.text for i in h11])

# unicode
uni = os.path.join(d, "uni.json")
h12 = History()
h12.add("héllo → 世界 🎉")
h12.save(uni)
h13 = History()
h13.load(uni)
check("unicode round trip", h13.items[0].text == "héllo → 世界 🎉",
      h13.items[0].text)

print()
print("RESULT: %s (%d failures)" % ("PASS" if not fails else "FAIL", len(fails)))
sys.exit(1 if fails else 0)
