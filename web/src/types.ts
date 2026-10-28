// JSON shapes of the Threshold API (server/views.py and server/app.py).

export type Bbox = [number, number, number, number]; // min_lng, min_lat, max_lng, max_lat

export interface ServerConfig {
  invite_required: boolean;
  max_bbox_deg2: number;
  coverage_tiles?: boolean; // the server passes Mapillary's coverage tiles on (/api/mapillary/tiles); not the static demo
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
}

// The offline comparison with the city's own records (eval, city_check_scan<N>.json), demo surveys only.
export interface CityCheck {
  scan_id: number;
  city: string;
  measured_at: string;
  reviewed: boolean;
  method: string;
  method_url: string;
  items: Record<string, { layer: string; found: number; covered: number; matched_ids: number[];
    rejected_at_city_point: number[] }>;
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
  id: number;
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
