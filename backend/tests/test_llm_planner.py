"""LLM planner wiring against a SCRIPTED mock endpoint (not a live LLM).

Moved from test_qwen_planner.py; the original assertions are kept, with the
planner kind now "llm" ("qwen" stays accepted as an alias)."""

import json
import re

from .conftest import DEMO, approve_latest, wait_for
from .llm_mock import MOCK_MODEL, scripted_planner

OBS = {
    "schema_version": "hive.plan-proposal.v1", "disposition": "plan", "scope": "observation_stage",
    "summary": "Locate the red block and observe the pickup corridor.",
    "mission_goals": [{"kind": "object_at", "object_query": {"label": "red_block"}, "location_id": "dropoff_zone"}],
    "stage_goals": [], "issues": [], "clarification_question": None,
    "tasks": [
        {"task_id": "t1", "binding_id": "observer", "action": {"kind": "inspect_area", "area_id": "work_area",
                                                               "object_query": {"label": "red_block"}},
         "depends_on": [], "preferred_robot_id": "camera_01"},
        {"task_id": "t2", "binding_id": "surveyor", "action": {"kind": "scan_route", "route_id": "pickup_corridor"},
         "depends_on": [], "preferred_robot_id": "lidar_01"}],
}


def carry(**over):
    nav = lambda loc, deps: {"kind": "navigate_to", "location_id": loc, "route_id": "pickup_corridor"}
    p = dict(OBS, scope="full", summary="Carry it.", tasks=[
        {"task_id": "t1", "binding_id": "carrier", "action": nav("pickup_zone", []), "depends_on": [],
         "preferred_robot_id": "gripper_01"},
        {"task_id": "t2", "binding_id": "carrier", "action": {"kind": "pick_object", "object_id": "red_block_1"},
         "depends_on": ["t1"], "preferred_robot_id": "gripper_01"},
        {"task_id": "t3", "binding_id": "carrier", "action": nav("dropoff_zone", []), "depends_on": ["t2"],
         "preferred_robot_id": "gripper_01"},
        {"task_id": "t4", "binding_id": "carrier", "action": {"kind": "place_object", "object_id": "red_block_1",
                                                              "location_id": "dropoff_zone"},
         "depends_on": ["t3"], "preferred_robot_id": "gripper_01"}])
    p.update(over)
    return p


