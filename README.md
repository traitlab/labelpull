# labelpull

Pull the latest Labelbox annotations into a tidy, ontology-agnostic table.

The Labelbox SDK already exports a project's labels and streams them. What it
doesn't give you is a *tabular* view of that deeply nested JSON, the correctness
logic to pick the right label when a row was reviewed, or a workflow status that
is always populated. `labelpull` is exactly that thin layer on top of the SDK.

## Quick start

```bash
pip install 'labelpull[live]'
export LABELBOX_API_KEY=...                 # Labelbox → Workspace settings → API keys
labelpull pull <PROJECT_ID> -o labels.csv   # <PROJECT_ID> is in your project's URL
```

You get one row per annotation (any ontology):

```
global_key,data_row_id,feature_kind,feature_name,value,workflow_status,labeled_by,created_at,parent_feature_id
photo_001.jpg,clz…,label,,,Done,bot@lab.org,2026-06-05T08:00:00Z,
photo_001.jpg,clz…,radio,Species,Ficus insipida,Done,bot@lab.org,2026-06-05T08:00:00Z,
photo_001.jpg,clz…,checklist,Organs,leaf;flower,Done,bot@lab.org,2026-06-05T08:00:00Z,
```

> The first row of each photo (`feature_kind=label`, empty `feature_name`) is a
> marker that the photo *was reached and labelled* — it carries who/when even
> when the annotator left it blank, so empty-but-labelled photos still show up.
> Ignore it if you only want answers: filter to `feature_kind != "label"`.

## Install

```bash
pip install labelpull          # offline parsing + CLI (no SDK)
pip install 'labelpull[live]'  # + the Labelbox SDK, for live pulls from the API
```

## CLI

```bash
labelpull pull <PROJECT_ID> -o labels.csv               # everything, generic long CSV
labelpull pull <PROJECT_ID> --status Done               # only verified rows
labelpull pull <PROJECT_ID> --since 2026-06-01          # only labels created since a date
labelpull pull <PROJECT_ID> --from-export export.ndjson # offline: a UI "Export" file, no API key
```

`--status` takes `ToLabel | InReview | InRework | Done`. Every run prints a
summary (rows, labelled count, feature kinds, latest label timestamp).

`--since` compares instants, including fractional seconds and UTC offsets. A date such as `2026-06-01` means midnight UTC; a datetime uses `YYYY-MM-DDTHH:MM:SS` (a space may replace `T`), optional dot/comma fractional seconds, and `Z` or a signed `HH:MM` offset such as `-05:00` (whole `:SS` offset seconds are also supported). Invalid values, out-of-range offset components, compact/reduced-time forms, datetimes without an offset, and fractional UTC offsets raise an error. Rows with no selected label timestamp are excluded by `--since`. Label selection uses the newest timestamp, keeping the first label for equal instants; if any label lacks a timestamp, it retains the last label in export order as a best-effort fallback. Timestamp strings in the output keep their original spelling.

If your project is a single-classification task and you want one row per item
instead of the long format, filter the CSV to your feature (e.g. keep
`feature_name == "Species"`), or write a 10-line `Adapter` (see below).

## Library

```python
import labelpull

rows = list(labelpull.export("proj_id", status="Done"))   # live; needs labelpull[live]
# or, offline from a UI export:
# rows = labelpull.read_export_file("export.ndjson")

features = [f for r in rows for f in labelpull.flatten(r, "proj_id")]
labelpull.write_csv("labels.csv", labelpull.GenericAdapter(), features)
print(labelpull.summarize(rows, features))
```

`flatten()` handles radio / checklist / text classifications and bbox / polygon /
line / point / mask objects (with nested classifications linked to their parent),
and always selects the most recently created label so a QC-reviewed row reports
the reviewer's answer, not the annotator's.

## Custom output shape

`GenericAdapter` (the default) writes one row per feature. To collapse features
into a project-specific wide table, write an `Adapter` — given the flattened
`FeatureRow`s, yield your own columns. `SpeciesAdapter` is a worked example
(it pivots a `Taxon` radio + `Organs` checklist into one row per photo):

```bash
labelpull pull <PROJECT_ID> --schema species -o taxa.csv
```
