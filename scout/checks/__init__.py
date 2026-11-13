"""Auto-discovers every Check subclass in this package, sorted by `order`."""
from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import Check, SkipCheck  # noqa: F401


def discover() -> list:
    found = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_") or mod.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Check) and obj is not Check and obj.__module__ == module.__name__:
                found.append(obj)
    return sorted(found, key=lambda c: c.order)
