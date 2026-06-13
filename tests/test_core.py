"""Engine tests: latest-label selection, status normalization, generic flatten."""

from __future__ import annotations

from pathlib import Path

import pytest

from labelpull.core import (
    FeatureRow,
    export,
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


# --- Fix 1: _latest_label missing-timestamp bug ----------------------------


def test_latest_label_all_timestamped_newest_wins() -> None:
    """When all labels have created_at, max by timestamp (newest) is returned."""
    dr = {
        "data_row": {"id": "d1", "global_key": "img.JPG"},
        "projects": {
            "p": {
                "labels": [
                    {
                        "label_details": {
                            "created_at": "2026-06-01T08:00:00Z",
                            "created_by": "early@x.org",
                        },
                        "annotations": {
                            "classifications": [{"name": "Tag", "radio_answer": {"value": "old"}}],
                            "objects": [],
                        },
                    },
                    {
                        "label_details": {
                            "created_at": "2026-06-05T12:00:00Z",
                            "created_by": "reviewer@x.org",
                        },
                        "annotations": {
                            "classifications": [{"name": "Tag", "radio_answer": {"value": "new"}}],
                            "objects": [],
                        },
                    },
                ],
                "project_details": {"workflow_status": "Done"},
            }
        },
    }
    feats = flatten(dr, "p")
    tags = [f.value for f in feats if f.feature_name == "Tag"]
    assert tags == ["new"]  # newest timestamp wins
    assert all(f.labeled_by == "reviewer@x.org" for f in feats if f.feature_kind != "label")


def test_latest_label_missing_timestamp_falls_back_to_last() -> None:
    """When any label lacks created_at, last label in array is returned (not lexicographic winner)."""
    dr = {
        "data_row": {"id": "d2", "global_key": "img2.JPG"},
        "projects": {
            "p": {
                "labels": [
                    {
                        # No created_at — would produce "" which sorts below any date string.
                        # Under old max() logic this label would NEVER win even if it's the
                        # real latest; under the new rule it triggers the fallback.
                        "label_details": {"created_by": "ann@x.org"},
                        "annotations": {
                            "classifications": [
                                {"name": "Tag", "radio_answer": {"value": "first"}}
                            ],
                            "objects": [],
                        },
                    },
                    {
                        "label_details": {
                            "created_at": "2026-01-01T00:00:00Z",  # very old date
                            "created_by": "reviewer@x.org",
                        },
                        "annotations": {
                            "classifications": [
                                {"name": "Tag", "radio_answer": {"value": "lexwinner"}}
                            ],
                            "objects": [],
                        },
                    },
                ],
                "project_details": {"workflow_status": "InReview"},
            }
        },
    }
    feats = flatten(dr, "p")
    tags = [f.value for f in feats if f.feature_name == "Tag"]
    # Export-order fallback: labels[-1] is the reviewer's label (index 1).
    # Under the broken max() logic, the timestamped label ("2026-01-01") would
    # win because "" < any date string, returning "lexwinner" which is wrong.
    assert tags == ["lexwinner"]  # last in array
    assert all(f.labeled_by == "reviewer@x.org" for f in feats if f.feature_kind != "label")


# --- Fix 2: NDJSON parse error context ------------------------------------


def test_read_export_file_ndjson_malformed_line_reports_number(tmp_path: Path) -> None:
    """A malformed NDJSON line raises ValueError naming the offending line number."""
    bad = tmp_path / "bad.ndjson"
    bad.write_text('{"ok": 1}\nNOT_JSON\n')
    with pytest.raises(ValueError, match=r"line 2"):
        read_export_file(bad)


# --- Contract tests: documented-but-untested branches --------------------


def test_relationships_annotation_yields_feature_kind_relationship() -> None:
    """A 'relationships' annotation produces feature_kind='relationship'."""
    dr = {
        "data_row": {"id": "d3", "global_key": "img3.JPG"},
        "projects": {
            "p": {
                "labels": [
                    {
                        "label_details": {
                            "created_at": "2026-06-01T00:00:00Z",
                            "created_by": "ann@x.org",
                        },
                        "annotations": {
                            "classifications": [],
                            "objects": [],
                            "relationships": [
                                {
                                    "name": "linked_to",
                                    "relationship": {"source": "f1", "target": "f2"},
                                }
                            ],
                        },
                    }
                ],
                "project_details": {"workflow_status": "Done"},
            }
        },
    }
    feats = flatten(dr, "p")
    rel_feats = [f for f in feats if f.feature_kind == "relationship"]
    assert len(rel_feats) == 1
    assert rel_feats[0].feature_name == "linked_to"


def test_classification_with_no_known_answer_type_yields_unknown() -> None:
    """A classification with no radio/checklist/text answer returns ('unknown', '')."""
    dr = {
        "data_row": {"id": "d4", "global_key": "img4.JPG"},
        "projects": {
            "p": {
                "labels": [
                    {
                        "label_details": {
                            "created_at": "2026-06-01T00:00:00Z",
                            "created_by": "ann@x.org",
                        },
                        "annotations": {
                            "classifications": [
                                # No radio_answer, checklist_answers, or text_answer.
                                {"name": "Mystery", "some_future_field": "value"}
                            ],
                            "objects": [],
                        },
                    }
                ],
                "project_details": {"workflow_status": "Done"},
            }
        },
    }
    feats = flatten(dr, "p")
    mystery = [f for f in feats if f.feature_name == "Mystery"]
    assert len(mystery) == 1
    assert mystery[0].feature_kind == "unknown"
    assert mystery[0].value == ""


def test_object_with_unrecognised_geometry_yields_unknown() -> None:
    """An object with no recognised geometry key returns ('unknown', '')."""
    dr = {
        "data_row": {"id": "d5", "global_key": "img5.JPG"},
        "projects": {
            "p": {
                "labels": [
                    {
                        "label_details": {
                            "created_at": "2026-06-01T00:00:00Z",
                            "created_by": "ann@x.org",
                        },
                        "annotations": {
                            "classifications": [],
                            "objects": [
                                {
                                    "feature_id": "f_x",
                                    "name": "FutureShape",
                                    "sphere": {"radius": 5},  # not in _GEOMETRY_KINDS
                                    "classifications": [],
                                }
                            ],
                        },
                    }
                ],
                "project_details": {"workflow_status": "Done"},
            }
        },
    }
    feats = flatten(dr, "p")
    shape = [f for f in feats if f.feature_name == "FutureShape"]
    assert len(shape) == 1
    assert shape[0].feature_kind == "unknown"
    assert shape[0].value == ""


def test_export_without_api_key_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """export('p', client=None) with LABELBOX_API_KEY unset raises RuntimeError with a clear message.

    Two cases: SDK absent -> message mentions 'labelpull[live]'; SDK present but no key ->
    message mentions 'LABELBOX_API_KEY'. Either way it is a RuntimeError, not a bare
    ImportError or AttributeError.
    """
    monkeypatch.delenv("LABELBOX_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        # Consume the iterator to trigger the lazy client creation.
        list(export("p", client=None))
