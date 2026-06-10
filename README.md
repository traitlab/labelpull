# labelpull

Pull the latest Labelbox annotations into a tidy, ontology-agnostic table.

The Labelbox SDK already exports a project's labels and streams them. What it
doesn't give you is a *tabular* view of that deeply nested JSON, the correctness
logic to pick the right label when a row was reviewed, or a workflow status that
is always populated. `labelpull` is exactly that thin layer on top of the SDK.

## Install

```bash
pip install labelpull            # offline parsing + CLI
pip install 'labelpull[live]'    # + the Labelbox SDK for live pulls
```

## CLI

```bash
export LABELBOX_API_KEY=...
labelpull pull <PROJECT_ID> -o labels.csv               # generic long CSV (any ontology)
labelpull pull <PROJECT_ID> --status Done               # only verified rows
labelpull pull <PROJECT_ID> --since 2026-06-01          # only the latest labels
labelpull pull <PROJECT_ID> --from-export export.ndjson # offline, no API key
labelpull pull <PROJECT_ID> --schema species -o taxa.csv # speciesfirst Taxon/Organs wide CSV
```

`--schema generic` (default) writes one row per feature — every classification
and object, any ontology:

```
global_key,data_row_id,feature_kind,feature_name,value,workflow_status,labeled_by,created_at,parent_feature_id
```

## Library

```python
import labelpull

rows = list(labelpull.export("proj_id", status="Done"))   # or read_export_file("export.ndjson")
features = [f for r in rows for f in labelpull.flatten(r, "proj_id")]
labelpull.write_csv("labels.csv", labelpull.GenericAdapter(), features)
print(labelpull.summarize(rows, features))
```

`flatten()` handles radio / checklist / text classifications and bbox / polygon /
line / point / mask objects (with nested classifications linked to their parent),
and always selects the most recently created label so a QC-reviewed row reports
the reviewer's answer, not the annotator's.

Write your own `Adapter` to collapse features into a project-specific wide table;
`SpeciesAdapter` is the reference implementation.
