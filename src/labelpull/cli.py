"""``labelpull`` CLI: pull the latest Labelbox annotations to a tidy CSV.

labelpull pull PROJECT_ID -o labels.csv
labelpull pull PROJECT_ID --status Done --since 2026-06-01
labelpull pull PROJECT_ID --schema species -o taxa.csv
labelpull pull PROJECT_ID --from-export export.ndjson   # offline, no API key
"""

from __future__ import annotations

from pathlib import Path

import typer

from labelpull import __version__
from labelpull.adapters import ADAPTERS, write_csv
from labelpull.core import (
    FeatureRow,
    JsonDict,
    _created_at,
    _select_project,
    flatten,
    read_export_file,
    summarize,
)
from labelpull.core import export as live_export

app = typer.Typer(add_completion=False, help="Pull the latest Labelbox annotations to CSV.")


@app.callback()
def _main() -> None:
    """labelpull: pull the latest Labelbox annotations into a tidy table."""


@app.command()
def pull(
    project_id: str = typer.Argument(..., help="Labelbox project id to export from."),
    out: Path = typer.Option(
        Path("pulled_labels.csv"), "--out", "-o", help="Where to write the CSV."
    ),
    schema: str = typer.Option(
        "generic",
        help="generic = one row per feature (any ontology); "
        "species = speciesfirst Taxon/Organs wide CSV.",
    ),
    status: str | None = typer.Option(
        None, help="Filter by task-queue stage: ToLabel | InReview | InRework | Done."
    ),
    since: str | None = typer.Option(
        None, help="Keep only rows whose newest label was created on/after this ISO date/time."
    ),
    from_export: Path | None = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Flatten a saved export (JSON/NDJSON) offline instead of the live API.",
    ),
    api_key: str | None = typer.Option(None, help="Labelbox API key (else LABELBOX_API_KEY)."),
) -> None:
    """Export the latest annotations and flatten them to CSV, with a summary."""
    if schema not in ADAPTERS:
        raise typer.BadParameter(f"unknown schema {schema!r}; choose from {sorted(ADAPTERS)}")
    adapter = ADAPTERS[schema]()

    typer.echo(f"labelpull v{__version__}")
    if from_export is not None:
        rows = read_export_file(from_export)
        if since is not None:
            rows = [r for r in rows if _row_since(r, project_id, since)]
        typer.echo(f"  read {len(rows)} rows from {from_export}")
    else:
        rows = list(live_export(project_id, status=status, since=since, api_key=api_key))
        typer.echo(f"  exported {len(rows)} rows from project {project_id}")

    features = [f for r in rows for f in flatten(r, project_id)]
    _print_summary(rows, features)
    write_csv(out, adapter, features)
    typer.echo(f"wrote {schema} CSV: {out}")


def _row_since(dr: JsonDict, project_id: str, since: str) -> bool:
    return _created_at(_select_project(dr, project_id)) >= since


def _print_summary(rows: list[JsonDict], features: list[FeatureRow]) -> None:
    s = summarize(rows, features)
    typer.echo(
        f"  {s.n_labelled} labelled / {s.n_data_rows} rows "
        f"({s.n_reached_unlabelled} reached unlabelled)"
    )
    if s.statuses:
        typer.echo("  status: " + ", ".join(f"{k}={v}" for k, v in sorted(s.statuses.items())))
    if s.feature_kinds:
        typer.echo("  kinds:  " + ", ".join(f"{k}={v}" for k, v in sorted(s.feature_kinds.items())))
    if s.latest_created_at:
        typer.echo(f"  latest label: {s.latest_created_at}")


if __name__ == "__main__":  # pragma: no cover
    app()
