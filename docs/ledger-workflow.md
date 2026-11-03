# Ledger review workflow: from a reviewed list to a new copy of a city's GeoJSON

Fieldless compares what its photos show with a city's public record lists and proposes updates. A person can review each proposal in the app (**Review for ledger**) and take the result away as one file. This guide explains what that file is, how to check it against the city's *current* GeoJSON without changing anything, how to produce a separate revised copy, and who decides what happens next.

Nothing in this workflow writes to a city system. The app makes the files in your browser; the command line tool reads your local GeoJSON and writes only a new folder.

## What you take away: the review package

The app's **Handoff package** panel offers one UTF-8 JSON file, `ledger-review-scan<N>-package.json`:

```json
{
  "schema_version": 1,
  "kind": "fieldless-ledger-review",
  "scan_id": 30,
  "source": { "...": "the whole Asset Check the reviewer worked on" },
  "journal": { "schema_version": 1, "scan_id": 30, "decisions": [ "..." ] },
  "manifest": {
    "notice": "Prepared only; not applied to an official ledger.",
    "reviewer_assertion": "Self-asserted; not authenticated municipal approval."
  },
  "files": { "changes_csv": "...", "observations_csv": "..." }
}
```

- **`source`** is the survey's list of city records with, for each record, a snapshot of the record as the city's file held it (its ID, a hash of all its original properties, a hash of its exact position, the one yes/no field a photo may propose to change).
- **`journal`** is every decision the reviewer recorded: `change` (propose the city's other yes/no value), `observe` (record what a dated photo showed; no value changes) or `hold` (decide later). Each decision carries the reviewer's name and reason.
- **`files`** hold the same two CSV files the app also downloads separately, for a person who opens the package in a spreadsheet. They are a convenience only. The tool rebuilds both from `source` and `journal` and **refuses the package if they differ**; the CSV text is never read as instructions. The manifest must say exactly what is shown above, or the package is refused.

A package is **not signed**. It does not prove who made it. Reviewer names are typed into the app by the reviewer and are self-asserted statements, not authenticated municipal approvals. A photo's date is the photo's date: it is never written into a city's inspection or verification date field.

The three separate downloads (journal, source list, CSV files) remain available; the package is the same content in one file.

## What the preview in the app shows, and what it does not

The panel shows, before you download, each proposed change as the record, the field, its value **Before** and **After**, and counts of recorded decisions, changes, observations and holds.

This is a preview of the **proposal prepared in this browser**, built from the list and the journal. It is **not** a validation of the city's current GeoJSON. The city's files may have changed since the scan; the browser has not seen them. Checking them is the job of the dry run below.

## Check the package against your current GeoJSON (dry run)

You need a checkout of this repository (Python 3.14, the version it is developed and tested on) and an authorized local copy of the city's current GeoJSON layers.

```bash
python -m eval.ledger_apply --package ledger-review-scan30-package.json \
  --input-dir path/to/current-city-layers \
  --output-dir path/to/new-reviewed-copy \
  --city seattle-ballard \
  --dry-run
```

A dry run makes every check of a real run and prints what would be applied (each change as record, field, old value and new value; each observation and hold). It creates **no folder and no file**, not even the output folder, and never touches the input folder. If it exits `0`, a real run with the same arguments will pass the same checks, unless the files change in between or the disk is full. If it exits `2`, it prints each reason and nothing was written.

`--package FILE` is the alternative to `--check FILE --journal FILE`, which still work with the three separate downloads. Give one or the other: mixing `--package` with `--check` or `--journal` is refused.

## Create the new copy

Run the same command without `--dry-run`. The tool writes a **new folder** that must not exist yet. It builds the copy in a staging folder next to it, verifies it, and renames it into place. An existing output is never overwritten and the input folder is never written.

The new folder holds:

- every layer named by the city file: untouched layers byte for byte, and a layer with a change written again as compact JSON (every record, key order, number and character is kept; only white space differs, so compare layers by record, not by line);
- `_ledger_review/audit.json` (what changed, who said so, hashes of every input and output file), `changes.csv`, `observations.csv` and `journal.json`.

Exit `0`: the folder was written. Exit `2`: something was refused and nothing was written.

## Which GeoJSON and which cities are supported

The tool does not guess at a city's layout. A **city file** shipped in `eval/cities/` states, per survey item, the layer file, the record ID field and, where a photo can settle it, the one yes/no field with its two raw values. `--city NAME` selects one by name (a plain name, never a path); the item, layer, ID key and field a change may touch come from that file, not from the package. A package edited to offer another field or other values is refused, and so is a package with changes and no `--city`.

| `--city` | Layer layout under `--input-dir` | Changes possible |
|---|---|---|
| `seattle-ballard` | the Ballard files: `poles.geojson`, `street_trees.geojson`, `litter_cans.geojson`, `pay_stations.geojson`, `curb_ramps.geojson` | utility pole street light (`IS_STREETLIGHT`, `YES`/`NO`), curb ramp warning surface (`DETWARN_EXISTS`, `Y`/`N`) |
| `seattle` | the whole-city layout (`assets/poles.geojson`, `assets/trees.geojson`, ...; curb ramps in `ramps_capitolhill.geojson`) | the same two fields |
| `nyc`, `seoul` | as their city files state | none: observations and holds only |

The current layer files must be:

- UTF-8 (a leading byte-order mark is accepted) GeoJSON `FeatureCollection`s with no `NaN` or `Infinity`;
- laid out at the paths the city file names, inside `--input-dir` (a link that leads outside the folder is refused);
- records with the city file's ID field, each ID found **exactly once** in its layer.

A package of observations and holds alone can be checked without `--city`; every `*.geojson` under the input folder is then a candidate.

## When the source does not match

Fieldless proposes a change only against the record it saw. If the city's file is no longer that record, the run is refused with a reason, and nothing is written:

- the record's ID is missing, or appears more than once, in the current layer;
- any of the record's original properties changed since the scan (the property hash differs);
- the record's exact position changed (the position hash differs);
- the value *before* the change differs from the one the reviewer saw;
- the layer is missing, found twice, not a `FeatureCollection`, or outside the input folder;
- the package's scan ID, review keys or source snapshot do not match each other, or a convenience CSV or the manifest was edited;
- the field, values, item or layer are not the ones the city file allows (dates, IDs and positions are never editable);
- the output folder already exists, or is inside or around the input folder.

To resolve a mismatch, re-scan or re-review against the current record. The tool never merges or guesses.

## Approval and import are the institution's

The revised copy is a **prepared proposal**. It is not approved, and Fieldless has no way to approve anything for a city. The city's authorized operator decides whether and how to bring any change into the official ledger, using its own identity checks, review and import process: compare the audit and the CSV files with the original, verify the objects on site where the city requires it, and treat the reviewer names as the reviewers' own statements. Observations are evidence that a dated photo showed something; they are not inspections.

No city API, database or account is used, and none is needed.
