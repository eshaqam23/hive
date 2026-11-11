from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..context import PlanningContext
from ..contracts import PlanProposal, PlannerRecord


class PlannerError(RuntimeError):
    """Planner failed to produce a valid proposal. Always surfaced to the operator."""

    def __init__(self, message: str, record: Optional[PlannerRecord] = None):
        super().__init__(message)
        self.record = record


@dataclass
class PlannerOutput:
    proposal: PlanProposal
    record: PlannerRecord


class Planner(ABC):
    kind: str

    @abstractmethod
    async def propose(self, ctx: PlanningContext, repair_feedback: Optional[list[str]] = None) -> PlannerOutput: ...

    def status(self) -> dict:
        return {"kind": self.kind}
