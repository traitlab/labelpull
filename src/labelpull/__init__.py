"""labelpull: pull the latest Labelbox annotations into a tidy table.

The Labelbox SDK exports a project's labels as nested, ontology-shaped JSON.
labelpull is the thin layer the SDK lacks: a generic flattener
(:func:`~labelpull.core.flatten`) plus the correctness logic (latest-label
selection, status normalization) and a one-command CLI on top.
"""

from __future__ import annotations

from labelpull.adapters import (
    ADAPTERS,
    Adapter,
    GenericAdapter,
    SpeciesAdapter,
    write_csv,
)
from labelpull.core import (
    WORKFLOW_STATUSES,
    FeatureRow,
    Summary,
    export,
    flatten,
    read_export_file,
    summarize,
)

__version__ = "0.1.0"

__all__ = [
    "ADAPTERS",
    "WORKFLOW_STATUSES",
    "Adapter",
    "FeatureRow",
    "GenericAdapter",
    "SpeciesAdapter",
    "Summary",
    "__version__",
    "export",
    "flatten",
    "read_export_file",
    "summarize",
    "write_csv",
]
