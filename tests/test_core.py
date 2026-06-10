"""Engine tests: latest-label selection, status normalization, generic flatten."""

from __future__ import annotations

from pathlib import Path

import pytest

from labelpull.core import (
    FeatureRow,
    flatten,
    read_export_file,
    summarize,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def species_rows() -> list[dict]:
    return read_export_file(FIXTURES / "species_export.ndjson")


@pytest.fixture
def boxes_rows() -> list[dict]:
    return read_export_file(FIXTURES / "boxes_masks_export.ndjson")


def _features(rows: list[dict], project_id: str) -> list[FeatureRow]:
    return [f for r in rows for f in flatten(r, project_id)]


def test_read_export_file_handles_ndjson_and_json_array(tmp_path: Path) -> None:
    ndjson = tmp_path / "a.ndjson"
    ndjson.write_text('{"x": 1}\n{"x": 2}\n')
    assert read_export_file(ndjson) == [{"x": 1}, {"x": 2}]
    arr = tmp_path / "b.json"
    arr.write_text('[{"x": 1}, {"x": 2}]')
    assert read_export_file(arr) == [{"x": 1}, {"x": 2}]
    empty = tmp_path / "c.json"
    empty.write_text("")
    assert read_export_file(empty) == []


def test_latest_label_wins_over_array_order(species_rows: list[dict]) -> None:
    # dr_2 has the annotator's "Apeiba membranacea" first, reviewer's correction second.
    feats = flatten(species_rows[1], "proj_x")
    taxa = [f.value for f in feats if f.feature_name == "Taxon"]
    assert taxa == ["Apeiba tibourbou"]  # reviewer's later label, not labels[0]
    assert all(f.labeled_by == "reviewer@bci.org" for f in feats)


def test_workflow_status_falls_back_to_task_queue(species_rows: list[dict]) -> None:
    # dr_3 has no workflow_status, only task_queue_name == "Done".
    feats = flatten(species_rows[2], "proj_x")
    assert feats  # reached + labelled (empty annotations) still yields the sentinel
    assert {f.workflow_status for f in feats} == {"Done"}
    assert [f.feature_kind for f in feats] == ["label"]  # no classifications/objects


def test_unlabelled_row_yields_nothing(species_rows: list[dict]) -> None:
    assert flatten(species_rows[3], "proj_x") == []  # dr_4 has no labels


def test_checklist_joined_and_radio_value(species_rows: list[dict]) -> None:
    feats = flatten(species_rows[0], "proj_x")
    by_name = {f.feature_name: f for f in feats if f.feature_kind != "label"}
    assert by_name["Taxon"].value == "Ficus insipida"
    assert by_name["Taxon"].feature_kind == "radio"
    assert by_name["Organs"].value == "leaf;flower"
    assert by_name["Organs"].feature_kind == "checklist"


def test_flatten_objects_and_nested_classifications(boxes_rows: list[dict]) -> None:
    feats = flatten(boxes_rows[0], "proj_y")
    kinds = {(f.feature_name, f.feature_kind) for f in feats}
    assert ("Caption", "text") in kinds
    assert ("Plant", "bounding_box") in kinds
    assert ("Canopy", "polygon") in kinds
    # Nested species radio is linked to its parent box feature_id.
    nested = next(f for f in feats if f.feature_name == "Species")
    assert nested.feature_kind == "radio"
    assert nested.value == "Cecropia"
    assert nested.parent_feature_id == "f_box1"


def test_flatten_mask_and_point(boxes_rows: list[dict]) -> None:
    feats = flatten(boxes_rows[1], "proj_y")
    by_name = {f.feature_name: f for f in feats if f.feature_kind != "label"}
    assert by_name["Leaf"].feature_kind == "mask"
    assert by_name["Leaf"].value == "https://api.labelbox.com/masks/abc.png"
    assert by_name["Tip"].feature_kind == "point"
    assert by_name["Tip"].value == '{"x": 7, "y": 8}'


def test_flatten_single_project_inferred_when_id_omitted(boxes_rows: list[dict]) -> None:
    assert flatten(boxes_rows[0]) == flatten(boxes_rows[0], "proj_y")


def test_flatten_ambiguous_multi_project_returns_empty() -> None:
    dr = {
        "data_row": {"id": "d", "global_key": "g"},
        "projects": {
            "a": {"labels": [{"label_details": {}, "annotations": {}}]},
            "b": {"labels": [{"label_details": {}, "annotations": {}}]},
        },
    }
    assert flatten(dr) == []  # two projects, none named -> no silent mixing


def test_summarize_counts(species_rows: list[dict]) -> None:
    feats = _features(species_rows, "proj_x")
    s = summarize(species_rows, feats)
    assert s.n_data_rows == 4
    assert s.n_labelled == 3  # dr_1, dr_2, dr_3 (dr_4 unlabelled)
    assert s.n_reached_unlabelled == 1
    assert s.statuses == {"InReview": 1, "Done": 2}
    assert s.feature_kinds["radio"] == 2
    assert s.latest_created_at == "2026-06-03T14:00:00Z"
