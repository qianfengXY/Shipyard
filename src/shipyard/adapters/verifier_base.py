"""Verifier adapter protocol."""

from __future__ import annotations

from typing import Protocol


class VerifierAdapter(Protocol):
    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        builder_result: dict,
        state: dict,
    ) -> dict:
        ...
