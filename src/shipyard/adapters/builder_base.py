"""Builder adapter protocol."""

from __future__ import annotations

from typing import Protocol


class BuilderAdapter(Protocol):
    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        prior_review: dict | None,
        state: dict,
    ) -> dict:
        ...
