"""CLI + live-export-with-stub tests (no network, no API key)."""

from __future__ import annotations

import csv
from pathlib import Path

from typer.testing import CliRunner

from labelpull.cli import app
from labelpull.core import export, read_export_file

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


class _StubStream:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = [type("R", (), {"json": r})() for r in rows]

    def __iter__(self):
        return iter(self._rows)


class _StubTask:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def wait_till_done(self) -> None:
        pass

    def get_buffered_stream(self):
        return _StubStream(self._rows)


class _StubProject:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_filters: dict | None = None

    def export(self, params: dict, filters: dict | None) -> _StubTask:
        self.last_filters = filters
        return _StubTask(self._rows)


class _StubClient:
    def __init__(self, rows: list[dict]) -> None:
        self._project = _StubProject(rows)

    def get_project(self, project_id: str) -> _StubProject:
        return self._project


def test_export_streams_via_injected_client() -> None:
    rows = read_export_file(FIXTURES / "species_export.ndjson")
    client = _StubClient(rows)
    out = list(export("proj_x", status="Done", client=client))
    assert out == rows
    assert client._project.last_filters == {"workflow_status": "Done"}


def test_export_since_filters_on_latest_label() -> None:
    rows = read_export_file(FIXTURES / "species_export.ndjson")
    out = list(export("proj_x", since="2026-06-02", client=_StubClient(rows)))
    keys = [r["data_row"]["global_key"] for r in out]
    # Only photo_b (latest label 2026-06-03) clears the 2026-06-02 floor.
    assert keys == ["photo_b.JPG"]


def test_cli_pull_offline_generic(tmp_path: Path) -> None:
    out = tmp_path / "labels.csv"
    result = runner.invoke(
        app,
        [
            "pull",
            "proj_y",
            "--from-export",
            str(FIXTURES / "boxes_masks_export.ndjson"),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "labelpull v" in result.output
    assert "kinds:" in result.output
    with out.open(newline="") as f:
        header = next(csv.reader(f))
    assert header[0] == "global_key" and "feature_kind" in header


def test_cli_pull_offline_species(tmp_path: Path) -> None:
    out = tmp_path / "taxa.csv"
    result = runner.invoke(
        app,
        [
            "pull",
            "proj_x",
            "--schema",
            "species",
            "--from-export",
            str(FIXTURES / "species_export.ndjson"),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    with out.open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["global_key", "taxon", "organs", "labeled_by", "workflow_status"]
    assert any(r[1] == "Apeiba tibourbou" for r in rows[1:])


def test_cli_unknown_schema_errors() -> None:
    result = runner.invoke(
        app,
        ["pull", "p", "--schema", "nope", "--from-export", str(FIXTURES / "species_export.ndjson")],
    )
    assert result.exit_code != 0


# --- Fix 3: CLI wording ------------------------------------------------------


def test_cli_summary_says_without_labels_not_reached_unlabelled(tmp_path: Path) -> None:
    """CLI output uses 'without labels' (not 'reached unlabelled') for unlabelled count."""
    out = tmp_path / "labels.csv"
    result = runner.invoke(
        app,
        [
            "pull",
            "proj_x",
            "--from-export",
            str(FIXTURES / "species_export.ndjson"),
            "-o",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "without labels" in result.output
    assert "reached unlabelled" not in result.output


# --- Fix 4: --status validation ----------------------------------------------


def test_cli_invalid_status_exits_nonzero_with_helpful_message() -> None:
    """--status bogus exits non-zero and includes the valid choices in the message."""
    result = runner.invoke(
        app,
        [
            "pull",
            "p",
            "--status",
            "bogus",
            "--from-export",
            str(FIXTURES / "species_export.ndjson"),
        ],
    )
    assert result.exit_code != 0
    combined = (result.output or "") + str(result.exception or "")
    assert "ToLabel" in combined or "must be one of" in combined
