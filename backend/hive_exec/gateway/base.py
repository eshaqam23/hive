"""RobotGateway interface. ROS/Synapse imports stay inside concrete adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from ..contracts import TaskEnvelope, TaskEvent

EventSink = Callable[[TaskEvent], Awaitable[None]]


@dataclass
class SubmitReceipt:
    """A command receipt. It is NOT evidence of successful execution."""

    accepted_for_delivery: bool
    detail: str = ""
    # True when the command may have reached the robot but delivery was not confirmed
    # (e.g. timeout). The outcome is then uncertain and must not be retried blindly.
    delivery_unknown: bool = False


@dataclass
class GatewayInfo:
    kind: str  # fake | synapse | rclpy_fallback
    label: str  # user-facing badge text
    verified_transport: bool  # True only when a real adapter round trip has been observed
    connected: bool
    detail: str = ""


class RobotGateway(ABC):
    @abstractmethod
    def info(self) -> GatewayInfo: ...

    @abstractmethod
    async def start(self, sink: EventSink) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def submit(self, envelope: TaskEnvelope) -> SubmitReceipt: ...

    @abstractmethod
    async def cancel(self, run_id: str, attempt_id: str) -> None: ...

    @abstractmethod
    async def status(self) -> dict[str, dict[str, Any]]:
        """Per-robot dynamic status: location, battery, health, availability, carried object."""

    @abstractmethod
    async def active_attempts(self) -> list[str]: ...

    @abstractmethod
    async def reset(self, run_id: str, faults: Optional[list[str]] = None) -> None:
        """Coordinated reset: stop all fake work and start a fresh simulated world for run_id."""

    @abstractmethod
    async def inject_fault(self, fault: str) -> None: ...

    async def debug_truth(self) -> Optional[dict[str, Any]]:
        return None
