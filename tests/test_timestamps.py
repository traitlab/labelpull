"""Chronological contracts exercised through public callers, without the SDK."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from labelpull import export, flatten, summarize
from labelpull.cli import app


def _row(key: str, timestamps: list[str | None]) -> dict:
    return {
        "data_row": {"id": key, "global_key": key},
        "projects": {
            "p": {
                "labels": [
                    {"label_details": {"created_at": timestamp}, "annotations": {}}
                    for timestamp in timestamps
                ]
            }
        },
    }


class _Client:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.requested = False

    def get_project(self, project_id: str) -> SimpleNamespace:
        self.requested = True
        task = SimpleNamespace(
            wait_till_done=lambda: None,
            get_buffered_stream=lambda: [SimpleNamespace(json=row) for row in self.rows],
        )
        return SimpleNamespace(export=lambda **kwargs: task)


def _oracle(timestamp: str) -> datetime:
    """Independent stdlib parser, deliberately uses strptime rather than fromisoformat."""
    if len(timestamp) == 10:
        return datetime.strptime(timestamp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    pattern = "%Y-%m-%dT%H:%M:%S.%f%z" if "." in timestamp else "%Y-%m-%dT%H:%M:%S%z"
    return datetime.strptime(timestamp, pattern).astimezone(timezone.utc)


@pytest.mark.parametrize(
    "timestamps",
    [
        ["2026-06-01T12:00:00Z", "2026-06-01T12:00:00.100Z"],
        ["2026-06-01T12:00:00.100000Z", "2026-06-01T12:00:00.100001Z"],
        ["2026-06-01T12:00:00.12Z", "2026-06-01T12:00:00.12345Z"],
        ["2026-06-01T13:00:00+02:00", "2026-06-01T12:00:00Z"],
        ["2026-06-02T00:30:00+02:00", "2026-06-01T23:00:00Z"],
    ],
)
def test_latest_label_matches_datetime_oracle(timestamps: list[str]) -> None:
    expected = max(timestamps, key=_oracle)
    # Changing the export order must not change the chronological winner.
    for order in (timestamps, list(reversed(timestamps))):
        assert flatten(_row("g", order), "p")[0].created_at == expected


@pytest.mark.parametrize(
    "timestamps",
    [
        ["2026-06-01T12:00:00Z", "2026-06-01T07:00:00-05:00"],
        ["2026-06-01T12:00:00.1Z", "2026-06-01T12:00:00.100000Z"],
        ["2026-06-01", "2026-06-01T00:00:00Z"],
    ],
)
def test_same_instant_ties_preserve_first_label(timestamps: list[str]) -> None:
    assert _oracle(timestamps[0]) == _oracle(timestamps[1])
    for order in (timestamps, list(reversed(timestamps))):
        assert flatten(_row("g", order), "p")[0].created_at == order[0]


@pytest.mark.parametrize("missing", [None, ""])
def test_missing_timestamp_preserves_export_order_fallback(missing: str | None) -> None:
    timestamp = "2026-06-01T12:00:00Z"
    for timestamps in ([timestamp, missing], [missing, timestamp]):
        assert flatten(_row("g", timestamps), "p")[0].created_at == timestamps[-1]


_TIMESTAMPS = [
    "2026-05-31T23:59:59.999999Z",
    "2026-06-01T00:00:00Z",
    "2026-06-01T01:00:00+02:00",
    "2026-05-31T19:00:00-05:00",
    "2026-06-01T12:00:00Z",
    "2026-06-01T12:00:00.1Z",
    "2026-06-01T12:00:00.100001Z",
    "2026-06-01T07:00:00.100000-05:00",
    "2026-06-01T14:00:00.100001+02:00",
    "2026-06-01",
]


@pytest.mark.parametrize(
    "since",
    [
        "2026-06-01",
        "2026-06-01T12:00:00Z",
        "2026-06-01T07:00:00-05:00",
        "2026-06-01T12:00:00.1Z",
        "2026-06-01T12:00:00.100001Z",
    ],
)
def test_live_and_offline_since_match_datetime_oracle(tmp_path: Path, since: str) -> None:
    rows = [_row(str(i), [timestamp]) for i, timestamp in enumerate(_TIMESTAMPS)]
    rows += [_row("missing", [None]), _row("empty", [""]), _row("unlabelled", [])]
    # Also force filtering to choose among multiple labels before comparing the cutoff.
    rows.append(_row("reviewed", ["2026-06-01T12:00:00Z", "2026-06-01T12:00:00.1Z"]))
    expected = [str(i) for i, value in enumerate(_TIMESTAMPS) if _oracle(value) >= _oracle(since)]
    if _oracle("2026-06-01T12:00:00.1Z") >= _oracle(since):
        expected.append("reviewed")
    actual = list(export("p", since=since, client=_Client(rows)))
    assert [row["data_row"]["global_key"] for row in actual] == expected

    source = tmp_path / "input.json"
    source.write_text(json.dumps(rows))
    out = tmp_path / "out.csv"
    result = CliRunner().invoke(
        app, ["pull", "p", "--from-export", str(source), "--since", since, "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    with out.open(newline="") as stream:
        assert [row["global_key"] for row in csv.DictReader(stream)] == expected


@pytest.mark.parametrize(
    "since",
    [
        "",
        "nonsense",
        "2026-02-30",
        "2026-06-01T12:00:00",
        "2026-06-01T12:00:00+00:99",
        "2026-06-01T12:00:00+01:00:99",
        "2026-06-01T12:00:00+24:00",
        "20260601T120000.1234561Z",
        "2026-06-01T12:00.1234561Z",
        "2026-06-01T12:00:00.123456١Z",
    ],
)
@pytest.mark.parametrize("rows", [[], [_row("missing", [None])]])
def test_invalid_cutoff_fails_before_live_request_or_offline_output(
    tmp_path: Path, since: str, rows: list[dict]
) -> None:
    client = _Client(rows)
    with pytest.raises(ValueError, match="since"):
        list(export("p", since=since, client=client))
    assert not client.requested
    source = tmp_path / "input.json"
    source.write_text(json.dumps(rows))
    out = tmp_path / "out.csv"
    result = CliRunner().invoke(
        app, ["pull", "p", "--from-export", str(source), "--since", since, "-o", str(out)]
    )
    assert result.exit_code != 0
    assert "Invalid value" in result.output and "since" in result.output
    assert not out.exists()


@pytest.mark.parametrize(
    "timestamp",
    [
        "nonsense",
        "2026-02-30",
        "2026-06-01T12:00:00",
        "2026-06-01T12:00:00+00:00:00.123456",
        "2026-06-01T12:00:00.1234561+00:00:00.0000001",
        "2026-06-01T12:00:00+00:00.123456",
        "2026-06-01T12:00:00+05:30.1",
        "2026-06-01T12:00:00+05.123456",
        "2026-06-01T12:00:00+00:99",
        "2026-06-01T12:00:00+01:00:99",
        "2026-06-01T12:00:00+24:00",
        "20260601T120000.1234561Z",
        "2026-06-01T12:00.1234561Z",
    ],
)
@pytest.mark.parametrize("missing_prefix", [False, True])
def test_invalid_label_timestamp_fails_instead_of_guessing(
    timestamp: str, tmp_path: Path, missing_prefix: bool
) -> None:
    row = _row("g", [None, timestamp] if missing_prefix else [timestamp])
    with pytest.raises(ValueError, match="created_at"):
        flatten(row, "p")
    with pytest.raises(ValueError, match="created_at"):
        list(export("p", since="2026-06-01", client=_Client([row])))
    source = tmp_path / "input.json"
    source.write_text(json.dumps([row]))
    result = CliRunner().invoke(
        app, ["pull", "p", "--from-export", str(source), "--since", "2026-06-01"]
    )
    assert result.exit_code != 0
    assert "created_at" in str(result.exception)


def test_summary_latest_timestamp_is_chronological_and_keeps_original_spelling() -> None:
    rows = [_row(str(i), [timestamp]) for i, timestamp in enumerate(_TIMESTAMPS)]
    features = [feature for row in rows for feature in flatten(row, "p")]
    expected = max(_TIMESTAMPS, key=_oracle)
    assert summarize(rows, features).latest_created_at == expected


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        ("2026-06-01T12:00:00.12345601Z", "2026-06-01T12:00:00.1234561Z"),
        ("2026-06-01T07:00:00.1234561-05:00", "2026-06-01T12:00:00.1234562Z"),
        ("2026-06-01T12:00:00.00000000000000001Z", "2026-06-01T12:00:00.0000000000000001Z"),
    ],
)
def test_sub_microsecond_precision_is_never_truncated(
    earlier: str, later: str, tmp_path: Path
) -> None:
    assert flatten(_row("g", [later, earlier]), "p")[0].created_at == later
    rows = [_row("early", [earlier]), _row("late", [later])]
    assert list(export("p", since=later, client=_Client(rows))) == [rows[1]]
    assert summarize(rows, [f for r in rows for f in flatten(r, "p")]).latest_created_at == later
    source = tmp_path / "input.json"
    source.write_text(json.dumps(rows))
    out = tmp_path / "out.csv"
    result = CliRunner().invoke(
        app, ["pull", "p", "--from-export", str(source), "--since", later, "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    with out.open(newline="") as stream:
        assert [row["global_key"] for row in csv.DictReader(stream)] == ["late"]


def test_sub_microsecond_trailing_zeros_and_offsets_are_same_instant() -> None:
    first = "2026-06-01T12:00:00.1234561Z"
    second = "2026-06-01T07:00:00.1234561000-05:00"
    for order in ([first, second], [second, first]):
        row = _row("g", order)
        assert flatten(row, "p")[0].created_at == order[0]
        assert list(export("p", since=first, client=_Client([row]))) == [row]
        assert list(export("p", since=second, client=_Client([row]))) == [row]


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-06-01 12:00:00,1234561Z",
        "2026-06-01T12:00:30.1234561+00:00:30",
        "2026-06-01T11:59:30.1234561-00:00:30",
    ],
)
def test_documented_extended_forms_keep_exact_instants(timestamp: str) -> None:
    reference = "2026-06-01T12:00:00.1234561Z"
    for order in ([reference, timestamp], [timestamp, reference]):
        item = _row("g", order)
        assert flatten(item, "p")[0].created_at == order[0]
        assert list(export("p", since=reference, client=_Client([item]))) == [item]
        assert list(export("p", since=timestamp, client=_Client([item]))) == [item]


def test_year_boundaries_do_not_require_unrepresentable_utc_conversion() -> None:
    minimum = ["0001-01-01T00:00:00+01:00", "0001-01-01T00:00:00Z"]
    maximum = ["9999-12-31T23:59:59-01:00", "9999-12-31T23:59:59Z"]
    assert flatten(_row("g", minimum), "p")[0].created_at == minimum[1]
    assert flatten(_row("g", maximum), "p")[0].created_at == maximum[0]
