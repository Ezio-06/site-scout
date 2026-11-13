"""Rebuild scout/data/guard_signatures.json from wafw00f's public plugin list.

    git clone --depth 1 https://github.com/EnableSecurity/wafw00f.git
    python tools/build_signatures.py wafw00f/wafw00f/plugins

Reads the plugins as text (AST) - nothing from wafw00f is executed or imported.
wafw00f is BSD-3-licensed; see scout/data/WAFW00F_LICENSE.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "scout" / "data" / "guard_signatures.json"


def cond(call: ast.Call, plugin: str):
    """self.matchX(...) -> condition dict, or None if unsupported."""
    if not (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self" and call.args):
        return None
    kind = call.func.attr
    try:
        arg = ast.literal_eval(call.args[0])
    except Exception:
        return None
    if kind == "matchHeader" and isinstance(arg, tuple) and len(arg) == 2:
        return {"t": "header", "name": arg[0], "re": arg[1]}
    if kind == "matchCookie":
        return {"t": "header", "name": "Set-Cookie", "re": arg}
    if kind == "matchContent":
        return {"t": "body", "re": arg}
    if kind == "matchStatus":
        return {"t": "status", "code": int(arg)}
    return None   # matchReason etc. - not available to the scout


def rules_of(node, env, plugin):
    """Return a list of AND-groups (each a list of conditions) for an if-test."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("all", "any"):
        items = node.args[0] if node.args else None
        if isinstance(items, ast.Name):
            items = env.get(items.id)
        if not isinstance(items, (ast.List, ast.Tuple)):
            return []
        groups = [rules_of(e, env, plugin) for e in items.elts]
        if node.func.id == "any":
            return [g for gs in groups for g in gs]
        out = [[]]
        for gs in groups:
            if not gs:
                return []
            out = [a + b for a in out for b in gs]
        return out
    if isinstance(node, ast.BoolOp):
        groups = [rules_of(v, env, plugin) for v in node.values]
        if isinstance(node.op, ast.Or):
            return [g for gs in groups for g in gs]
        out = [[]]
        for gs in groups:
            if not gs:
                return []
            out = [a + b for a in out for b in gs]
        return out
    if isinstance(node, ast.Name) and node.id in env:
        return rules_of(env[node.id], env, plugin)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS:
        return FUNCS[node.func.id]          # helper like check_schema_01(self)
    if isinstance(node, ast.Call):
        c = cond(node, plugin)
        return [[c]] if c else []
    return []


FUNCS: dict = {}


def func_rules(fn: ast.FunctionDef, plugin: str) -> list:
    """Rules for one function. Two styles appear in wafw00f:
    'if X: return True' (any of them) and 'if not X: return False ... return True' (all of them)."""
    env, rules, required = {}, [], []
    for st in ast.walk(fn):
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            env[st.targets[0].id] = st.value
    for st in fn.body:
        if not isinstance(st, ast.If) or not st.body or not isinstance(st.body[0], ast.Return):
            continue
        ret = getattr(st.body[0].value, "value", None)
        if ret is True:
            rules.extend(rules_of(st.test, env, plugin))
        elif ret is False and isinstance(st.test, ast.UnaryOp) and isinstance(st.test.op, ast.Not):
            required.append(rules_of(st.test.operand, env, plugin))
    if required and all(required):
        group = [[]]
        for gs in required:
            group = [a + b for a in group for b in gs]
        rules.extend(group)
    return rules


def parse_plugin(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    name, rules = None, []
    FUNCS.clear()
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    for fn in fns:                      # helpers first, so is_waf can refer to them
        if fn.name != "is_waf":
            FUNCS[fn.name] = func_rules(fn, path.stem)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "NAME" for t in node.targets):
            name = ast.literal_eval(node.value)
    for fn in fns:
        if fn.name == "is_waf":
            rules.extend(func_rules(fn, path.stem))
    good = []
    for r in rules:
        if not r or all(c["t"] == "status" for c in r):
            continue            # a status code alone says nothing
        ok = True
        for c in r:
            if "re" in c:
                try:
                    re.compile(c["re"])
                except re.error:
                    ok = False
        if ok and r not in good:
            good.append(r)
    return name, good


def main(plugin_dir: str):
    out = {}
    for p in sorted(Path(plugin_dir).glob("*.py")):
        if p.name == "__init__.py":
            continue
        name, rules = parse_plugin(p)
        if name and rules:
            out[p.stem] = {"name": name, "rules": rules}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "wafw00f plugins (BSD-3, EnableSecurity)",
                               "guards": out}, indent=1, ensure_ascii=False), encoding="utf-8")
    n = sum(len(v["rules"]) for v in out.values())
    print(f"{len(out)} guards, {n} rules -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "wafw00f/wafw00f/plugins")
