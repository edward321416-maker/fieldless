// JSON shapes of the Threshold API (server/views.py and server/app.py).

export type Bbox = [number, number, number, number]; // min_lng, min_lat, max_lng, max_lat

export interface ServerConfig {
  invite_required: boolean;
  max_bbox_deg2: number;
  coverage_tiles?: boolean; // the server passes Mapillary's coverage tiles on (/api/mapillary/tiles); not the static demo
  items?: boolean; // the server keeps items the agent wrote in the app (plan 12)
  make_items?: boolean; // and makes new ones, with an invite code
  report?: boolean; // a finished survey has its results report for the city (docs/specs/2026-10-29-survey-report-design.md)
}

export interface Preview {
  photos: number;
  sequences: number;
  captured_from: string | null;
  captured_to: string | null;
  est_cost_usd: number;
  walks?: [number, number][][]; // the walks the survey would follow, [lng, lat]
  standard?: string;
  items?: ItemMeta[]; // what the survey would look for
  cap_usd?: number; // the survey's spending cap
}

export interface CheckInfo {
  id: string;
  find: string;
  target: string; // the noun the survey is about; `find` is SAM's phrase and may differ
  question: string; // the check's `ask` sentence (pipeline/standards)
  note: string | null; // the model's own one-line reason
  answer: "yes" | "no" | "unclear";
  report_when: "yes" | "no" | ""; // "" for an inventory check
  label_yes: string;
  label_no: string;
  inventory: boolean; // a confirmed object is an asset point, not a defect
  model: string; // who answered, e.g. "Muse Spark" or "Muse Glimmer (open weights, served on Modal)"
  zoomed?: boolean; // asked once more on the marked box enlarged, after a first answer of unclear (main 7df850c)
  first?: { answer: string | null; note: string | null } | null; // that first answer
}

export interface CheckCount {
  id: string;
  find: string;
  target: string;
  report_when: "yes" | "no" | ""; // the answer that is the defect; "" for an inventory check
  label_yes: string;
  label_no: string;
  inventory: boolean;
  map_icon: string;
  question: string; // a short question for people; the check's ask sentence when the standard gives none
  yes: number;
  no: number;
  unclear: number;
}

// Every kind of finding a scan can show, in the order the map colours them (server/views.py items_json).
export interface ItemMeta {
  id: string;
  target: string;
  map_icon: string; // a shape of the web icon set; empty: the target's first letter
  kind: "check" | "stage";
  inventory: boolean;
  added_by?: "agent"; // written by the agent in the app, not in the survey standard yet
}

// An item the agent wrote in the app, as /api/items/{id} gives it once done (server.views.item_run_json).
export interface AgentItem extends ItemMeta {
  added_by: "agent";
  find: string; // the phrase SAM 3.1 looks for
  question: string;
  label_yes: string;
  label_no: string;
  yes: number;
  no: number;
  unclear: number;
}

export interface ItemTest {
  labels: "model"; // the answers it was tested on came from another model, never a person
  judge?: string; // that model, "Muse Spark Open"
  layer: string | null; // the city's layer, when its list helped choose the photos
  phrase: string;
  ask: string;
  calls: number;
  found: [number, number]; // photos showing it where SAM found the phrase, of all photos showing it
  kept?: [number, number]; // photos showing it the item keeps, of all
  false?: [number, number]; // photos without it the item keeps wrongly, of all
  city?: { layer: string; found: number; covered: number }; // its rows against the city's records in view
}

export interface ItemRunPhoto {
  idx: number;
  url: string;
  image_id: string;
  creator: string | null;
  captured_at: string | null;
  link: string;
}

export interface ItemRun {
  id: number;
  scan_id: number;
  target: string;
  status: "queued" | "running" | "labels_needed" | "done" | "failed";
  progress: string[]; // what happened, a sentence a step
  photos: ItemRunPhoto[]; // the photos a person is asked about, while labels_needed
  item: AgentItem | null;
  issues: Issue[];
  cost_usd: number;
  error: string | null;
  test: ItemTest | null;
  created_at: string;
}

export interface ItemRunSummary {
  id: number;
  target: string;
  status: string;
  created_at: string;
}

// The offline comparison with the city's own records (eval, city_check_scan<N>.json), demo surveys only.
export interface CityCheck {
  scan_id: number;
  city: string;
  measured_at: string;
  reviewed: boolean;
  method: string;
  method_url: string;
  // The city's list may not be passed on (Seoul's is KOGL Type 4): only the counts and the source are sent, and the
  // fields that hold its records (matched_ids, id_fields, pairs, missed) are empty.
  counts_only?: boolean;
  source?: string;
  items: Record<string, { layer: string; found: number; covered: number; matched_ids: number[];
    rejected_at_city_point: number[];
    id_fields?: string[]; // the fields the city names one record by, e.g. ["COMPKEY", "UNITID"]
    // findings paired with a city record: in a photo's view or beyond it, and the record's id fields
    pairs?: { issue_id: number; in_view: boolean; dist_m: number; city: Record<string, string | number | null> }[];
    // the city's records in a photo's view that no finding was paired with
    missed?: { lat: number; lng: number; city: Record<string, string | number | null> }[] }>;
}

