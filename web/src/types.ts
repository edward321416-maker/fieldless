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
  error: string | null;
  counts: { defects: number; obstructions: number; entrances: number; entrances_with_steps: number; needs_review: number };
}

export interface Evidence {
  url: string;
  creator: string | null;
  captured_at: string | null;
  link: string;
  license: string;
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
  issue_type: IssueType;
  durable: boolean; // missing blocks, covers and fixed objects stay; a movable obstruction is a fact at photo time
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
  action: "confirm" | "reject";
  seconds: number;
}
