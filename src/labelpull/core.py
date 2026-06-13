"""Ontology-agnostic Labelbox export + flatten.

The Labelbox SDK already exports a project's labels and streams them as deeply
nested, ontology-shaped JSON. What it does *not* give you is a tabular view, the
correctness logic to pick the right label when a row was reviewed, or a workflow
status that is always populated. This module is exactly that thin layer:

* :func:`export` wraps ``project.export(...)`` + ``wait_till_done()`` +
  ``get_buffered_stream()`` (SDK lazy-imported, so it is optional) and adds a
  ``since`` filter for "only the latest annotations".
* :func:`flatten` turns one export row into :class:`FeatureRow` long-format rows,
  covering *every* feature kind (classifications AND objects) without assuming a
  particular ontology. It encodes the two traps a hand-written parser gets wrong:
  selecting the most-recently-created label (a QC-reviewed row carries both the
  annotator's and the reviewer's label) and normalizing the workflow status.
* :func:`read_export_file` parses a saved export (UI download or a prior pull) so
  the same flattener runs offline, no API key required.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

# One export row (and its nested blocks) is arbitrary JSON; alias it for brevity.
JsonDict = dict[str, Any]

# The task-queue stages ``project.export(filters={"workflow_status": ...})`` accepts.
WORKFLOW_STATUSES = ("ToLabel", "InReview", "InRework", "Done")

# Geometry keys a localized object may carry in the v7 export, in probe order.
_GEOMETRY_KINDS = ("bounding_box", "polygon", "line", "point", "mask")


@dataclass(frozen=True)
class FeatureRow:
    """One ``(label, feature)`` pair from an export row, ontology-agnostic.

    A classification answer or a localized object. An object's nested
    classifications become their own rows, linked to the object via
    :attr:`parent_feature_id`. Each labelled data row also yields one
    ``feature_kind="label"`` sentinel row (no feature, ``value=""``) so that a
    reached-and-labelled row is always represented even when empty.
    """

    global_key: str
    data_row_id: str
    # one of: label, radio, checklist, text, bounding_box, polygon,
    # line, point, mask, relationship, unknown
    feature_kind: str
    feature_name: str
    value: str  # answer value(s) / compact geometry; "" when none
    workflow_status: str | None
    labeled_by: str | None
    created_at: str | None
    parent_feature_id: str  # "" for top-level features


def export(
    project_id: str,
    *,
    status: str | None = None,
    since: str | None = None,
    api_key: str | None = None,
    client: Any | None = None,
) -> Iterator[JsonDict]:
    """Stream export rows (one dict per data row) for ``project_id``.

    ``status`` filters by task-queue stage (see :data:`WORKFLOW_STATUSES`).
    ``since`` keeps only rows whose newest label was created on/after an ISO
    date/datetime string (lexicographic compare on the ISO timestamp). Pass
    ``client`` to inject a stub; otherwise the ``labelbox`` SDK is imported
    lazily and a client is built from ``api_key`` or ``LABELBOX_API_KEY``.
    """
    cl = client if client is not None else _make_client(api_key)
    project = cl.get_project(project_id)
    filters = {"workflow_status": status} if status else None
    task = project.export(
        params={"data_row_details": True, "label_details": True, "project_details": True},
        filters=filters,
    )
    task.wait_till_done()
    for row in task.get_buffered_stream():
        dr = row.json
        if since is None or _created_at(_select_project(dr, project_id)) >= since:
            yield dr


def read_export_file(path: str | Path) -> list[JsonDict]:
    """Load a saved export (JSON array or NDJSON) for offline flattening."""
    text = Path(path).read_text().strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, list) else [loaded]
    except json.JSONDecodeError:
        result: list[JsonDict] = []
        for i, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"malformed NDJSON on line {i}: {e}") from e
        return result


def flatten(dr: JsonDict, project_id: str | None = None) -> list[FeatureRow]:
    """Flatten one export row into :class:`FeatureRow` rows (every feature).

    ``project_id`` selects which project's labels to read; ``None`` uses the only
    project present (the common single-project export) and returns nothing if the
    row is ambiguous (multiple projects) so a caller never silently mixes them.
    An unreached or unlabelled row yields ``[]``.
    """
    data_row = dr.get("data_row") or {}
    global_key = data_row.get("global_key") or ""
    data_row_id = data_row.get("id") or global_key
    proj = _select_project(dr, project_id)
    label = _latest_label(proj)
    if not global_key or label is None:
        return []

    status = _workflow_status(proj)
    details = label.get("label_details") or {}
    labeled_by = details.get("created_by")
    created_at = details.get("created_at")
    ann = label.get("annotations") or {}
    rows: list[FeatureRow] = []

    def emit(kind: str, name: str | None, value: str, parent: str = "") -> None:
        rows.append(
            FeatureRow(
                global_key,
                data_row_id,
                kind,
                name or "",
                value,
                status,
                labeled_by,
                created_at,
                parent,
            )
        )

    # Sentinel: this row was reached and labelled (carries who/when even if empty).
    emit("label", "", "")

    for cls in ann.get("classifications") or []:
        kind, value = _classification_value(cls)
        emit(kind, cls.get("name"), value)

    for obj in ann.get("objects") or []:
        kind, value = _object_geometry(obj)
        feature_id = obj.get("feature_id") or obj.get("feature_schema_id") or ""
        emit(kind, obj.get("name"), value)
        for cls in obj.get("classifications") or []:
            ckind, cvalue = _classification_value(cls)
            emit(ckind, cls.get("name"), cvalue, parent=feature_id)

    for rel in ann.get("relationships") or []:
        value = json.dumps(rel.get("relationship") or {}, sort_keys=True)
        emit("relationship", rel.get("name"), value)

    return rows


@dataclass(frozen=True)
class Summary:
    """Triage view of a pull: how much came back, of what kind, how fresh."""

    n_data_rows: int
    n_labelled: int
    n_reached_unlabelled: int  # rows without labels, regardless of workflow stage
    feature_kinds: dict[str, int]
    feature_names: dict[str, int]
    statuses: dict[str, int]
    latest_created_at: str | None


def summarize(rows: Iterable[JsonDict], features: Iterable[FeatureRow]) -> Summary:
    """Count data rows, labelled rows, and per-kind/name/status breakdowns."""
    rows = list(rows)
    features = list(features)
    labelled_keys = {f.global_key for f in features}
    kinds: dict[str, int] = {}
    names: dict[str, int] = {}
    statuses: dict[str, int] = {}
    latest: str | None = None
    for f in features:
        if f.feature_kind == "label":
            if f.workflow_status:
                statuses[f.workflow_status] = statuses.get(f.workflow_status, 0) + 1
            if f.created_at and (latest is None or f.created_at > latest):
                latest = f.created_at
            continue
        kinds[f.feature_kind] = kinds.get(f.feature_kind, 0) + 1
        if f.feature_name:
            names[f.feature_name] = names.get(f.feature_name, 0) + 1
    n_data_rows = len(rows)
    n_labelled = len(labelled_keys)
    return Summary(
        n_data_rows=n_data_rows,
        n_labelled=n_labelled,
        n_reached_unlabelled=max(n_data_rows - n_labelled, 0),
        feature_kinds=kinds,
        feature_names=names,
        statuses=statuses,
        latest_created_at=latest,
    )


# --- internals -------------------------------------------------------------


def _make_client(api_key: str | None) -> Any:
    try:
        import labelbox as lb  # noqa: PLC0415 (optional dep, imported only for live pulls)
    except ImportError as exc:  # pragma: no cover - exercised only without the SDK
        raise RuntimeError(
            "a live pull needs the Labelbox SDK: pip install 'labelpull[live]'"
        ) from exc
    key = api_key or os.environ.get("LABELBOX_API_KEY")
    if not key:
        raise RuntimeError(
            "no Labelbox API key: pass api_key=... or set LABELBOX_API_KEY "
            "(or use a saved export with read_export_file)"
        )
    return lb.Client(api_key=key)


def _select_project(dr: JsonDict, project_id: str | None) -> JsonDict:
    projects = dr.get("projects") or {}
    if project_id is not None:
        return projects.get(project_id) or {}
    if len(projects) == 1:
        return next(iter(projects.values()))
    return {}  # ambiguous: caller must name the project


def _latest_label(proj: JsonDict) -> JsonDict | None:
    """Return the most-authoritative label from the project export block.

    A QC-reviewed row carries the annotator's label *and* the reviewer's; the
    verified answer is the most recently created, not labels[0].

    Selection rules:
    - If ALL labels have a non-empty ``created_at``, return the one with the
      maximum (newest) timestamp.
    - Otherwise, at least one label is missing a timestamp. Fall back to export
      order and return ``labels[-1]``. Export order is last-in-newest by
      Labelbox convention, so this is a best-effort approximation when
      timestamps are absent.
    """
    labels = proj.get("labels") or []
    if not labels:
        return None
    # Fall back to export order when any label is missing a timestamp.
    # (Export order: last-in == newest; deliberate best-effort fallback.)
    if not all(_created_at_of_label(lbl) for lbl in labels):
        return cast("JsonDict", labels[-1])
    return cast("JsonDict", max(labels, key=_created_at_of_label))


def _created_at_of_label(label: JsonDict) -> str:
    return (label.get("label_details") or {}).get("created_at") or ""


def _created_at(proj: JsonDict) -> str:
    label = _latest_label(proj)
    return _created_at_of_label(label) if label else ""


def _workflow_status(proj: JsonDict) -> str | None:
    details = proj.get("project_details") or {}
    status = details.get("workflow_status")
    if status is None:
        queue = details.get("task_queue_name") or details.get("task_queue_status")
        status = "Done" if queue == "Done" else queue
    return status


def _classification_value(cls: JsonDict) -> tuple[str, str]:
    if cls.get("radio_answer"):
        answer = cls["radio_answer"]
        return "radio", answer.get("value") or answer.get("name") or ""
    if cls.get("checklist_answers") is not None:
        values = [a.get("value") or a.get("name") or "" for a in cls["checklist_answers"]]
        return "checklist", ";".join(v for v in values if v)
    if cls.get("text_answer") is not None:
        return "text", (cls["text_answer"] or {}).get("content") or ""
    return "unknown", ""


def _object_geometry(obj: JsonDict) -> tuple[str, str]:
    for kind in _GEOMETRY_KINDS:
        geom = obj.get(kind)
        if geom is None:
            continue
        if kind == "mask":
            return "mask", (geom or {}).get("url") or ""
        return kind, json.dumps(geom, sort_keys=True)
    return "unknown", ""
