"""Adapter tests: generic long CSV + the species reference adapter."""

from __future__ import annotations

import csv
from pathlib import Path

from labelpull.adapters import ADAPTERS, GenericAdapter, SpeciesAdapter, write_csv
from labelpull.core import flatten, read_export_file

FIXTURES = Path(__file__).parent / "fixtures"


def _features(name: str, project_id: str) -> list:
    rows = read_export_file(FIXTURES / name)
    return [f for r in rows for f in flatten(r, project_id)]


def _read(path: Path) -> list[list[str]]:
    with path.open(newline="") as f:
        return list(csv.reader(f))


def test_registry_keys() -> None:
    assert set(ADAPTERS) == {"generic", "species"}


def test_generic_long_csv_roundtrips_every_feature(tmp_path: Path) -> None:
    feats = _features("boxes_masks_export.ndjson", "proj_y")
    out = write_csv(tmp_path / "g.csv", GenericAdapter(), feats)
    rows = _read(out)
    assert rows[0] == list(GenericAdapter.columns)
    assert len(rows) - 1 == len(feats)  # one CSV row per feature row
    # The nested species radio carries its parent box id in the long format.
    species = [r for r in rows if r[3] == "Species"][0]
    assert species[2] == "radio" and species[4] == "Cecropia" and species[8] == "f_box1"


def test_species_adapter_wide_csv(tmp_path: Path) -> None:
    feats = _features("species_export.ndjson", "proj_x")
    out = write_csv(tmp_path / "s.csv", SpeciesAdapter(), feats)
    rows = _read(out)
    assert rows[0] == ["global_key", "taxon", "organs", "labeled_by", "workflow_status"]
    body = {r[0]: r for r in rows[1:]}
    # photo_a: InReview, leaf;flower
    assert body["photo_a.JPG"] == ["photo_a.JPG", "Ficus insipida", "leaf;flower", "ann@bci.org", "InReview"]
    # photo_b: reviewer's corrected taxon + fruit, Done
    assert body["photo_b.JPG"] == ["photo_b.JPG", "Apeiba tibourbou", "fruit", "reviewer@bci.org", "Done"]
    # photo_c: reached + labelled but empty -> taxon "", still present
    assert body["photo_c.JPG"] == ["photo_c.JPG", "", "", "ann@bci.org", "Done"]
    # photo_d: unlabelled -> absent
    assert "photo_d.JPG" not in body
    # insertion order follows the export stream
    assert [r[0] for r in rows[1:]] == ["photo_a.JPG", "photo_b.JPG", "photo_c.JPG"]
