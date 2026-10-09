from copy import deepcopy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from analysis_segments import prepare_segment, segment_projection


TASKS = ["chapter01", "chapter02", "chapter03", "material-001", "material-002",
         "material-003", "material-004", "chapter04"]


def state_at(cursor=0):
    return {"inputSha256": "frozen", "completed": [{"taskId": task} for task in TASKS[:cursor]]}


def begin(state, segment_id="s-1", host_run_id="h-1", **kwargs):
    return prepare_segment(state, TASKS, begin_segment=True, segment_id=segment_id,
                           host_run_id=host_run_id, now=100, **kwargs)


def accept_next(state):
    state["completed"].append({"taskId": TASKS[len(state["completed"])]})


def test_legacy_is_unchanged_until_explicit_begin():
    state = state_at(3)
    before = deepcopy(state)
    assert prepare_segment(state, TASKS) is None
    assert segment_projection(state, TASKS, now=200) is None
    assert state == before
    with pytest.raises(ValueError, match="begin_segment"):
        prepare_segment(state, TASKS, segment_id="s", host_run_id="h")
    assert state == before


def test_begin_and_retry_keep_owner_start_time_cursor_and_policy():
    state = state_at(3)
    first = begin(state)
    before = deepcopy(state)
    retry = prepare_segment(state, TASKS, begin_segment=True, segment_id="s-1",
                            host_run_id="h-1", now=999)
    assert retry is first and state == before
    assert first["startCursor"] == 3 and first["startedAtSec"] == 100
    assert first["materialLimit"] == 3 and first["softTimeBudgetSeconds"] == 480
    assert state["inputSha256"] == "frozen"
    assert prepare_segment(state, TASKS, segment_id="s-1", host_run_id="h-1") is first


@pytest.mark.parametrize("kwargs", [
    {}, {"segment_id": "s-1"}, {"segment_id": "s-other", "host_run_id": "h-1"},
    {"segment_id": "s-1", "host_run_id": "h-other"},
])
def test_enabled_sessions_require_matching_identity_on_every_advance(kwargs):
    state = state_at()
    begin(state)
    before = deepcopy(state)
    with pytest.raises(ValueError, match="requires the active"):
        prepare_segment(state, TASKS, **kwargs)
    assert state == before


@pytest.mark.parametrize("field,value", [("material_limit", 4), ("soft_time_budget_seconds", 481)])
@pytest.mark.parametrize("begin_segment", [False, True])
def test_existing_policy_cannot_be_changed(field, value, begin_segment):
    state = state_at()
    begin(state)
    before = deepcopy(state)
    with pytest.raises(ValueError, match="cannot change"):
        prepare_segment(state, TASKS, begin_segment=begin_segment,
                        segment_id="s-1", host_run_id="h-1", **{field: value})
    assert state == before


@pytest.mark.parametrize("value", [None, "", "../bad", "a/b", "a b", "a\nb", "a" * 129])
@pytest.mark.parametrize("field", ["segment_id", "host_run_id"])
def test_ids_must_be_safe_nonempty_and_bounded(field, value):
    state = state_at()
    kwargs = {"segment_id": "s", "host_run_id": "h", field: value}
    with pytest.raises(ValueError, match="identifier"):
        prepare_segment(state, TASKS, begin_segment=True, **kwargs)
    assert state == state_at()


@pytest.mark.parametrize("field,value", [
    ("material_limit", 0), ("material_limit", -1), ("material_limit", True),
    ("material_limit", 1.5), ("soft_time_budget_seconds", 0),
    ("soft_time_budget_seconds", False), ("soft_time_budget_seconds", float("inf")),
    ("soft_time_budget_seconds", float("nan")),
])
def test_invalid_policies_are_rejected_without_state_mutation(field, value):
    state = state_at()
    with pytest.raises(ValueError):
        begin(state, **{field: value})
    assert state == state_at()


def test_three_material_limit_survives_cli_round_trips_without_duplicate_counts():
    state = state_at(3)
    begin(state)
    for number in range(3):
        state = json.loads(json.dumps(state))
        prepare_segment(state, TASKS, segment_id="s-1", host_run_id="h-1")
        assert segment_projection(state, TASKS, now=110)["acceptedMaterialsInSegment"] == number
        accept_next(state)
    before = deepcopy(state)
    prepare_segment(state, TASKS, segment_id="s-1", host_run_id="h-1")
    projection = segment_projection(state, TASKS, now=110)
    assert projection["yieldReason"] == "material_limit"
    assert projection["acceptedMaterialsInSegment"] == 3
    assert projection["lastAcceptedTaskId"] == "material-003"
    assert projection["nextTaskId"] == "material-004"
    assert state == before


def test_next_segment_resets_only_segment_budget_not_session_completion():
    state = state_at(3)
    first = begin(state)
    for _ in range(3):
        accept_next(state)
    first.update(status="yielded", yieldReason="material_limit")
    second = begin(state, "s-2", "h-2", after_segment="s-1")
    projection = segment_projection(state, TASKS, now=110)
    assert second["startCursor"] == 6
    assert projection["acceptedMaterialsInSegment"] == 0
    assert projection["yieldReason"] is None
    assert len(state["completed"]) == 6
    assert state["inputSha256"] == "frozen"


def test_each_chapter_yields_after_one_accepted_task():
    state = state_at()
    begin(state)
    accept_next(state)
    assert segment_projection(state, TASKS, now=110)["yieldReason"] == "chapter_complete"


