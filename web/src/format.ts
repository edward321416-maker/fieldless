import type { Issue, IssueType, Label, Scan } from "./types";

// Same order as pipeline/runner.py STAGES: the sidewalk layer runs first because it is the main layer.
export const STAGES = ["collect", "chunks", "scene", "sidewalk", "defects", "sidewalk_evidence", "entrances", "evidence"] as const;
export type Stage = (typeof STAGES)[number];

// Each stage names the Meta tool doing the work, so the screen shows what runs where (demo scene S4).
export const STAGE_LABELS: Record<Stage, string> = {
  collect: "Collect street photos from Mapillary",
  chunks: "Prepare the photo sequences",
  scene: "Watch each walk for obstacles and entrances (Muse Spark)",
  sidewalk: "Track tactile paving and obstacles on it (SAM 3.1)",
  defects: "Check tactile paving for missing blocks and covers (Muse Spark)",
  sidewalk_evidence: "Blur faces and plates in sidewalk evidence photos (SAM 3.1)",
  entrances: "Count entrance steps (SAM 3.1 + Muse Spark)",
  evidence: "Blur faces and plates in entrance evidence photos (SAM 3.1)",
};

export const REASONS: Record<string, string> = {
  possible_sill: "Possible sill: a step of a few centimetres cannot be confirmed from photos",
  count_mismatch: "Muse Spark and SAM 3.1 disagree on the number of steps",
  low_confidence: "The model was not confident",
  analysis_failed: "The analysis of this entrance failed",
  spark_unsure: "Muse Spark was unsure whether the tactile paving is blocked",
  check_failed: "The check of this item failed",
  possible_defect: "Found by the model from one photo; confirm it on the photo",
  unclear: "The model could not tell from the photo",
  models_disagree: "Muse Spark and Muse Spark Open answered differently; check this first",
};

// A US scan's stages survey the walking path and the standard's checks (pipeline/standards/us.toml).
const US_STAGE_LABELS: Partial<Record<Stage, string>> = {
  sidewalk: "Track the walking path and objects on it (SAM 3.1)",
  defects: "Check the survey items: SAM 3.1 finds, Muse Spark answers",
};

const QUESTIONS: Record<IssueType, string> = {
  missing_blocks: "Are tactile blocks missing here?",
  cover_cut: "Does a cover or plate cut the tactile line here?",
  fixed_obstruction: "Does a fixed object stand on the tactile paving here?",
  obstruction: "Is the tactile paving blocked here?",
};

// The check's own question for every check row, whatever the model answered (user decision 2026-10-28).
export function issueQuestion(issue: Issue): string {
  if (issue.check) return issue.check.question;
  return QUESTIONS[issue.issue_type as IssueType] ?? "Is this right?";
}

// The colour class of an issue row: a check row is coloured by whether it is a defect, not by its answer; an
// inventory row is an asset (unless still unclear).
export function issueTone(issue: Issue): string {
  if (!issue.check) return issue.issue_type;
  if (issue.check.inventory) return issue.check.answer === "unclear" ? "check-unclear" : "check-asset";
  return issue.defect ? "check-defect" : issue.check.answer === "unclear" ? "check-unclear" : "check-ok";
}

export function reasonText(code: string): string {
  return REASONS[code] ?? code.replace(/_/g, " ");
}

export function labelText(l: Label): string {
  return `${l.icon} ${l.text}`;
}

export function photoMonth(iso: string | null): string {
  return iso ? iso.slice(0, 7) : "unknown date";
}

export function yearsSpan(from: string | null, to: string | null): string {
  if (!from || !to) return "no photos";
  const a = from.slice(0, 4);
  const b = to.slice(0, 4);
  return a === b ? a : `${a}–${b}`;
}

export function minutes(seconds: number): string {
  return `${(seconds / 60).toFixed(1)} min`;
}

export function dollars(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

export function km(value: number): string {
  return `${value.toFixed(2)} km`;
}

export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function stageState(scan: Scan | null): { name: Stage; label: string; state: string }[] {
  return STAGES.map((name) => {
    const raw = scan?.stage_status[name] ?? "pending";
    const label = (scan?.standard === "US" ? US_STAGE_LABELS[name] : undefined) ?? STAGE_LABELS[name];
    return { name, label, state: raw.startsWith("failed") ? "failed" : raw };
  });
}

export function isFinished(scan: Scan | null): boolean {
  return scan !== null && ["done", "failed", "stopped_cost_cap"].includes(scan.status);
}
