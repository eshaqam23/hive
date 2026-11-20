"""Next-stage preview helpers: the deterministic diff of a preview against the stage that followed it.

The model never computes this (and its own ``preview_changes`` is never trusted for it): the backend
compares the previewed tasks with the tasks of the continuation revision by what they would do, not by
their ids. Pure functions, no I/O, so the same inputs always give the same answer.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from ..agent_commands import describe_command
from ..contracts import NextStagePreview, PreviewDiff, PreviewDiffItem

_ID_SUFFIX = re.compile(r"_[0-9]{1,3}$")
MATCH_LINE = "The evidence matches the preview."
MAX_LINES = 4
MAX_LINE_CHARS = 160


def label_of(object_id: str) -> str:
    """``red_block_1`` -> ``red_block`` (object ids are always label + counter)."""
    return _ID_SUFFIX.sub("", object_id)


def _params(action: Any) -> dict[str, Optional[str]]:
    """The parameters that decide what a task does; everything but the object id must match to be 'kept'."""
    out: dict[str, Optional[str]] = {}
    kind = action.kind
    if kind in ("navigate_to", "place_object"):
        out["location_id"] = action.location_id
    if kind == "navigate_to":
        out["route_id"] = action.route_id
    if kind in ("inspect_area", "capture_image"):
        out["area_id"] = action.area_id
    if kind == "scan_route":
        out["route_id"] = action.route_id
    if kind in ("inspect_area", "pick_object", "place_object"):
        query = getattr(action, "object_query", None)
        object_id = getattr(action, "object_id", None)
        out["label"] = query.label if query is not None else (label_of(object_id) if object_id else None)
    return out


def _object_id(action: Any) -> Optional[str]:
    return getattr(action, "object_id", None)


def _differences(preview_task: Any, task: Any) -> list[str]:
    diffs: list[str] = []
    if preview_task.binding_id != task.binding_id:
        diffs.append(f"binding_id: {preview_task.binding_id} -> {task.binding_id}")
    if preview_task.action.kind != task.action.kind:
        diffs.append(f"kind: {preview_task.action.kind} -> {task.action.kind}")
    a, b = _params(preview_task.action), _params(task.action)
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            diffs.append(f"{key}: {a.get(key)} -> {b.get(key)}")
    ida, idb = _object_id(preview_task.action), _object_id(task.action)
    if ida and idb and ida != idb:
        diffs.append(f"object_id: {ida} -> {idb}")
    return diffs


def _describe(task: Any) -> str:
    return describe_command(task.action.kind, task.action.model_dump(exclude={"kind"}, exclude_none=True))


def compute_preview_diff(previous_revision: int, preview: NextStagePreview, tasks: Sequence[Any]) -> PreviewDiff:
    """Compare ``preview.tasks`` with the actual ``tasks`` (anything with binding_id and action).

    Tasks are paired in three passes, each in order: identical (binding, kind, location, route, area,
    object label, and object id when both name one) is kept; the same binding and kind is changed; the
    same kind under another binding is changed. What is left over is added (actual) or removed (previewed).
    """
    previewed = list(preview.tasks)
    actual = list(tasks)
    pair: dict[int, int] = {}  # actual index -> previewed index
    used: set[int] = set()
    kept_idx: set[int] = set()

    def sweep(match, mark_kept: bool) -> None:
        for pi, p in enumerate(previewed):
            if pi in used:
                continue
            for ai, a in enumerate(actual):
                if ai not in pair and match(p, a):
                    pair[ai] = pi
                    used.add(pi)
                    if mark_kept:
                        kept_idx.add(ai)
                    break

    sweep(lambda p, a: not _differences(p, a), True)
    sweep(lambda p, a: p.binding_id == a.binding_id and p.action.kind == a.action.kind, False)
    sweep(lambda p, a: p.action.kind == a.action.kind, False)

    kept, changed, added = [], [], []
    for ai, a in enumerate(actual):
        if ai not in pair:
            added.append(PreviewDiffItem(task=a.task_id, what=_describe(a)))
            continue
        p = previewed[pair[ai]]
        if ai in kept_idx:
            kept.append(PreviewDiffItem(preview_task=p.task_id, task=a.task_id, what=_describe(a)))
        else:
            changed.append(PreviewDiffItem(preview_task=p.task_id, task=a.task_id, what=_describe(a),
                                           was=_describe(p), differences=_differences(p, a)))
    removed = [PreviewDiffItem(preview_task=p.task_id, what=_describe(p))
               for pi, p in enumerate(previewed) if pi not in used]
    return PreviewDiff(previous_revision=previous_revision, matches_preview=not (changed or added or removed),
                       kept=kept, changed=changed, added=added, removed=removed)


def preview_change_lines(diff: PreviewDiff) -> list[str]:
    """Up to four short sentences for ``preview_changes``, from the diff (used by the baseline planner)."""
    if diff.matches_preview:
        return [MATCH_LINE]
    lines = [f"{i.preview_task} -> {i.task} changed: " + "; ".join(i.differences) for i in diff.changed]
    lines += [f"Previewed {i.preview_task} is no longer needed: {i.what}" for i in diff.removed]
    lines += [f"New task {i.task}: {i.what}" for i in diff.added]
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES - 1] + [f"{len(lines) - MAX_LINES + 1} more differences."]
    return [line[:MAX_LINE_CHARS] for line in lines]