// One area's Record Update List (eval.asset_check): the counts come with the scan, the rows on request.
export interface AssetCounts {
  in_view: number; // city records in view of the photos
  seen: number;
  seen_by: Record<string, number>;
  not_determined: number; // does not mean missing
  not_listed: number;
  not_listed_unsure: number;
  seen_unsure: number;
  newer: number; // records with a photo newer than the city's date
  in_area?: number; // city records in the survey's box: those in view and those no photo reached
}

export interface AttributeCounts {
  required: number;
  agree: number;
  different: number;
  unsettled: number;
  not_checked: number;
  open_by: Record<string, number>; // why the open ones are open
}

export interface AssetCheckSummary {
  city: string;
  items: (AssetCounts & { item: string; asset: string; city_list: string; actions: Record<string, number>;
    attribute_checks?: AttributeCounts })[];
  totals: AssetCounts;
  actions: Record<string, number>; // rows per action
  no_visit: number; // key kept for older lists: city records that are candidates for desk review, not a measured count of avoided visits
  desk_review_candidates?: number; // the same number under its honest name
  // seen records whose city list has a yes or no a photo can answer: how that check ended. Seen is not checked.
  attribute_checks?: AttributeCounts;
  visits_attribute_unresolved?: number; // visit rows that are there in a photo but have an open attribute
  compare_unsure: number; // objects on no list that nobody could settle
  visits_with_photo?: number; // visits with a photo of their own or a survey photo from nearby
  date_gap_years?: number; // median gap between the city's date and a newer photo, on the rows that update a date
  credit?: string; // the words the city's data asks to be shown with its records
  left_out: { repeats: number; wrong: number };
  trust: {
    photo_months: string[];
    controls: { photos: string; asked: number; both_yes: number; note?: string }[];
    looked_at: number;
    sent_back: number;
    outside_view: number;
    findings?: { n: number; yes: number; no: number; unclear: number };
    record_check?: { asked: number; agree: number; differ: number; unsettled: number; sent_back: number };
    second_looks_usd?: number; // what the list's second looks cost, on top of the survey; none when there were none
  };
}

// A survey photo near a record the photos did not settle: linked on Mapillary, never served here. It may not show the record.
export interface NearbyPhoto {
  link: string;
  creator: string | null;
  captured_at: string | null;
  license: string;
  dist_m: number; // from the record's position to where the photo was taken
}

// The city record a row came from, as the review for the ledger needs it (spec 2026-11-04 "Fixed contract"). Absent on rows
// of older lists: those cannot be reviewed for the ledger.
export interface LedgerEditableField {
  before: unknown;
  yes: string; // the city's own value for yes
  no: string; // the city's own value for no
  question: string;
  suggested: string | null;
}

export interface LedgerSource {
  version: 1;
  layer: string; // a safe file name such as curb_ramps.geojson
  record_key: string;
  record_id: string;
  properties_sha256: string;
  review_key: string; // given by the list: binds the scan, item, record, source snapshot, photo and box. Never made here.
  editable_fields: Record<string, LedgerEditableField>;
  identity_warnings: string[]; // a nearby record of the same item photographed within seconds: a warning, not a verdict
}

export type LedgerIdentity = "confirmed" | "uncertain" | "different";
export type LedgerEvidence = "clear" | "unclear";
export type LedgerChoice = "change" | "observe" | "hold";

export interface LedgerDecision {
  review_key: string;
  record_id: string;
  item: string;
  identity: LedgerIdentity;
  evidence: LedgerEvidence;
  decision: LedgerChoice;
  field: string | null;
  proposed_value: string | null;
  neighbors_checked: boolean;
  reviewer: string;
  note: string;
  reviewed_at: string;
}

export interface LedgerJournal {
  schema_version: 1;
  scan_id: number;
  decisions: LedgerDecision[];
}

