// Explicit test inputs for the ledger review tests. These are fixtures, not reviews: nothing here is an approval.
import type { AssetRow, Evidence, LedgerSource } from "./types";

export const PHOTO: Evidence = { url: "/api/evidence/scan17_look0_7.jpg", creator: "kev", captured_at: "2025-07-11T00:00:00Z",
  link: "https://www.mapillary.com/app/?pKey=7", license: "CC BY-SA 4.0", blurred: true };

export const NOW = "2026-11-04T12:00:00.000Z";

export function source(over: Partial<LedgerSource> = {}): LedgerSource {
  return { version: 1, layer: "curb_ramps.geojson", record_key: "fid:12", record_id: "RAMP-12", properties_sha256: "a".repeat(64),
    review_key: "rk-ramp-12-" + "b".repeat(20),
    editable_fields: { HAS_DETECTABLE_WARNING: { before: "No", yes: "Yes", no: "No", question: "Is a detectable warning surface present?", suggested: "Yes" } },
    identity_warnings: [], ...over };
}

export function row(over: Partial<AssetRow> = {}, src: LedgerSource | null = source()): AssetRow {
  return { item: "curb_ramp", asset: "Curb ramp", city_list: "SDOT curb ramps", city_record: "RAMP-12", city_location: "1 Main St",
    status: "Seen in photo", how_seen: "Survey", models_unsure: "", city_date: "2019-03",
    seen_in_photo_on: "2025-07-11", photo_newer_than_city_date: "yes", city_condition: "", lat: 47.6, lng: -122.3,
    position_source: "City record", position_accuracy: "City record", box: [100, 200, 300, 400], evidence: PHOTO,
    action: "Update the date", city_says: "HAS_DETECTABLE_WARNING No", photo_shows: "Seen in a photo of 2025-07-11",
    ...(src ? { ledger_source: src } : {}), ...over };
}

// A tree: the city list has no attribute a photo can answer, so only an observation or a hold is possible.
export function treeRow(n = 53943, warnings: string[] = []): AssetRow {
  return row({ item: "street_tree", asset: "Street tree", city_record: `TRE-${n}`, city_list: "SDOT street trees" },
    source({ layer: "street_trees.geojson", record_key: `fid:${n}`, record_id: `TRE-${n}`, review_key: `rk-tre-${n}-` + "c".repeat(20),
      editable_fields: {}, identity_warnings: warnings }));
}
