"""What Karen does while she answers, reported to the streaming endpoint so the chat can show it."""

from __future__ import annotations

import contextvars
from typing import Any, Callable

# Set by the streaming endpoint: receives each step (Jev's route, a site search, the pages read).
answer_steps: contextvars.ContextVar[Callable[[dict[str, Any]], None] | None] = contextvars.ContextVar("answer_steps", default=None)


def report_step(kind: str, **details: Any) -> None:
    report = answer_steps.get()
    if report:
        report({"kind": kind, **details})
