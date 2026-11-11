"""Environment configuration. See ../../.env.example."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:  # optional .env support
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover
    pass


def _f(name: str, default: Optional[float]) -> Optional[float]:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return float(v)


@dataclass
class QwenSettings:
    base_url: str = ""
    model: str = "Qwen/Qwen3-8B"
    api_key: str = ""
    # Qwen3 published non-thinking recommendation is temperature 0.7, top_p 0.8, top_k 20.
    # QWEN_TEMPERATURE=0 is an experimental repeatability setting, not a determinism guarantee.
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: Optional[int] = 20
    seed: int = 1234
    max_tokens: int = 3072
    timeout_s: float = 60.0
    structured_mode: str = "structured_outputs"  # or guided_json for older vLLM

    @classmethod
    def from_env(cls) -> "QwenSettings":
        top_k = os.environ.get("QWEN_TOP_K", "20")
        return cls(
            base_url=os.environ.get("QWEN_BASE_URL", ""),
            model=os.environ.get("QWEN_MODEL", "Qwen/Qwen3-8B"),
            api_key=os.environ.get("QWEN_API_KEY", ""),
            temperature=_f("QWEN_TEMPERATURE", 0.7),
            top_p=_f("QWEN_TOP_P", 0.8),
            top_k=int(top_k) if top_k not in ("", "none", "None") else None,
            seed=int(os.environ.get("QWEN_SEED", "1234")),
            max_tokens=int(os.environ.get("QWEN_MAX_TOKENS", "3072")),
            timeout_s=_f("QWEN_TIMEOUT_S", 60.0),
            structured_mode=os.environ.get("QWEN_STRUCTURED_MODE", "structured_outputs"),
        )


@dataclass
class ExecSettings:
    host: str = "127.0.0.1"
    port: int = 8788
    gateway: str = "fake"  # fake | synapse | rclpy
    default_planner: str = "qwen"  # qwen | baseline (operator can choose per mission)
    sim_time_scale: float = 1.0
    bridge_url: str = "ws://127.0.0.1:8790"
    cors_origins: list[str] = field(default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"])
    qwen: QwenSettings = field(default_factory=QwenSettings)

    @classmethod
    def from_env(cls) -> "ExecSettings":
        return cls(
            host=os.environ.get("HIVE_EXEC_HOST", "127.0.0.1"),
            port=int(os.environ.get("HIVE_EXEC_PORT", "8788")),
            gateway=os.environ.get("HIVE_GATEWAY", "fake"),
            default_planner=os.environ.get("HIVE_DEFAULT_PLANNER", "qwen"),
            sim_time_scale=float(os.environ.get("HIVE_SIM_TIME_SCALE", "1.0")),
            bridge_url=os.environ.get("HIVE_BRIDGE_URL", "ws://127.0.0.1:8790"),
            qwen=QwenSettings.from_env(),
        )
