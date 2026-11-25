from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_provider_store(tmp_path, monkeypatch):
    """No test may read or write the operator's real ~/.hive/providers.yaml.

    The store exports keys into os.environ, so the environment is restored too.
    """
    saved = dict(os.environ)
    monkeypatch.setenv("HIVE_PROVIDERS_FILE", str(tmp_path / "providers.yaml"))
    # A8: the fake fleet's synthetic maps land under the test's tmp dir, never in the repo's runs/.
    monkeypatch.setenv("HIVE_SIM_MAP_ROOT", str(tmp_path / "sim_map_root"))
    yield
    for var in set(os.environ) - set(saved):
        os.environ.pop(var, None)
    for var, value in saved.items():
        if os.environ.get(var) != value:
            os.environ[var] = value


DEMO = ("Find the red block in the work area, check the pickup corridor, "
        "and bring the block to the drop-off area.")


async def wait_for(ex: Executive, statuses: set[str], timeout: float = 10.0) -> str:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if ex.mission and ex.mission.status in statuses:
            return ex.mission.status
        await asyncio.sleep(0.01)
    raise AssertionError(f"timeout waiting for {statuses}; mission is {ex.mission.status if ex.mission else None}")


async def approve_latest(ex: Executive):
    p = ex.mission.revisions[-1]
    return await ex.approve(p.revision, p.proposal_hash)


@pytest.fixture
async def make_exec():
    created: list[Executive] = []

    from hive_exec.executive import Executive
    from hive_exec.gateway.fake import FakeGateway
    from hive_exec.planner.baseline import BaselinePlanner

    async def factory(scenario: str = "unknown", faults: Optional[list[str]] = None,
                      planners: Optional[dict] = None, time_scale: float = 0.005) -> Executive:
        ex = Executive(FakeGateway(time_scale=time_scale), planners or {"baseline": BaselinePlanner()},
                       default_planner="baseline" if not planners else next(iter(planners)))
        await ex.gateway.start(ex.handle_event)
        await ex.reset(scenario, faults)
        created.append(ex)
        return ex

    yield factory
    for ex in created:
        await ex.gateway.stop()
