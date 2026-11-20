"""scripts/prompt_eval.py: the case checks, the table and the exit code, against scripted
planners. No network: the LLM path runs on httpx.MockTransport and a temp HIVE_PROVIDERS_FILE.
"""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from hive_exec.context import build_context
from hive_exec.llm.providers import Sampling
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


V1 = "hive.plan-proposal.v1"
NOTHING = {"mission_goals": [], "stage_goals": [], "tasks": []}
#: Known-good answers for the unseen cases the deterministic baseline does not handle.
GOOD = {
    "photo-dropoff": {"schema_version": V1, "disposition": "reject", "summary": "No camera sees the drop-off zone.",
                      **NOTHING, "issues": ["no robot observes dropoff_zone"]},
    "toy-car-over-payload": {"schema_version": V1, "disposition": "reject", "summary": "The toy car is too heavy.",
                             **NOTHING, "issues": ["rc_car is over the gripper payload"]},
    "corridor-clear": {"schema_version": V1, "disposition": "plan", "scope": "full",
                       "summary": "Scan the pickup corridor.",
                       "mission_goals": [{"kind": "route_observed", "route_id": "pickup_corridor"}], "stage_goals": [],
                       "tasks": [{"task_id": "t1", "binding_id": "surveyor",
                                  "action": {"kind": "scan_route", "route_id": "pickup_corridor"},
                                  "depends_on": [], "preferred_robot_id": "lidar_01"}]},
    "duck-home": {"schema_version": V1, "disposition": "clarify", "summary": "'home' is not a named location.",
                  **NOTHING, "clarification_question": "Where should the duck go?",
                  "clarification_options": ["the drop-off zone"]},
}
#: Unseen cases the baseline gets wrong today (it is not what prompt_eval measures).
BASELINE_MISSES = set(GOOD)


async def good_outputs() -> list[str]:
    """A known-good script: the deterministic baseline's proposal, or GOOD where it misses."""
    out = []
    for case in pe.CASES:
        if case["id"] in GOOD:
            out.append(json.dumps(GOOD[case["id"]]))
            continue
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
    assert len(commands) == 17 and "Find a red box in the feild" in commands
    assert {d for c in pe.CASES for d in pe.accepted(c)} == {"plan", "clarify", "answer", "reject"}
    assert all(isinstance(c["in_prompt"], bool) for c in pe.CASES)


def test_unseen_paraphrases_are_tagged_apart_from_prompt_examples():
    unseen = [c["command"] for c in pe.CASES if not c["in_prompt"]]
    for cmd in ["could you locate the blue cube?", "snap a photo of the drop-off area",
                "carry the tennis ball to the drop-off zone", "grab the toy car and bring it to the drop-off zone",
                "is the corridor clear?", "take the duck home"]:
        assert cmd in unseen
    assert len(unseen) >= 10
    assert all(c["in_prompt"] for c in pe.CASES if c["id"] in {"find-red-box", "photo", "fly-drone"})


async def test_baseline_passes_every_in_prompt_case_and_misses_only_known_unseen_ones():
    rows = await pe.evaluate(BaselinePlanner(), pe.CASES, runs=1)
    assert len(rows) == 17
    assert {r["case"] for r in rows if not r["ok"]} == BASELINE_MISSES
    assert all(r["ok"] for r in rows if r["in_prompt"])


async def test_wrong_planner_fails_with_reasons_and_runs_repeat():
    rows = await pe.evaluate(WrongPlanner(), pe.CASES, runs=2)
    assert len(rows) == 34 and {r["run"] for r in rows} == {1, 2}
    failed = [r for r in rows if not r["ok"]]
    assert len(failed) == 28 and all("expected" in r["note"] and "got reject" in r["note"] for r in failed)
    text = pe.table(rows)
    assert "FAIL" in text and "6/34 passed" in text
    assert "unseen (not in prompt examples): 4/20 passed" in text and "in prompt examples: 2/14 passed" in text


def _plan(goals, tasks=(), issues=()):
    return {"disposition": "plan", "scope": "full", "mission_goals": list(goals), "tasks": list(tasks),
            "issues": list(issues)}


CASE = {c["id"]: c for c in pe.CASES}
SCAN = {"action": {"kind": "scan_route", "route_id": "pickup_corridor"}, "preferred_robot_id": "lidar_01"}
CARRY = {"action": {"kind": "pick_object"}, "preferred_robot_id": "gripper_01"}
LOOK = {"action": {"kind": "inspect_area", "area_id": "work_area"}, "preferred_robot_id": "camera_01"}