def test_materials_yield_before_chapter_even_below_material_limit():
    state = state_at(6)
    begin(state)
    accept_next(state)
    assert segment_projection(state, TASKS, now=110)["yieldReason"] == "kind_changed"


def test_final_completion_does_not_require_another_segment():
    state = state_at(7)
    begin(state)
    accept_next(state)
    projection = segment_projection(state, TASKS, now=110)
    assert projection["allTasksComplete"] is True
    assert projection["nextTaskId"] is None and projection["yieldReason"] is None
    with pytest.raises(ValueError, match="all analysis tasks"):
        begin(state, "s-2", "h-2", after_segment="s-1", previous_host_ended=True)


def test_time_budget_yields_only_with_accepted_work_at_an_unissued_boundary():
    state = state_at(3)
    active = begin(state, soft_time_budget_seconds=20)
    projection = segment_projection(state, TASKS, now=120)
    assert projection["softTimeExpired"] is True and projection["yieldReason"] is None
    accept_next(state)
    assert segment_projection(state, TASKS, now=120)["yieldReason"] == "soft_time_budget"
    active["issuedTaskId"] = "material-002"
    projection = segment_projection(state, TASKS, now=130)
    assert projection["softTimeExpired"] is True and projection["yieldReason"] is None
    accept_next(state)
    assert segment_projection(state, TASKS, now=140)["yieldReason"] == "soft_time_budget"


def test_successor_requires_previous_host_confirmation_or_saved_yield():
    state = state_at(3)
    first = begin(state)
    before = deepcopy(state)
    with pytest.raises(ValueError, match="previous host ended"):
        begin(state, "s-2", "h-2", after_segment="s-1")
    assert state == before
    begin(state, "s-2", "h-2", after_segment="s-1", previous_host_ended=True)
    assert first["status"] == "interrupted"
    assert first["endCursor"] == 3
    assert state["segmentHistory"][-1]["previousSegmentId"] == "s-1"


@pytest.mark.parametrize("kwargs", [
    {"after_segment": None, "host_run_id": "h-2"},
    {"after_segment": "wrong", "host_run_id": "h-2"},
    {"after_segment": "s-1", "host_run_id": "h-1"},
])
def test_successor_rejects_wrong_predecessor_and_same_host(kwargs):
    state = state_at(3)
    begin(state)["status"] = "yielded"
    before = deepcopy(state)
    with pytest.raises(ValueError):
        begin(state, "s-2", **kwargs)
    assert state == before


def test_serial_successors_reject_stale_requests_and_historical_reactivation():
    state = state_at(3)
    begin(state)["status"] = "yielded"
    begin(state, "s-2", "h-2", after_segment="s-1")
    before = deepcopy(state)
    with pytest.raises(ValueError, match="latest analysis segment"):
        begin(state, "s-3", "h-3", after_segment="s-1", previous_host_ended=True)
    with pytest.raises(ValueError, match="historical"):
        begin(state, "s-1", "h-1", after_segment="s-2", previous_host_ended=True)
    assert state == before


def test_duplicate_successor_begin_is_idempotent_after_predecessor_closes():
    state = state_at(3)
    begin(state)["status"] = "yielded"
    second = begin(state, "s-2", "h-2", after_segment="s-1")
    before = deepcopy(state)
    assert begin(state, "s-2", "h-2", after_segment="s-1") is second
    assert state == before


def test_historical_host_cannot_reset_budget_even_with_a_new_segment_id():
    state = state_at(3)
    begin(state)["status"] = "yielded"
    begin(state, "s-2", "h-2", after_segment="s-1")["status"] = "yielded"
    before = deepcopy(state)
    with pytest.raises(ValueError, match="historical host run"):
        begin(state, "s-3", "h-1", after_segment="s-2")
    assert state == before


def test_duplicate_host_ids_in_persisted_history_are_rejected():
    state = state_at(3)
    begin(state)["status"] = "yielded"
    begin(state, "s-2", "h-2", after_segment="s-1")
    state["segmentHistory"][-1]["hostRunId"] = "h-1"
    with pytest.raises(ValueError, match="host run IDs must be unique"):
        segment_projection(state, TASKS, now=110)


def test_host_ended_requires_boolean_confirmation():
    state = state_at(3)
    begin(state)
    before = deepcopy(state)
    with pytest.raises(ValueError, match="booleans"):
        begin(state, "s-2", "h-2", after_segment="s-1", previous_host_ended="false")
    assert state == before


def test_projection_is_read_only_and_clock_rollback_does_not_create_negative_elapsed():
    state = state_at(3)
    begin(state)
    before = deepcopy(state)
    assert segment_projection(state, TASKS, now=90)["elapsedSeconds"] == 0
    assert state == before


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(activeSegmentId="missing"),
    lambda s: s["segmentHistory"][0].update(startCursor=999),
    lambda s: s["segmentHistory"][0].update(status="unknown"),
    lambda s: s["segmentHistory"][0].pop("startedAtSec"),
    lambda s: s["segmentHistory"][0].update(kind="chapter"),
    lambda s: s["completed"].append({"taskId": "wrong"}),
])
def test_corrupt_segment_and_task_state_is_rejected(mutation):
    state = state_at(3)
    begin(state)
    mutation(state)
    with pytest.raises(ValueError):
        segment_projection(state, TASKS, now=110)
