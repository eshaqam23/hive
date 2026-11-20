"""scripts/prompt_eval.py: the case checks, the table and the exit code, against scripted
planners. No network: the LLM path runs on httpx.MockTransport and a temp HIVE_PROVIDERS_FILE.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from hive_exec.context import build_context
from hive_exec.planner.base import PlannerOutput
from hive_exec.planner.baseline import BaselinePlanner
from hive_exec.world import ObservedWorld, initial_robot_states

from .llm_mock import ScriptedQwen, mock_row, save_chain

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prompt_eval.py"
KEY = "gsk_PROMPTEVAL_never_real_0123456789abcd"


def load():
    spec = importlib.util.spec_from_file_location("prompt_eval", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pe = load()


async def good_outputs() -> list[str]:
    """What the deterministic baseline proposes for each case: a known-good script."""
    out = []
    for case in pe.CASES:
        ctx = build_context(phase="initial", command=case["command"], robots=initial_robot_states(),
                            world=ObservedWorld())
        out.append((await BaselinePlanner().propose(ctx)).proposal.model_dump_json())
    return out


class WrongPlanner(BaselinePlanner):
    """Always answers 'reject': every non-reject case must fail."""

    async def propose(self, ctx, repair_feedback=None, previous_output=None):
        out = await BaselinePlanner().propose(build_context(phase="initial", command="Fly the drone.",
                                                            robots=initial_robot_states(), world=ObservedWorld()))
        return PlannerOutput(out.proposal, out.record)


def test_cases_cover_the_ticket_commands():
    commands = [c["command"] for c in pe.CASES]
    assert len(commands) == 10 and "Find a red box in the feild" in commands
    assert {c["disposition"] for c in pe.CASES} == {"plan", "clarify", "answer", "reject"}


async def test_baseline_passes_every_case():
    rows = await pe.evaluate(BaselinePlanner(), pe.CASES, runs=1)
    assert len(rows) == 10 and all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]


async def test_wrong_planner_fails_with_reasons_and_runs_repeat():
    rows = await pe.evaluate(WrongPlanner(), pe.CASES, runs=2)
    assert len(rows) == 20 and {r["run"] for r in rows} == {1, 2}
    failed = [r for r in rows if not r["ok"]]
    assert len(failed) == 18 and all("expected plan" in r["note"] or "expected clarify" in r["note"]
                                     or "expected answer" in r["note"] for r in failed)
    text = pe.table(rows)
    assert "FAIL" in text and "2/20 passed" in text


@pytest.mark.parametrize("proposal,needle", [
    ({"disposition": "plan", "scope": "full", "mission_goals": [
        {"kind": "object_located", "object_query": {"label": "red_block"}, "area_id": "work_area"}],
      "tasks": [{"action": {"kind": "scan_route", "route_id": "pickup_corridor"}}]}, "scan_route"),
    ({"disposition": "plan", "scope": "full", "mission_goals": [
        {"kind": "object_located", "object_query": {"label": "red_block"}, "area_id": "work_area"}],
      "tasks": [{"action": {"kind": "pick_object"}, "preferred_robot_id": "gripper_01"}]}, "gripper"),
    ({"disposition": "plan", "scope": "full", "mission_goals": [
        {"kind": "object_located", "object_query": {"label": "blue_block"}, "area_id": "work_area"}],
      "tasks": []}, "object_located red_block"),
])
def test_check_names_the_broken_expectation(proposal, needle):
    case = pe.CASES[0]
    notes = pe.check(case, proposal, valid=True)
    assert notes and any(needle in n for n in notes)


def test_check_requires_a_valid_plan():
    assert any("validation" in n for n in pe.check(pe.CASES[5], {
        "disposition": "plan", "scope": "full", "mission_goals": [{"kind": "image_captured", "area_id": "work_area"}],
        "tasks": []}, valid=False))


def test_main_without_a_provider_file_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("HIVE_PROVIDERS_FILE", raising=False)
    assert pe.main([]) == 2
    assert "HIVE_PROVIDERS_FILE" in capsys.readouterr().out


async def test_main_live_path_passes_seeds_and_never_prints_the_key(capsys):
    mock = ScriptedQwen(await good_outputs() * 2)
    save_chain([mock_row()], keys={"scripted-mock": KEY})
    code = await pe.run([ "--runs", "2", "--seed", "7"], transport=httpx.MockTransport(mock.handler))
    out = capsys.readouterr().out
    assert code == 0 and "20/20 passed" in out and KEY not in out
    seeds = [b.get("seed") for b in mock.requests if "INPUT (data" in b["messages"][-1]["content"]
             and "Fly the drone to the roof" not in b["messages"][-1]["content"]]  # not the probe
    assert sorted(set(seeds)) == [7, 8] and len(seeds) == 20


async def test_main_live_path_exits_1_on_a_failure(capsys):
    outputs = await good_outputs()
    outputs[0] = outputs[7]  # the reject answer for the first find case
    mock = ScriptedQwen(outputs)
    save_chain([mock_row()], keys={"scripted-mock": KEY})
    code = await pe.run([], transport=httpx.MockTransport(mock.handler))
    out = capsys.readouterr().out
    assert code == 1 and "9/10 passed" in out and "FAIL" in out and KEY not in out
    assert json.loads(outputs[0])["disposition"] == "reject"
