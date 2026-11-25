"""Dependency health for /api/exec/health and the 503 gate on starting or approving work.

Each dependency is reported on its own: the robot gateway (connected, and whether a real round trip
has been seen), each planner (for the LLM: is the executive role bound to a provider, and what did
the last probe say), and, for the Synapse gateway, the bridge and rosbridge links behind it.
Cancel and stop are never gated on any of this.
"""

from __future__ import annotations

from typing import Any, Optional

from .contracts import normalize_planner_kind
from .executive import Executive
from .planner.llm import LLMPlanner

_PROBE_FIELDS = ("ok", "provider_id", "model", "checked_at", "latency_ms", "error")


def planner_health(ex: Executive, kind: str) -> dict[str, Any]:
    kind = normalize_planner_kind(kind)
    p = ex.planners.get(kind)
    if p is None:
        return {"kind": kind, "ready": False, "reason": f"planner {kind} is not installed"}
    if not isinstance(p, LLMPlanner):
        return {"kind": kind, "ready": True, "reason": None}  # deterministic, in process
    st = p.status()
    probe = st["probe"]
    out: dict[str, Any] = {
        "kind": kind, "role": st["role"], "bound": st["configured"], "provider": st["provider"],
        "model": st["model"], "fallbacks": st["fallbacks"], "live_enabled": st["live_enabled"],
        "last_probe": None if probe is None else {k: probe.get(k) for k in _PROBE_FIELDS},
        "ready": True, "reason": None,
    }
    if not st["configured"]:
        problem = st["problems"][-1] if st["problems"] else "no provider"
        out.update(ready=False, reason=f"{st['role']} role is not bound to a provider: {problem}")
        return out
    probed_this_row = bool(probe and probe.get("provider_id") == st["provider"] and probe.get("model") == st["model"]
                           and probe.get("base_url") == st["endpoint"])
    if probed_this_row and not probe.get("ok") and not st["fallbacks"]:
        out.update(ready=False, reason=f"provider {st['provider']} failed its last probe and has no fallback: "
                                       f"{probe.get('error') or 'probe failed'}")
    return out


def exec_health(ex: Executive) -> dict[str, Any]:
    gi = ex.gateway.info()
    links = ex.gateway.transport_health()
    planners = {k: planner_health(ex, k) for k in ex.planners}
    default = planners.get(ex.default_planner) or planner_health(ex, ex.default_planner)
    missing = []
    if not gi.connected:
        missing.append("gateway")
    for name in ("bridge", "rosbridge"):
        if links[name].get("used") and not links[name].get("connected"):
            missing.append(name)
    if not default["ready"]:
        missing.append(f"planner:{default['kind']}")
    return {
        "ok": True, "service": "hive-executive", "run_id": ex.run_id,
        "ready": not missing, "missing": missing,
        "gateway": {"kind": gi.kind, "label": gi.label, "connected": gi.connected,
                    "verified_round_trip": gi.verified_transport, "detail": gi.detail,
                    "simulated": gi.simulated, "fleet": gi.fleet},
        "default_planner": default["kind"], "planner": default, "planners": planners,
        "bridge": links["bridge"], "rosbridge": links["rosbridge"],
    }


def not_ready_reason(ex: Executive, planner_kind: Optional[str]) -> Optional[tuple[str, list[str]]]:
    """Why new work cannot start or be approved, naming each missing piece; None when ready."""
    reasons, missing = [], []
    gi = ex.gateway.info()
    if not gi.connected:
        missing.append("gateway")
        reasons.append(f"robot gateway ({gi.label}) is not connected: {gi.detail}")
    ph = planner_health(ex, planner_kind or ex.default_planner)
    if not ph["ready"]:
        missing.append(f"planner:{ph['kind']}")
        reasons.append(f"planner {ph['kind']} is down: {ph['reason']}")
    return ("; ".join(reasons), missing) if missing else None
