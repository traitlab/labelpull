"""Adapters: collapse ontology-agnostic :class:`FeatureRow` rows into a shape.

The generic path writes ``FeatureRow`` rows straight to a long-format CSV that
any project can read. An adapter narrows that to a project-specific wide record.
:class:`SpeciesAdapter` is the reference implementation, reproducing
speciesfirst's ``global_key,taxon,organs,labeled_by,workflow_status`` pull CSV
from the generic rows, so the engine has exactly one parser.
"""

from __future__ import annotations

import csv
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from labelpull.core import FeatureRow


@runtime_checkable
class Adapter(Protocol):
    """Map flattened features to named columns plus the rows to write."""

    columns: Sequence[str]

    def rows(self, features: Iterable[FeatureRow]) -> Iterable[Sequence[str]]: ...


class GenericAdapter:
    """One CSV row per feature: the ontology-agnostic long format."""

    columns: Sequence[str] = (
        "global_key",
        "data_row_id",
        "feature_kind",
        "feature_name",
        "value",
        "workflow_status",
        "labeled_by",
        "created_at",
        "parent_feature_id",
    )

    def rows(self, features: Iterable[FeatureRow]) -> Iterable[Sequence[str]]:
        for f in features:
            yield (
                f.global_key,
                f.data_row_id,
                f.feature_kind,
                f.feature_name,
                f.value,
                f.workflow_status or "",
                f.labeled_by or "",
                f.created_at or "",
                f.parent_feature_id,
            )


class SpeciesAdapter:
    """One row per ``global_key``: reproduces speciesfirst's pull CSV.

    ``taxon`` is the ``Taxon`` single-select radio; ``organs`` is the ``Organs``
    checklist (``;``-joined). A reached-and-labelled row with neither still
    appears (seeded by the ``label`` sentinel), matching speciesfirst's "reached
    but unlabelled yields ``taxon=''``" behaviour. Insertion order follows the
    export stream.
    """

    columns: Sequence[str] = ("global_key", "taxon", "organs", "labeled_by", "workflow_status")
    taxon_feature = "Taxon"
    organs_feature = "Organs"

    def rows(self, features: Iterable[FeatureRow]) -> Iterable[Sequence[str]]:
        by_key: OrderedDict[str, dict[str, str]] = OrderedDict()
        for f in features:
            rec = by_key.setdefault(
                f.global_key,
                {"taxon": "", "organs": "", "labeled_by": "", "workflow_status": ""},
            )
            if f.labeled_by:
                rec["labeled_by"] = f.labeled_by
            if f.workflow_status:
                rec["workflow_status"] = f.workflow_status
            if f.feature_kind == "radio" and f.feature_name == self.taxon_feature and f.value:
                rec["taxon"] = f.value
            elif f.feature_kind == "checklist" and f.feature_name == self.organs_feature:
                rec["organs"] = f.value
        for global_key, rec in by_key.items():
            yield (
                global_key,
                rec["taxon"],
                rec["organs"],
                rec["labeled_by"],
                rec["workflow_status"],
            )


ADAPTERS: dict[str, type] = {"generic": GenericAdapter, "species": SpeciesAdapter}


def write_csv(path: str | Path, adapter: Adapter, features: Iterable[FeatureRow]) -> Path:
    """Write ``features`` through ``adapter`` to ``path`` (parents created)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(adapter.columns)
        writer.writerows(adapter.rows(features))
    return path