async def test_request_shape_non_thinking_structured(make_exec):
    planner, mock = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    body = mock.requests[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["structured_outputs"]["json"] == planner.schema
    assert body["seed"] == 1234 and body["model"] == MOCK_MODEL
    rec = ex.mission.revisions[-1].planner
    assert rec.kind == "llm" and rec.model == MOCK_MODEL and "mock" in rec.label
    assert rec.first_pass_ok and not rec.repaired


async def test_malformed_output_gets_one_repair(make_exec):
    planner, mock = scripted_planner(['{"disposition": "plan"', json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert rec.repaired and rec.attempts == 2 and rec.first_pass_ok is False
    assert "PREVIOUS OUTPUT WAS REJECTED" in mock.requests[-1]["messages"][-1]["content"]


async def test_two_bad_outputs_surface_planner_error(make_exec):
    planner, _ = scripted_planner(["not json", "still not json"])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert not ex.mission.revisions


async def test_validation_failure_triggers_repair(make_exec):
    # First output transports without evidence (invalid); repair returns the observation stage.
    planner, mock = scripted_planner([json.dumps(carry()), json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rev = ex.mission.revisions[-1]
    assert rev.validation.ok and rev.proposal.scope == "observation_stage"
    assert rev.planner.repaired
    assert "route_not_observed_clear" in mock.requests[-1]["messages"][-1]["content"] or \
        "unknown_object" in mock.requests[-1]["messages"][-1]["content"]


async def test_full_two_stage_flow_with_mock(make_exec):
    planner, _ = scripted_planner([json.dumps(OBS), json.dumps(carry())])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    await approve_latest(ex)
    await wait_for(ex, {"awaiting_approval", "blocked", "completed", "planner_error"})
    await approve_latest(ex)
    assert await wait_for(ex, {"completed", "blocked"}) == "completed"


async def test_unconfigured_endpoint_is_visible_not_substituted(make_exec):
    from hive_exec.config import QwenSettings
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    ex = await make_exec(planners={"llm": LLMPlanner(Router(legacy=QwenSettings(base_url="")))})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert "not configured" in ex.mission.outcome["reason"]
    assert ex.mission.planner_kind == "llm" and not ex.mission.revisions


# ---------------------------------------------------------------- prompt 4: provider store + router
import httpx  # noqa: E402

from .llm_mock import MOCK_ID, ScriptedQwen, mock_row, save_chain  # noqa: E402

FAKE_KEY = "sk-test-NOT-A-REAL-KEY-9f3k"


def _planner(handler):
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    return LLMPlanner(Router(transport=httpx.MockTransport(handler)))


async def test_record_carries_provider_fields(make_exec):
    planner, _ = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert rec.provider_id == MOCK_ID and rec.provider_kind == "openai_compatible"
    assert rec.fallback_used is False and rec.failovers == []
    assert rec.provider_latency_ms is not None and rec.provider_latency_ms >= 0
    assert rec.usage == {"prompt_tokens": 100, "completion_tokens": 50}
    assert rec.endpoint == "http://mock.invalid/v1"
    assert rec.settings["structured_mode"] == "structured_outputs"
    assert rec.settings["provider_kind"] == "openai_compatible"


async def test_failover_on_timeout_is_recorded(make_exec):
    mock = ScriptedQwen([json.dumps(OBS)])
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "down.invalid":
            raise httpx.ConnectTimeout("timed out", request=request)
        return mock.handler(request)

    save_chain([mock_row("down-first", host="down.invalid"), mock_row()])
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    rec = ex.mission.revisions[-1].planner
    assert hosts == ["down.invalid", "mock.invalid"]
    assert rec.fallback_used is True and rec.provider_id == MOCK_ID
    assert len(rec.failovers) == 1 and rec.failovers[0].startswith("down-first: ConnectTimeout")


async def test_schema_failure_is_repaired_not_failed_over(make_exec):
    mock = ScriptedQwen(["not json", "still not json"])
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "second.invalid":
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(OBS)}}]})
        return mock.handler(request)

    save_chain([mock_row(), mock_row("second", host="second.invalid")])
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    assert hosts == ["mock.invalid", "mock.invalid"]  # first pass + the Executive's one repair
    assert ex.mission.status == "planner_error" and not ex.mission.revisions
    assert "PREVIOUS OUTPUT WAS REJECTED" in mock.requests[-1]["messages"][-1]["content"]


async def test_every_provider_down_is_planner_error_with_attempts(make_exec):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    save_chain([mock_row("a", host="a.invalid"), mock_row("b", host="b.invalid")])
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "planner_error"
    assert "every provider for role 'executive' failed" in ex.mission.outcome["reason"]
    rec = ex.mission.planning_log[-1]["planner"]
    assert rec["provider_id"] == "b" and len(rec["failovers"]) == 2


async def test_provider_401_echoing_the_key_is_redacted(make_exec):
    auth = []

    def handler(request):
        auth.append(request.headers.get("authorization"))
        if request.url.host == "second.invalid":
            raise AssertionError("a 401 must not fail over")
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {FAKE_KEY}"}})

    save_chain([mock_row(), mock_row("second", host="second.invalid")], keys={MOCK_ID: FAKE_KEY})
    ex = await make_exec(planners={"llm": _planner(handler)})
    await ex.create_mission(DEMO)
    assert auth == [f"Bearer {FAKE_KEY}"]  # the key reached the client through its env var
    assert ex.mission.status == "planner_error"
    reason = ex.mission.outcome["reason"]
    assert "HTTP 401" in reason and "Incorrect API key provided" in reason and "…9f3k" in reason
    assert FAKE_KEY not in json.dumps(ex.state(), default=str)
    assert FAKE_KEY not in repr(ex.planners["llm"].status())


