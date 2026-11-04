// Reading a journal or a review package back in. A file is only a claim: it is checked against the list that is open, the same way
// the backend checks a journal, and it is shown as a plan first. Planning changes nothing; applying replaces the current work whole.
import type { RecoverableState } from "./ledgerRecovery";
import { hasMeaningfulWork } from "./ledgerRecovery";
import {
  EMPTY_DRAFT, isRecord, reviewSignature, reviewableRows, sourceOf, validateJournal, type Draft, type JournalCheck, type LedgerCheck,
} from "./ledgerReview";
import type { AssetRow, LedgerDecision, LedgerJournal } from "./types";

export const IMPORT_MAX_CHARS = 5_000_000;
const PACKAGE_KIND = "fieldless-ledger-review";

export interface PreviewLine {
  key: string;
  text: string;
}

export type ImportPlan =
  | { ok: false; errors: string[] }
  | {
    ok: true;
    kind: "journal" | "package";
    journal: LedgerJournal;
    check: JournalCheck;
    counts: { changes: number; observations: number; holds: number };
    lines: PreviewLine[];
    replaces: { decisions: number; drafts: number };
    notes: string[];
  };

export interface ImportContext {
  scanId: number;
  rows: AssetRow[];
  current: RecoverableState;
  // The package reader (ledgerPackage.readReviewPackage). Without one a package cannot be read.
  readPackage?: (value: unknown) => { source: LedgerCheck; journal: LedgerJournal };
}

const reject = (...errors: string[]): ImportPlan => ({ ok: false, errors });

const NOTES = [
  "A journal holds recorded decisions only. Drafts you have not recorded are not in it, and replacing clears the ones you have now.",
  "Imported decisions are self-asserted by the reviewer who made them. They are not an authenticated municipal approval, and every one was checked against the same rules again.",
];

// What replacing would lose: the decisions recorded now, and the drafts no decision stands for.
export function lossOf(current: RecoverableState): { decisions: number; drafts: number } {
  const decisions = Object.keys(current.decisions).length;
  if (!hasMeaningfulWork(current)) return { decisions: 0, drafts: 0 };
  const drafts = Object.entries(current.drafts).filter(([key, d]) => !Object.prototype.hasOwnProperty.call(current.decisions, key)
    && JSON.stringify({ ...EMPTY_DRAFT, ...d }) !== JSON.stringify(EMPTY_DRAFT)).length;
  return { decisions, drafts };
}

export function planImport(text: string, ctx: ImportContext): ImportPlan {
  if (typeof text !== "string" || text.length > IMPORT_MAX_CHARS) return reject("The file is far larger than a review. Nothing was imported.");
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return reject("The file is not readable JSON. Nothing was imported.");
  }
  if (!isRecord(raw)) return reject("The file is not a journal or a review package. Nothing was imported.");

  let kind: "journal" | "package" = "journal";
  let journal: unknown = raw;
  if (Object.prototype.hasOwnProperty.call(raw, "kind")) {
    if (raw.kind !== PACKAGE_KIND) return reject("The file is not a journal or a review package. Nothing was imported.");
    if (!ctx.readPackage) return reject("Review packages cannot be read here. Import the journal instead.");
    kind = "package";
    try {
      const read = ctx.readPackage(raw);
      if (read.source.scan_id !== ctx.scanId || reviewSignature(ctx.scanId, read.source.rows) !== reviewSignature(ctx.scanId, ctx.rows)) {
        return reject("This package was made on a different source list than the one that is open, so it was not imported. A package cannot switch the scan or the source.");
      }
      journal = read.journal;
    } catch (error) {
      return reject(`The package was rejected: ${error instanceof Error ? error.message : "it could not be read"}`);
    }
  }
  const checked = validateJournal(ctx.rows, journal as LedgerJournal, ctx.scanId);
  if (!checked.ok) return reject(...checked.errors);
  if ((journal as LedgerJournal).decisions.length === 0) return reject("The file holds no decisions, so there is nothing to import. Your current work is unchanged.");

  const byKey = new Map(reviewableRows(ctx.rows).map((r) => [sourceOf(r)!.review_key, r]));
  const lines: PreviewLine[] = (journal as LedgerJournal).decisions.map((d) => {
    const row = byKey.get(d.review_key)!;
    const source = sourceOf(row)!;
    const what = d.decision === "change"
      ? `change ${d.field}: ${String(source.editable_fields[d.field!].before)} → ${d.proposed_value}`
      : d.decision === "observe" ? "observe (seen in this photo; no value changes)" : "hold (no claim)";
    return { key: d.review_key, text: `${source.record_id} · ${row.asset} · ${what} · by ${d.reviewer}` };
  });
  return { ok: true, kind, journal: journal as LedgerJournal, check: checked,
    counts: { changes: checked.changes.length, observations: checked.observations.length, holds: checked.holds.length },
    lines, replaces: lossOf(ctx.current), notes: NOTES };
}

function draftOf(d: LedgerDecision): Draft {
  return { identity: d.identity, evidence: d.evidence, decision: d.decision, field: d.field ?? "", proposed: d.proposed_value ?? "",
    neighbors: d.neighbors_checked, note: d.note };
}

// The state after the reviewer chose to replace: the decisions of the file, drafts that show them, and everything else as it was.
// A plan that was not accepted changes nothing.
export function applyImport(plan: ImportPlan, current: RecoverableState, rows: AssetRow[]): RecoverableState {
  if (!plan.ok) return current;
  const keys = new Set(reviewableRows(rows).map((r) => sourceOf(r)!.review_key));
  const decisions = Object.fromEntries(plan.journal.decisions.map((d) => [d.review_key, d]));
  const drafts = Object.fromEntries(plan.journal.decisions.map((d) => [d.review_key, draftOf(d)]));
  return { ...current, reviewer: current.reviewer || plan.journal.decisions[0]?.reviewer || "", decisions, drafts,
    selected: keys.has(current.selected) ? current.selected : "" };
}
