"""Find the 'list of items' inside any JSON blob - the part a scraper wants."""
from __future__ import annotations

from collections import deque

MAX_NODES = 50_000   # stop walking giant blobs early (memory/time guard)


def item_arrays(obj, min_len: int = 3, limit: int = 5) -> list:
    """Return the biggest arrays of objects found in `obj`, as
    [{path, length, keys}] sorted by length. Breadth-first, capped."""
    found = []
    queue = deque([("$", obj)])
    seen = 0
    while queue and seen < MAX_NODES:
        path, node = queue.popleft()
        seen += 1
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    queue.append((f"{path}.{k}", v))
        elif isinstance(node, list):
            dicts = [x for x in node[:200] if isinstance(x, dict)]
            if len(node) >= min_len and len(dicts) >= max(min_len, len(node[:200]) * 0.8):
                keys = {}
                for d in dicts[:20]:
                    for k in d:
                        keys[k] = keys.get(k, 0) + 1
                common = [k for k, c in sorted(keys.items(), key=lambda kv: -kv[1])][:12]
                found.append({"path": path, "length": len(node), "keys": common})
            for i, v in enumerate(node[:3]):  # look inside a few elements only
                if isinstance(v, (dict, list)):
                    queue.append((f"{path}[{i}]", v))
    found.sort(key=lambda f: -f["length"])
    return found[:limit]


def top_keys(obj, n: int = 12) -> list:
    if isinstance(obj, dict):
        return list(obj.keys())[:n]
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        return list(obj[0].keys())[:n]
    return []
