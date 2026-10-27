// JSON shapes of the Threshold API (server/views.py and server/app.py).

export type Bbox = [number, number, number, number]; // min_lng, min_lat, max_lng, max_lat

export interface ServerConfig {
  invite_required: boolean;
  max_bbox_deg2: number;
}

export interface Preview {
  photos: number;
  sequences: number;
  captured_from: string | null;
  captured_to: string | null;
  est_cost_usd: number;
}

export interface CheckInfo {
  id: string;
  find: string;
  target: string; // the noun the survey is about; `find` is SAM's phrase and may differ
  question: string; // the check's `ask` sentence (pipeline/standards)
  answer: "yes" | "no" | "unclear";
  report_when: "yes" | "no" | ""; // "" for an inventory check
  label_yes: string;
  label_no: string;
  inventory: boolean; // a confirmed object is an asset point, not a defect
}

export interface CheckCount {
  id: string;
  find: string;
  target: string;
  report_when: "yes" | "no" | ""; // the answer that is the defect; "" for an inventory check
  label_yes: string;
  label_no: string;
  inventory: boolean;
  yes: number;
  no: number;
  unclear: number;
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
  error: string | null;
  counts: { defects: number; obstructions: number; entrances: number; entrances_with_steps: number; needs_review: number };
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
  name: string;
  category: string;
  step_count: number | null;
  stairs_detected: boolean | null;
  sill_possible: boolean | null;
  label: Label;
  review_status: string; // auto | needs_review | verified
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
  issue_type: string; // KR classes, "obstruction", or a check id
  durable: boolean; // missing blocks, covers and fixed objects stay; a movable obstruction is a fact at photo time
  defect: boolean;
  check: CheckInfo | null;
  second_opinion: { answer: "yes" | "no" | "unclear"; agree: boolean } | null;
  label: Label;
  object_label: string | null;
  frame_start: number | null;
  frame_end: number | null;
  review_status: string; // auto | needs_review | verified | rejected
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
  action: "confirm" | "correct";
  seconds: number;
  step_count?: number;
}

export interface IssueReview {
  action: "confirm" | "reject" | "answer";
  answer?: "yes" | "no";
  seconds: number;
}