async def test_qwen_alias_is_accepted_in_state(make_exec):
    from hive_exec.contracts import PlannerRecord

    assert PlannerRecord(kind="qwen", label="old run").kind == "llm"
    planner, _ = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"qwen": planner})  # old key and default normalize to "llm"
    assert set(ex.planners) == {"llm"} and ex.default_planner == "llm"
    await ex.create_mission(DEMO, "qwen")
    assert ex.mission.planner_kind == "llm"
    assert ex.mission.revisions[-1].planner.kind == "llm"


async def test_status_reports_provider_chain():
    planner, _ = scripted_planner([])
    save_chain([mock_row(), mock_row("backup", host="backup.invalid")])
    s = planner.status()
    assert s["kind"] == "llm" and s["configured"] is True and s["role"] == "executive"
    assert s["provider"] == MOCK_ID and s["model"] == MOCK_MODEL and s["fallbacks"] == ["backup"]
    assert s["live_enabled"] is False and s["probe"] is None


async def test_status_unconfigured_is_explicit():
    from hive_exec.config import QwenSettings
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    s = LLMPlanner(Router(legacy=QwenSettings(base_url=""))).status()
    assert s["kind"] == "llm" and s["configured"] is False and s["provider"] is None
    assert any("not configured" in p for p in s["problems"])


async def test_baseline_is_labeled_no_llm(make_exec):
    ex = await make_exec("known")
    await ex.create_mission(DEMO)
    assert ex.mission.revisions[-1].planner.label == "Baseline (no LLM)"


async def test_probe_runs_against_the_primary_and_enables_live():
    planner, mock = scripted_planner([])
    assert planner.status()["live_enabled"] is False
    result = await planner.probe()
    assert result["ok"] is True and result["provider_id"] == MOCK_ID
    s = planner.status()
    assert s["live_enabled"] is True and s["probe"]["schema_roundtrip"]["ok"] is True
    assert mock.contents == []  # the probe never consumed a scripted planning answer


async def test_probe_unconfigured_is_a_clean_result():
    from hive_exec.config import QwenSettings
    from hive_exec.llm.router import Router
    from hive_exec.planner.llm import LLMPlanner

    result = await LLMPlanner(Router(legacy=QwenSettings(base_url=""))).probe()
    assert result["ok"] is False and "not configured" in result["error"]