@pytest.mark.parametrize("case_id,proposal,ok", [
    ("corridor-clear", {"disposition": "answer", "answer": "Unknown until scanned."}, True),
    ("corridor-clear", _plan([{"kind": "route_observed", "route_id": "pickup_corridor"}], [SCAN]), True),
    ("corridor-clear", _plan([{"kind": "image_captured", "area_id": "work_area"}]), False),
    ("corridor-clear", {"disposition": "clarify"}, False),
    ("toy-car-over-payload", {"disposition": "reject"}, True),
    ("toy-car-over-payload", {"disposition": "clarify"}, True),
    ("toy-car-over-payload", _plan([{"kind": "object_located", "object_query": {"label": "rc_car"},
                                     "area_id": "work_area"}], [LOOK], ["rc_car is over the payload"]), True),
    ("toy-car-over-payload", _plan([{"kind": "object_located", "object_query": {"label": "rc_car"},
                                     "area_id": "work_area"}], [LOOK]), False),  # no issue raised
    ("toy-car-over-payload", _plan([{"kind": "object_at", "object_query": {"label": "rc_car"},
                                     "location_id": "dropoff_zone"}], [CARRY], ["heavy"]), False),
    ("photo-dropoff", {"disposition": "reject"}, True),
    ("photo-dropoff", _plan([{"kind": "image_captured", "area_id": "work_area"}]), False),
    ("duck-home", {"disposition": "clarify"}, True),
    ("duck-home", _plan([{"kind": "object_at", "object_query": {"label": "duck"}, "location_id": "dropoff_zone"}]),
     False),
])
def test_check_accepts_only_the_documented_set(case_id, proposal, ok):
    assert (not pe.check(CASE[case_id], proposal, valid=True)) is ok


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
    assert code == 0 and "34/34 passed" in out and KEY not in out
    seeds = [b.get("seed") for b in mock.requests if "INPUT (data" in b["messages"][-1]["content"]
             and "Fly the drone to the roof" not in b["messages"][-1]["content"]]  # not the probe
    assert sorted(set(seeds)) == [7, 8] and len(seeds) == 34
    assert "sampling scripted-mock: temperature 0.2 top_p 0.8 top_k 20 seed 7" in out
    assert "WARNING" not in out


async def test_main_warns_before_running_when_the_row_samples_hot(capsys):
    mock = ScriptedQwen(await good_outputs())
    save_chain([mock_row(sampling=Sampling(temperature=0.7, top_k=None, seed=5))], keys={"scripted-mock": KEY})
    code = await pe.run([], transport=httpx.MockTransport(mock.handler))
    out = capsys.readouterr().out
    assert code == 0 and KEY not in out
    head = out.split("prompt_eval ")[0]
    assert "sampling scripted-mock: temperature 0.7 top_p 0.8 top_k None seed 5" in head
    assert "WARNING: scripted-mock plans at temperature 0.7 (> 0.3)" in head and "re-save" in head


async def test_main_live_path_exits_1_on_a_failure(capsys):
    outputs = await good_outputs()
    outputs[0] = outputs[7]  # the reject answer for the first find case
    mock = ScriptedQwen(outputs)
    save_chain([mock_row()], keys={"scripted-mock": KEY})
    code = await pe.run([], transport=httpx.MockTransport(mock.handler))
    out = capsys.readouterr().out
    assert code == 1 and "16/17 passed" in out and "FAIL" in out and KEY not in out
    assert json.loads(outputs[0])["disposition"] == "reject"


async def test_sampling_lines_print_the_effective_num_ctx_for_an_ollama_row(capsys):
    from hive_exec.llm.router import Router

    save_chain([mock_row(), mock_row("local-ollama", "ollama.local", kind="ollama", structured_mode="ollama_format",
                                    base_url="http://ollama.local:11434", model="qwen3:8b",
                                    sampling=Sampling(max_tokens=2048))])
    lines = pe.sampling_lines(Router())
    openai_line = next(x for x in lines if x.startswith("sampling scripted-mock:"))
    ollama_line = next(x for x in lines if x.startswith("sampling local-ollama:"))
    assert "num_ctx" not in openai_line or "not sent" in openai_line
    assert ollama_line.endswith("num_ctx 11264")  # 9216 + the row's 2048
