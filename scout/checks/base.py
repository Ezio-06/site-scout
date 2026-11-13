"""The plug-in contract. A check is one file in scout/checks/ containing one
subclass of Check. It is discovered automatically - no registration needed.

    class MyCheck(Check):
        name = "my_check"          # short id, used by --only / --skip
        title = "What it answers"  # shown on the scout card
        order = 55                 # lower runs earlier (see existing checks)

        def run(self, ctx):
            ...                    # use ctx.get(), ctx.render(), ctx.facts
            self.note("plain-language finding")
            return {"key": "value"}    # machine-readable findings
"""
from __future__ import annotations


class SkipCheck(Exception):
    """Raise inside run() to mark the check as skipped with a reason."""


class Check:
    name = "base"
    title = "Base check"
    order = 100

    def __init__(self):
        self.notes: list = []      # plain-language findings for the card
        self.warnings: list = []   # things that need attention

    def note(self, text: str):
        self.notes.append(text)

    def warn(self, text: str):
        self.warnings.append(text)

    def should_run(self, ctx) -> str | None:
        """Return a reason to skip, or None to run."""
        return None

    def run(self, ctx) -> dict:
        raise NotImplementedError