async def test_propose_does_not_wait_for_a_probe(make_exec):
    planner, mock = scripted_planner([json.dumps(OBS)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    assert ex.mission.status == "awaiting_approval" and len(mock.requests) == 1


def test_catalog_text_presents_labels_as_open_vocabulary():
    from hive_exec.catalog import OBJECT_PROFILES
    from hive_exec.planner.prompt import RULES, _catalog_text, system_prompt

    text = _catalog_text()
    assert "OBJECT LABELS: red_block, blue_block, green_block" not in text
    assert "open vocabulary" in text and "lowercase snake_case" in text
    assert "objects with a trusted profile (mass known)" in text
    assert "can be found but not carried" in text
    for label, prof in OBJECT_PROFILES.items():
        assert f"{label} ({prof.mass_kg} kg" in text
    assert "rubber_duck" in text  # aliases are listed so the model can map synonyms
    assert RULES in system_prompt()


# ---------------------------------------------------------------- WS2: prompt v3
def _examples():
    from hive_exec.contracts import PlanProposal
    from hive_exec.planner.prompt import EXAMPLES

    return [(cmd, PlanProposal.model_validate(out)) for cmd, _state, out in EXAMPLES]


def _example(prefix):
    return next(p for cmd, p in _examples() if cmd.startswith(prefix))


def test_prompt_v3_rules_cover_find_answer_and_asking():
    from hive_exec.planner.prompt import PROMPT_VERSION, RULES, system_prompt

    assert PROMPT_VERSION == "hive.prompt.v3.5" and "hive.prompt.v3.5" in system_prompt()
    for phrase in ["Write your own summary and rationale for this command; do not reuse example text.",
                   "object_located", "no scan_route", "the field", "work_area", "ONE short question",
                   "Never map an unnamed destination to dropoff_zone", "operator's own noun",
                   "disposition=answer", "clarification_options", "Do not ask when one reading is clear"]:
        assert phrase in RULES, phrase


def test_prompt_v3_examples_validate_and_have_distinct_summaries():
    examples = _examples()
    assert {p.disposition for _, p in examples} == {"plan", "clarify", "reject", "answer"}
    summaries = [p.summary for _, p in examples]
    assert len(set(summaries)) == len(summaries)


def test_prompt_v3_find_only_example_is_sensing_only():
    p = _example("Find a red box in the field")
    assert p.disposition == "plan" and p.scope == "full"
    assert [(g.kind, g.object_query.label, g.area_id) for g in p.mission_goals] == \
        [("object_located", "red_block", "work_area")]
    assert [t.action.kind for t in p.tasks] == ["inspect_area"]


def test_prompt_v3_fetch_example_is_staged_with_the_route_scan():
    p = _example("Find the green block and bring it to the drop-off zone")
    assert p.scope == "observation_stage"
    assert [(g.kind, g.location_id) for g in p.mission_goals] == [("object_at", "dropoff_zone")]
    assert {t.action.kind for t in p.tasks} == {"inspect_area", "scan_route"}


def test_prompt_v3_examples_show_unknown_noun_answer_and_clarify():
    duck = _example("Is there a duck")
    assert duck.disposition == "plan" and duck.mission_goals[0].object_query.label == "duck"
    ans = _example("What can the camera car do?")
    assert ans.disposition == "answer" and ans.answer and not ans.tasks
    dest = _example("Bring the blue block back to base")
    assert dest.disposition == "clarify" and "dropoff_zone" not in json.dumps(dest.model_dump()["mission_goals"])
    assert dest.clarification_options and dest.clarification_question.count("?") == 1
    vague = _example("Bring the block over here")
    assert vague.disposition == "clarify" and vague.clarification_question.count("?") == 1
    assert len(vague.clarification_options) >= 2


def test_prompt_v3_fits_the_token_budget_with_clarification_history():
    from hive_exec.context import MAX_INPUT_TOKENS, build_context, estimate_tokens
    from hive_exec.llm import wire
    from hive_exec.planner.prompt import system_prompt, user_prompt
    from hive_exec.world import ObservedWorld, initial_robot_states

    hist = [{"question": "Which block should I bring?", "options": ["the red block", "the blue block"],
             "answer": "the red block", "phase": "initial"}] * 3
    ctx = build_context(phase="clarification_answer", command="Bring the block over here.",
                        robots=initial_robot_states(), world=ObservedWorld(), clarifications=hist)
    total = (estimate_tokens(system_prompt()) + estimate_tokens(user_prompt(ctx))
             + estimate_tokens(json.dumps(wire.planning_schema())) // 4)
    assert total < MAX_INPUT_TOKENS * 6 // 10  # WS4: was 50 %; the preview rules and schema need the room


# ---------------------------------------------------------------- WS3: plan rationale and sensorless navigation
def test_prompt_catalog_lists_each_robots_sensors_and_localization():
    from hive_exec.catalog import ROBOTS
    from hive_exec.planner.prompt import _catalog_text

    text = _catalog_text()
    assert "rgb_camera" in text and "2d_lidar" in text
    for r in ROBOTS.values():
        assert r.localization.replace(" (synthetic)", "") in text, r.robot_id  # the label is dropped to save tokens
    gripper_line = next(ln for ln in text.splitlines() if ln.startswith("- gripper_01"))
    assert "sensors none" in gripper_line and "no onboard camera or LiDAR" in gripper_line


def test_prompt_rule_asks_the_planner_to_decide_and_explain_sensorless_navigation():
    from hive_exec.planner.prompt import RULES

    rule = RULES[RULES.index("14. "):]
    for phrase in ["rationale", "sensors none", "dead reckoning", "Every plan", "decide"]:
        assert phrase in rule, phrase
    # the planner decides: no hard-coded escort rule in the rules or the validator
    assert "must be escorted" not in RULES and "always scan" not in RULES.lower()


def test_prompt_examples_explain_every_plan_that_moves_the_sensorless_robot():
    moving = [(cmd, p) for cmd, p in _examples()
              if p.disposition == "plan" and any(t.preferred_robot_id == "gripper_01" for t in p.tasks)]
    assert moving
    for cmd, p in moving:
        assert p.rationale, cmd
        assert any("gripper_01" in item and ("dead" in item or "lidar" in item or "camera" in item)
                   for item in p.rationale), cmd
    assert _example("Find a red box in the field").rationale  # sensing plans carry one too


PROMPT_V3_CHARS = 10917  # len(system_prompt()) of hive.prompt.v3 at e6f94bf


def test_prompt_growth_for_rationale_stays_under_600_chars():
    # v3 -> v3.1 added < 600; v3.1 -> v3.2 (preview) may add PROMPT_WS4_BUDGET more (see the WS4 test below)
    from hive_exec.planner.prompt import system_prompt

    assert len(system_prompt()) - PROMPT_V3_CHARS < 600 + PROMPT_WS4_BUDGET + PROMPT_FIX_BUDGET + PROMPT_A8_BUDGET


def test_prompt_with_rationale_still_fits_the_token_budget():
    from hive_exec.llm import wire

    assert wire.planning_schema()["properties"]["rationale"]["maxItems"] == 4
    test_prompt_v3_fits_the_token_budget_with_clarification_history()


# ---------------------------------------------------------------- WS4: next-stage preview in the prompt
PROMPT_V31_CHARS = 11513  # len(system_prompt()) of hive.prompt.v3.1 at afd062e
PROMPT_WS4_BUDGET = 900 + 200  # B (preview rules and example) < 900, G (carry-overs) <= 200 on top
PROMPT_V32_WS4_CHARS = 12572  # len(system_prompt()) when WS4 landed (a24f8f0)
PROMPT_FIX_BUDGET = 120  # WS4 fix F6 (preview_changes only with a previous preview) may add this much


def _preview_context(preview=True):
    from hive_exec.contracts import Observation, ObjectAtGoal
    from hive_exec.context import build_context
    from hive_exec.world import ObservedWorld, initial_robot_states

    w = ObservedWorld()
    w.accept(Observation(kind="object_seen", entity_id="green_block_1", label="green_block", area_id="work_area",
                         location_id="pickup_zone"), source="camera_01")
    goal = ObjectAtGoal(kind="object_at", object_query={"label": "green_block"}, location_id="dropoff_zone")
    prev = None
    if preview:
        prev = {"from_revision": 1, "summary": "Carry the green block to the drop-off zone.",
                "tasks": [{"task_id": "t1", "binding_id": "carrier",
                           "action": {"kind": "navigate_to", "location_id": "pickup_zone",
                                      "route_id": "pickup_corridor"}, "depends_on": []}],
                "waits_for": ["camera_01 sighting of green_block"]}
    return build_context(phase="continuation", command="Bring the green block to the drop-off zone.",
                         robots=initial_robot_states(), world=w, canonical_goals=[goal], previous_preview=prev)


def test_prompt_is_v3_5_and_says_so():
    from hive_exec.planner.prompt import PROMPT_VERSION, system_prompt

    assert PROMPT_VERSION == "hive.prompt.v3.5" and "hive.prompt.v3.5" in system_prompt()


def test_prompt_rules_ask_for_a_preview_and_for_changes_on_continuation():
    from hive_exec.planner.prompt import RULES

    assert "next_stage_preview" in RULES and "waits_for" in RULES and "object_query" in RULES
    rule = RULES[RULES.index("16. "):]
    for phrase in ["previous_preview", "keep it where it still fits", "object elsewhere", "two matches",
                   "route blocked", "robot battery low", "preview_changes", "max 4",
                   "Without previous_preview leave preview_changes empty"]:
        assert phrase in rule, phrase
    assert "Only shown, never run" in RULES


def test_fetch_example_shows_a_next_stage_preview_by_object_query_and_no_other_example_does():
    p = _example("Find the green block and bring it to the drop-off zone")
    pv = p.next_stage_preview
    assert p.scope == "observation_stage" and pv is not None and len(pv.waits_for) >= 1
    assert [t.action.kind for t in pv.tasks] == ["navigate_to", "pick_object", "navigate_to", "place_object"]
    assert pv.tasks[1].action.object_query.label == "green_block" and pv.tasks[1].action.object_id is None
    assert [c for c, e in _examples() if e.next_stage_preview is not None] == \
        [next(c for c, _ in _examples() if c.startswith("Find the green block and bring"))]


def test_continuation_example_explains_what_the_evidence_changed():
    from hive_exec.planner.prompt import EXAMPLES

    p = _example("(continuation)")
    assert p.scope == "full" and p.next_stage_preview is None and p.preview_changes
    state = next(st for cmd, st, _ in EXAMPLES if cmd.startswith("(continuation)"))
    assert "previous_preview" in state  # the example's context shows the preview it answers
    assert not [c for c, e in _examples() if e.preview_changes and not c.startswith("(continuation)")]


def test_continuation_user_prompt_carries_the_previous_preview_and_points_at_the_rule():
    from hive_exec.planner.prompt import user_prompt

    text = user_prompt(_preview_context())
    assert '"previous_preview"' in text and "camera_01 sighting of green_block" in text
    assert "previous_preview" in text.split("```")[2]  # the instruction line after the data block
    without = user_prompt(_preview_context(preview=False))
    assert "previous_preview" not in without


def test_prompt_growth_for_preview_and_carry_overs_is_budgeted():
    from hive_exec.planner.prompt import system_prompt

    assert len(system_prompt()) - PROMPT_V31_CHARS < PROMPT_WS4_BUDGET + PROMPT_FIX_BUDGET + PROMPT_A8_BUDGET


def test_schema_with_a_nested_optional_preview_still_fits_the_structured_output_budget():
    from hive_exec.context import MAX_INPUT_TOKENS, estimate_tokens
    from hive_exec.llm import wire
    from hive_exec.planner.prompt import system_prompt, user_prompt

    schema = wire.planning_schema()
    assert "next_stage_preview" in schema["properties"] and "next_stage_preview" not in schema["required"]
    total = (estimate_tokens(system_prompt()) + estimate_tokens(user_prompt(_preview_context()))
             + estimate_tokens(json.dumps(schema)) // 4)
    assert total < MAX_INPUT_TOKENS * 6 // 10  # WS4: was 50 %; the preview rules and schema need the room


async def test_llm_proposal_with_a_preview_becomes_a_revision_with_the_preview_never_allocated(make_exec):
    from hive_exec.catalog import ROBOTS  # noqa: F401

    staged = dict(OBS, next_stage_preview={
        "summary": "Carry the red block to the drop-off zone.",
        "tasks": [{"task_id": "t1", "binding_id": "carrier", "depends_on": [],
                   "action": {"kind": "navigate_to", "location_id": "pickup_zone", "route_id": "pickup_corridor"}}],
        "waits_for": ["camera_01 sighting of red_block"]})
    planner, mock = scripted_planner([json.dumps(staged)])
    ex = await make_exec(planners={"llm": planner})
    await ex.create_mission(DEMO)
    rev = ex.mission.revisions[-1]
    assert rev.proposal.next_stage_preview.tasks[0].action.kind == "navigate_to"
    assert set(rev.allocations) == {"observer", "surveyor"}
    assert mock.requests[-1]["structured_outputs"]["json"] == planner.schema


# ---------------------------------------------------------------- WS4 carry-overs (G): prompt hygiene
def test_every_plan_example_has_its_own_rationale():
    plans = [(cmd, p) for cmd, p in _examples() if p.disposition == "plan"]
    assert plans
    for cmd, p in plans:
        assert p.rationale, cmd
    texts = [r for _, p in plans for r in p.rationale]
    assert len(set(texts)) == len(texts)  # no two examples share a rationale line


def test_rule_11_also_forbids_copying_example_rationale():
    from hive_exec.planner.prompt import RULES

    assert "Write your own summary and rationale for this command; do not reuse example text." in RULES


def _observed(line_text, verb_for):
    import re

    m = re.search(rf"observes {verb_for} ([a-z_, ]+?)(?:;|$)", line_text)
    return {x.strip() for x in m.group(1).split(",")} if m else set()


def test_prompt_observed_places_equal_the_catalog_for_every_robot():
    from hive_exec.catalog import ROBOTS
    from hive_exec.planner.prompt import _catalog_text

    lines = {ln.split(":")[0][2:]: ln for ln in _catalog_text().splitlines() if ln.startswith("- ") and ": skills " in ln}
    assert set(lines) == set(ROBOTS)
    for rid, r in ROBOTS.items():
        assert _observed(lines[rid], "areas") == set(r.observable_areas), rid
        assert _observed(lines[rid], "routes") == set(r.observable_routes), rid
        assert " sees " not in lines[rid] and " scans " not in lines[rid]  # one verb: observes


def test_prompt_follows_the_catalog_when_a_robot_observes_something_else(monkeypatch):
    import dataclasses

    from hive_exec.catalog import ROBOTS
    from hive_exec.planner import prompt

    changed = dict(ROBOTS)
    changed["camera_01"] = dataclasses.replace(ROBOTS["camera_01"], observable_areas=("dropoff_zone",))
    monkeypatch.setattr(prompt, "ROBOTS", changed)
    line = next(ln for ln in prompt._catalog_text().splitlines() if ln.startswith("- camera_01"))
    assert _observed(line, "areas") == {"dropoff_zone"}


def test_localization_texts_no_longer_repeat_what_a_robot_observes():
    from hive_exec.catalog import ROBOTS

    for r in ROBOTS.values():
        for place in (*r.observable_areas, *r.observable_routes):
            assert place not in r.localization, (r.robot_id, place)


def test_prompt_growth_for_the_f6_fix_is_small():
    from hive_exec.planner.prompt import system_prompt

    assert PROMPT_V32_WS4_CHARS < len(system_prompt()) <= PROMPT_V32_WS4_CHARS + PROMPT_FIX_BUDGET + PROMPT_A8_BUDGET


# ---------------------------------------------------------------- A8: map_area in the prompt
PROMPT_V34_CHARS = 12667  # len(system_prompt()) of hive.prompt.v3.4 at 4d0ae65 (A8 baseline)
PROMPT_A8_MAP_BUDGET = 650  # A8 step 3: map_area catalog line, "maps arena", one example; measured +610 (DECISIONS)
PROMPT_A8_BUDGET = PROMPT_A8_MAP_BUDGET


def test_prompt_lists_map_area_and_who_maps_what():
    from hive_exec.catalog import ROBOTS, SKILLS
    from hive_exec.planner.prompt import _catalog_text

    text = _catalog_text()
    assert f"- map_area(area_id): {SKILLS['map_area'].description}" in text
    lines = {ln.split(":")[0][2:]: ln for ln in text.splitlines() if ln.startswith("- ") and ": skills " in ln}
    for rid, r in ROBOTS.items():
        maps = re.search(r"maps ([a-z_, ]+?)(?:;|$)", lines[rid])
        assert (set(x.strip() for x in maps.group(1).split(",")) if maps else set()) == set(r.map_areas), rid


def test_prompt_has_one_map_example_that_validates():
    p = _example("Map the arena")
    assert p.disposition == "plan" and p.scope == "full"
    assert [(g.kind, g.area_id) for g in p.mission_goals] == [("area_mapped", "arena")]
    assert [(t.action.kind, t.preferred_robot_id) for t in p.tasks] == [("map_area", "lidar_01")]
    assert sum(cmd.lower().startswith("map ") for cmd, _ in _examples()) == 1


def test_prompt_growth_for_map_area_is_budgeted():
    from hive_exec.planner.prompt import system_prompt

    assert PROMPT_V34_CHARS < len(system_prompt()) <= PROMPT_V34_CHARS + PROMPT_A8_BUDGET