export interface AssetRow {
  ledger_source?: LedgerSource;
  item: string;
  asset: string;
  action: string; // what the city could do with this row
  city_says: string;
  photo_shows: string;
  city_list: string;
  city_record: string;
  city_location: string;
  status: "Seen in photo" | "Not determined" | "Not on the city list";
  how_seen: string;
  models_unsure: string;
  city_date: string;
  seen_in_photo_on: string;
  photo_newer_than_city_date: string;
  city_condition: string;
  lat: number;
  lng: number;
  position_source: string;
  position_accuracy: string;
  box: number[] | null;
  evidence: Evidence | null;
  nearby_photo?: NearbyPhoto;
  // where the city list has a yes or no a photo can answer: an object seen says nothing about it (eval.attribute_state)
  attribute_field?: string;
  attribute_state?: "agree" | "different" | "unsettled" | "not_checked";
  attribute_note?: string;
  action_if_attribute_agrees?: string; // a visit row waiting on its attribute: what it would suggest if that agreed
  visit_reason?: string; // why a visit row needs a newer photo or a field check
  // Evidence quality gate (spec 2026-11-04 B). Optional: older lists have none. "not_flagged" and "not_reviewed" mean nobody has
  // looked or nothing was found, never that the identity or the photo was verified.
  identity_state?: "unresolved" | "not_flagged";
  identity_reason?: string;
  competing_record_ids?: string[];
  evidence_quality?: "unclear" | "not_reviewed";
  evidence_quality_reason?: string;
}

export type UnreachedEntry = [string, number, number] | [string, number, number, string[]];

export interface AssetCheck extends AssetCheckSummary {
  rows: AssetRow[];
  // each city record in the box no photo had in view: [item, lng, lat] and, from 11/2, its ids in unreached_fields' order
  unreached?: UnreachedEntry[];
  unreached_fields?: Record<string, string[]>; // each item's id fields, the ones a record in view is shown by
}

export interface Walk {
  clip: number;
  points: [number, number][]; // [lng, lat] in photo order
}

export interface Scan {
  id: number;
  status: string; // queued | running | done | failed | stopped_cost_cap
  stage_status: Record<string, string>;
  bbox: Bbox;
  image_count: number;
  captured_from: string | null;
  captured_to: string | null;
  cost_usd: number;
  processing_seconds: number;
  review_seconds: number;
  km: number | null; // street length the photos cover
  usd_per_km: number | null;
  standard: "KR" | "US";
  checks: CheckCount[];
  items: ItemMeta[];
  city_check?: CityCheck;
  asset_check?: AssetCheckSummary;
  error: string | null;
  counts: {
    defects: number; obstructions: number; entrances: number; entrances_with_steps: number; needs_review: number;
    field_check: number; // a surveyor looked and could not tell from the photo: a site visit decides
    verified: number; // confirmed by a person
    rejected: number; // rejected by a person: off the map
    dropped: number | null; // set aside by the models as not the item; null: not recorded for this survey
  };
}

export interface Evidence {
  url: string;
  creator: string | null;
  captured_at: string | null;
  link: string;
  license: string;
  blurred: boolean;
}

// Every label is an icon plus text, so colour is never the only signal (spec 15).
export interface Label {
  icon: string;
  text: string;
}

export interface Entrance {
  id: number;
  lat: number;
  lng: number;
  heading: number | null; // the photo's compass direction
  name: string;
  category: string;
  step_count: number | null;
  stairs_detected: boolean | null;
  sill_possible: boolean | null;
  label: Label;
  review_status: string; // auto | needs_review | verified | field_check
  review_reasons: string[];
  evidence: Evidence | null;
  captured_at: string | null;
  old_photo: boolean;
}

export type IssueType = "missing_blocks" | "cover_cut" | "fixed_obstruction" | "obstruction";

export interface Issue {
  id: number | string; // a scan's own row is a number; an agent item's is "item<run>-<n>"
  item_run?: number; // the run of the agent's item this row belongs to
  lat: number;
  lng: number;
  heading: number | null; // the photo's compass direction
  box: number[] | null; // the model's box around the object, [x1, y1, x2, y2] in 0-1000 of the photo
  issue_type: string; // KR classes, "obstruction", or a check id
  durable: boolean; // missing blocks, covers and fixed objects stay; a movable obstruction is a fact at photo time
  defect: boolean;
  check: CheckInfo | null;
  // the other model's answer: Muse Spark Open on a defect, or Muse Spark auditing Muse Glimmer
  second_opinion: { answer: "yes" | "no" | "unclear"; agree: boolean; model: string } | null;
  label: Label;
  object_label: string | null;
  frame_start: number | null;
  frame_end: number | null;
  review_status: string; // auto | needs_review | verified | rejected | field_check
  review_reasons: string[];
  evidence: Evidence | null;
  captured_at: string | null;
  old_photo: boolean;
}

export interface DemoArea {
  scan_id: number;
  name: string;
  bbox: Bbox;
}

export interface EntranceReview {
  action: "confirm" | "correct" | "field_check";
  seconds: number;
  step_count?: number;
}

export interface IssueReview {
  action: "confirm" | "reject" | "answer" | "field_check";
  answer?: "yes" | "no";
  seconds: number;
}

export type Picked = { kind: "entrance"; item: Entrance } | { kind: "issue"; item: Issue };
