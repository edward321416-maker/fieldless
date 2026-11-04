// Review for the ledger: a person's decision about one city record and one photo, checked here the way the backend checks
// it again (docs/specs/2026-11-04-ledger-review-design.md). Pure functions only. Nothing here sends anything anywhere, and
// nothing is decided for the reviewer: every choice starts blank.
import type {
  AssetRow, LedgerChoice, LedgerDecision, LedgerEvidence, LedgerIdentity, LedgerJournal, LedgerSource,
} from "./types";

export type { LedgerChoice, LedgerDecision, LedgerEvidence, LedgerIdentity, LedgerJournal, LedgerSource } from "./types";

const DECISION_KEYS = ["review_key", "record_id", "item", "identity", "evidence", "decision", "field", "proposed_value",
  "neighbors_checked", "reviewer", "note", "reviewed_at"];
export const IDENTITIES: string[] = ["confirmed", "uncertain", "different"];
export const EVIDENCES: string[] = ["clear", "unclear"];
export const CHOICES: string[] = ["change", "observe", "hold"];
// A name that would reach the prototype if it were ever used as an object key. Nothing from a file or a list may be one.
const DANGEROUS_KEYS = ["__proto__", "constructor", "prototype"];
export const isDangerousKey = (key: string) => DANGEROUS_KEYS.includes(key);
// An official inspection or verification date is the city's own statement: a photo never edits it.
const DATE_FIELD = /date|verif|inspect|install|updated/i;
export const REVIEWER_MAX = 200;
export const NOTE_MAX = 2000;
const JOURNAL_KEYS = ["schema_version", "scan_id", "decisions"];
// Said on every row of both CSV files, in the words eval/ledger_review.py writes (tests/fixtures/ledger_interop proves it).
export const ASSERTION = "Self-asserted by the reviewer in the app; not an authenticated municipal approval.";
export const APPLICATION_STATUS = "Prepared only. Not applied to any official ledger.";
export const DATE_MEANING = "Date of the photo (an observation). Not the city's inspection or verification date.";
// One BOM at the start of the CSV text, so Excel reads non-ASCII names and notes as UTF-8. A Blob or a file made from the string
// encodes it as is: nothing may add a second one.
const BOM = "﻿";
const SAFE_LAYER = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const ISO_INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const PHOTO_DAY = /^\d{4}-\d{2}-\d{2}/;

export const isText = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";
export const has = (o: object, key: string) => Object.prototype.hasOwnProperty.call(o, key);
export const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

// The source snapshot of a row, or null when there is none or it is not the version 1 contract. A row with none cannot be reviewed.
export function sourceOf(row: AssetRow): LedgerSource | null {
  const s: unknown = row.ledger_source;
  if (!isRecord(s) || s.version !== 1) return null;
  if (![s.layer, s.record_key, s.record_id, s.properties_sha256, s.review_key].every(isText)) return null;
  if (!SAFE_LAYER.test(s.layer as string) || (s.layer as string).includes("..")) return null;
  if (!isRecord(s.editable_fields) || !Array.isArray(s.identity_warnings) || !s.identity_warnings.every((w) => typeof w === "string")) return null;
  if (isDangerousKey(s.review_key as string) || Object.keys(s.editable_fields).some(isDangerousKey)) return null;
  for (const f of Object.values(s.editable_fields)) {
    if (!isRecord(f) || typeof f.yes !== "string" || typeof f.no !== "string" || typeof f.question !== "string") return null;
  }
  return s as unknown as LedgerSource;
}

// The rows that can be reviewed: a source, and a review key no other row shares (then the key could not name one row).
export function reviewableRows(rows: AssetRow[]): AssetRow[] {
  const count = new Map<string, number>();
  for (const r of rows) {
    const key = sourceOf(r)?.review_key;
    if (key) count.set(key, (count.get(key) ?? 0) + 1);
  }
  return rows.filter((r) => {
    const key = sourceOf(r)?.review_key;
    return key !== undefined && count.get(key) === 1;
  });
}

// A short fingerprint of text (cyrb53). It only notices a change; it protects nothing against anyone who wants to forge one.
function fingerprint(text: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i += 1) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

// What the list says about a row beyond its source snapshot: its suggested action, the identity and clarity states with their reasons, the
// records it could be and the neighbouring-record warnings. When any of it changes, work saved before is not for this version of the row.
function classification(row: AssetRow, source: LedgerSource): string {
  return fingerprint(JSON.stringify([row.action, row.identity_state ?? null, row.identity_reason ?? "", row.competing_record_ids ?? [],
    row.evidence_quality ?? null, row.evidence_quality_reason ?? "", source.identity_warnings]));
}

// Changes when the scan, any record's snapshot or its classification changes, so a review made on one never carries over to another.
export function reviewSignature(scanId: number, rows: AssetRow[]): string {
  return [String(scanId), ...reviewableRows(rows).map((r) => {
    const s = sourceOf(r)!;
    return `${s.review_key}:${s.properties_sha256}:${s.record_id}:${classification(r, s)}`;
  })].join("|");
}

// The values a reviewer may propose for a field: the city's other yes/no value. Nothing for a date, a field the list does
// not name, or a record whose own value is neither the city's yes nor its no.
export function proposalChoices(source: LedgerSource, field: string): string[] {
  if (!has(source.editable_fields, field) || DATE_FIELD.test(field)) return [];
  const { before, yes, no } = source.editable_fields[field];
  if (yes === no) return [];
  const now = String(before);
  return now === yes ? [no] : now === no ? [yes] : [];
}

// The fields of a source a person may propose a change to.
export function editableNames(source: LedgerSource): string[] {
  return Object.keys(source.editable_fields).filter((f) => proposalChoices(source, f).length > 0);
}

export interface Draft {
  identity: "" | LedgerIdentity;
  evidence: "" | LedgerEvidence;
  decision: "" | LedgerChoice;
  field: string;
  proposed: string;
  neighbors: boolean;
  note: string;
}

// Nothing chosen, nothing approved.
export const EMPTY_DRAFT: Draft = { identity: "", evidence: "", decision: "", field: "", proposed: "", neighbors: false, note: "" };

export function isInstant(text: unknown): boolean {
  const m = typeof text === "string" ? ISO_INSTANT.exec(text) : null;
  if (!m) return false;
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  const day = new Date(Date.UTC(y, mo - 1, d));
  const offset = m[8] === "Z" ? [0, 0] : m[8].slice(1).split(":").map(Number);
  return day.getUTCFullYear() === y && day.getUTCMonth() === mo - 1 && day.getUTCDate() === d
    && h < 24 && mi < 60 && s < 60 && offset[0] < 24 && offset[1] < 60;
}

// The day the photo was taken, in UTC as the backend reads it, kept apart from any date the city wrote.
export function photoDay(row: AssetRow): string {
  const at = row.evidence?.captured_at;
  if (!at) return "";
  const ms = Date.parse(at);
  if (!Number.isNaN(ms)) return new Date(ms).toISOString().slice(0, 10);
  return PHOTO_DAY.test(at) ? at.slice(0, 10) : "";
}

function photoProblems(row: AssetRow, what: string): string[] {
  if (!row.evidence || !isText(row.evidence.link)) return [`${what} needs a photo as its evidence.`];
  return photoDay(row) ? [] : [`${what} needs the day the photo was taken.`];
}

// What is still missing before a draft can be recorded, in words for the reviewer.
export function draftProblems(row: AssetRow, draft: Draft, reviewer: string): string[] {
  const source = sourceOf(row);
  if (!source) return ["This row has no source snapshot, so it cannot be reviewed for the ledger."];
  const out: string[] = [];
  if (!reviewer.trim()) out.push("Enter the reviewer name.");
  else if (reviewer.length > REVIEWER_MAX) out.push(`The reviewer name is too long (${REVIEWER_MAX} characters at most).`);
  if (!draft.decision) out.push("Choose a decision: hold, observe or change.");
  if (!draft.note.trim()) out.push("Write the reason.");
  else if (draft.note.length > NOTE_MAX) out.push(`The reason is too long (${NOTE_MAX} characters at most).`);
  if (draft.decision === "change" || draft.decision === "observe") {
    const what = draft.decision === "change" ? "A change" : "An observation";
    if (draft.identity !== "confirmed") out.push(`${what} needs you to confirm it is the same facility as the record.`);
    if (draft.evidence !== "clear") out.push(`${what} needs a photo that is clear enough to rely on.`);
    out.push(...photoProblems(row, what));
    if (source.identity_warnings.length > 0 && !draft.neighbors) out.push("Confirm you checked the neighboring records.");
  }
  if (draft.decision === "change") {
    const choices = draft.field ? proposalChoices(source, draft.field) : [];
    if (!draft.field) out.push("Choose the field to change.");
    else if (choices.length === 0) out.push("That field cannot be changed from here.");
    else if (!draft.proposed) out.push("Choose the new value.");
    else if (!choices.includes(draft.proposed)) {
      const f = source.editable_fields[draft.field];
      out.push(draft.proposed === f.yes || draft.proposed === f.no ? "The record already has that value."
        : "The new value must be the city's yes or no value.");
    }
  }
  return out;
}

// The decision a draft stands for, or what is missing. A hold records only what was answered: a blank answer is recorded
// in its cautious form.
export function decisionFromDraft(row: AssetRow, draft: Draft, reviewer: string, reviewedAt: string):
  { decision: LedgerDecision } | { problems: string[] } {
  const problems = draftProblems(row, draft, reviewer);
  if (!isInstant(reviewedAt)) problems.push("The time of the review is not valid.");
  const source = sourceOf(row);
  if (problems.length > 0 || !source || !draft.decision) return { problems };
  const change = draft.decision === "change";
  const decision: LedgerDecision = {
    review_key: source.review_key,
    record_id: source.record_id,
    item: row.item,
    identity: draft.identity || "uncertain",
    evidence: draft.evidence || "unclear",
    decision: draft.decision,
    field: change ? draft.field : null,
    proposed_value: change ? draft.proposed : null,
    neighbors_checked: source.identity_warnings.length > 0 && draft.neighbors,
    reviewer: reviewer.trim(),
    note: draft.note.trim(),
    reviewed_at: reviewedAt,
  };
  const strict = decisionProblems(row, decision);
  return strict.length > 0 ? { problems: strict } : { decision };
}

// The rules a recorded decision must meet, whoever made it. The same rules the backend applies to a journal it is handed.
export function decisionProblems(row: AssetRow, d: unknown): string[] {
  if (!isRecord(d)) return ["A decision is not an object."];
  const source = sourceOf(row);
  if (!source) return ["The row has no source snapshot."];
  const id = typeof d.record_id === "string" ? d.record_id : "?";
  const out: string[] = [];
  const bad = (text: string) => out.push(`${id}: ${text}`);
  for (const key of Object.keys(d)) if (!DECISION_KEYS.includes(key)) bad(`unknown key ${key}.`);
  for (const key of DECISION_KEYS) if (!(key in d)) bad(`missing ${key}.`);
  if (out.length > 0) return out;
  if (d.review_key !== source.review_key) bad("the review key is not this row's.");
  if (d.record_id !== source.record_id) bad("the record ID is not the source's.");
  if (d.item !== row.item) bad("the item is not this row's.");
  if (!IDENTITIES.includes(d.identity as string)) bad("identity is not confirmed, uncertain or different.");
  if (!EVIDENCES.includes(d.evidence as string)) bad("evidence is not clear or unclear.");
  if (!CHOICES.includes(d.decision as string)) bad("the decision is not change, observe or hold.");
  if (typeof d.neighbors_checked !== "boolean") bad("neighbors_checked is not true or false.");
  if (!isText(d.reviewer) || d.reviewer.length > REVIEWER_MAX) bad(`the reviewer is empty or longer than ${REVIEWER_MAX} characters.`);
  if (!isText(d.note) || d.note.length > NOTE_MAX) bad(`the reason is empty or longer than ${NOTE_MAX} characters.`);
  if (!isInstant(d.reviewed_at)) bad("the time of the review is not valid.");
  if (out.length > 0) return out;
  const c = d.decision as LedgerChoice;
  if (c === "hold" || c === "observe") {
    if (d.field !== null || d.proposed_value !== null) bad(`${c} must not carry a field or value.`);
  }
  if (c === "change" || c === "observe") {
    if (d.identity !== "confirmed") bad("not confirmed as the same facility.");
    if (d.evidence !== "clear") bad("the evidence is not clear.");
    for (const p of photoProblems(row, c === "change" ? "A change" : "An observation")) bad(p);
    if (source.identity_warnings.length > 0 && d.neighbors_checked !== true) bad("the neighboring records were not checked.");
  }
  if (c === "change") {
    const choices = typeof d.field === "string" ? proposalChoices(source, d.field) : [];
    if (typeof d.field !== "string" || choices.length === 0) bad("that field cannot be changed.");
    else if (typeof d.proposed_value !== "string" || !choices.includes(d.proposed_value)) bad("the new value is not the other yes/no value.");
  }
  return out;
}

export interface Attribution {
  photo_observed_on: string;
  date_meaning: string;
  photo_creator: string;
  photo_link: string;
  photo_license: string;
}

interface Common extends Attribution {
  review_key: string;
  record_id: string;
  item: string;
  layer: string;
  identity: LedgerIdentity;
  evidence: LedgerEvidence;
  neighbors_checked: boolean;
  reviewer: string;
  reviewer_assertion: string;
  note: string;
  reviewed_at: string;
  application_status: string;
    scan_id: number;
  record_key: string;
  properties_sha256: string;
}
export interface ChangeEntry extends Common {
  field: string;
  before: string;
  after: string;
}
export type ObservationEntry = Common;

export interface JournalCheck {
  ok: boolean;
  errors: string[];
  changes: ChangeEntry[];
  observations: ObservationEntry[];
  holds: LedgerDecision[];
}

const REJECTED: JournalCheck = { ok: false, errors: [], changes: [], observations: [], holds: [] };

// The whole package is accepted or rejected: one bad decision, a repeated key or a key that is not in this list rejects it all.
export function validateJournal(rows: AssetRow[], journal: LedgerJournal, scanId: number): JournalCheck {
  const reject = (errors: string[]): JournalCheck => ({ ...REJECTED, errors });
  if (!isRecord(journal) || journal.schema_version !== 1) return reject(["The journal is not schema version 1."]);
  if (journal.scan_id !== scanId) return reject([`The journal is for scan ${String(journal.scan_id)}, not scan ${scanId}.`]);
  const stray = Object.keys(journal).filter((k) => !JOURNAL_KEYS.includes(k));
  if (stray.length > 0) return reject([`The journal has a key the contract does not have: ${stray.join(", ")}.`]);
  if (!Array.isArray(journal.decisions)) return reject(["The journal has no list of decisions."]);
  const byKey = new Map(reviewableRows(rows).map((r) => [sourceOf(r)!.review_key, r]));
  const seen = new Set<string>();
  const errors: string[] = [];
  const changes: ChangeEntry[] = [];
  const observations: ObservationEntry[] = [];
  const holds: LedgerDecision[] = [];
  const touched = new Map<string, string>();
  for (const d of journal.decisions) {
    const key = isRecord(d) && typeof d.review_key === "string" ? d.review_key : "";
    if (seen.has(key)) {
      errors.push(`The review key of ${String((d as LedgerDecision).record_id)} appears more than once.`);
      continue;
    }
    seen.add(key);
    const row = byKey.get(key);
    if (!row) {
      errors.push(`A decision for ${isRecord(d) ? String(d.record_id) : "?"} is not in this list: its source is not a row of this scan's list.`);
      continue;
    }
    const problems = decisionProblems(row, d);
    if (problems.length > 0) {
      errors.push(...problems);
      continue;
    }
    const decision = d as LedgerDecision;
    const source = sourceOf(row)!;
    const common = {
      review_key: source.review_key, record_id: source.record_id, item: row.item, layer: source.layer, identity: decision.identity,
      evidence: decision.evidence, neighbors_checked: decision.neighbors_checked, reviewer: decision.reviewer, note: decision.note,
      reviewer_assertion: ASSERTION, application_status: APPLICATION_STATUS, date_meaning: DATE_MEANING,
      reviewed_at: decision.reviewed_at, photo_observed_on: photoDay(row), photo_creator: row.evidence?.creator ?? "",
      photo_link: row.evidence?.link ?? "", photo_license: row.evidence?.license ?? "", scan_id: scanId, record_key: source.record_key,
      properties_sha256: source.properties_sha256,
    };
    if (decision.decision === "hold") holds.push(decision);
    else if (decision.decision === "observe") observations.push(common);
    else {
      const slot = `${source.layer}\u0000${source.record_id}\u0000${decision.field}`;
      if (touched.has(slot)) {
        errors.push(`${source.record_id}: two decisions change the same field (${decision.field}) of the same record.`);
        continue;
      }
      touched.set(slot, source.review_key);
      changes.push({ ...common, field: decision.field!, before: String(source.editable_fields[decision.field!].before), after: decision.proposed_value! });
    }
  }
  return errors.length > 0 ? reject(errors) : { ok: true, errors: [], changes, observations, holds };
}

// A cell that a spreadsheet would run as a formula is kept as text with a leading apostrophe, as the backend writes it.
// Quotes and line breaks are the CSV's own.
export function csvCell(value: unknown): string {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@\t\r\n]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

// The columns, in the order of interface-checkpoint.md that eval/ledger_review.py writes too.
const CHANGE_COLUMNS = ["review_key", "record_id", "item", "layer", "field", "before", "after", "identity", "evidence", "neighbors_checked",
  "reviewer", "reviewer_assertion", "note", "reviewed_at", "application_status", "photo_observed_on", "date_meaning", "photo_creator",
  "photo_link", "photo_license", "scan_id", "record_key", "properties_sha256"] as const;
const OBSERVATION_COLUMNS = ["review_key", "record_id", "item", "layer", "photo_observed_on", "date_meaning", "photo_creator", "photo_link",
  "photo_license", "identity", "evidence", "neighbors_checked", "reviewer", "reviewer_assertion", "note", "reviewed_at", "application_status",
  "scan_id", "record_key", "properties_sha256"] as const;

function table(columns: readonly string[], entries: Record<string, unknown>[]): string {
  return BOM + [columns.join(","), ...entries.map((e) => columns.map((c) => csvCell(e[c])).join(","))].map((line) => `${line}\r\n`).join("");
}

function accepted(rows: AssetRow[], journal: LedgerJournal, scanId: number): JournalCheck {
  const check = validateJournal(rows, journal, scanId);
  if (!check.ok) throw new Error(`The journal was rejected: ${check.errors.join(" ")}`);
  return check;
}

export function changesCsv(rows: AssetRow[], journal: LedgerJournal, scanId: number): string {
  return table(CHANGE_COLUMNS, accepted(rows, journal, scanId).changes as unknown as Record<string, unknown>[]);
}

export function observationsCsv(rows: AssetRow[], journal: LedgerJournal, scanId: number): string {
  return table(OBSERVATION_COLUMNS, accepted(rows, journal, scanId).observations as unknown as Record<string, unknown>[]);
}

export function buildJournal(scanId: number, decisions: LedgerDecision[]): LedgerJournal {
  return { schema_version: 1, scan_id: scanId, decisions };
}

export interface ExportFile {
  name: string;
  mime: string;
  text: string;
}

// The three files a reviewer keeps. The journal holds every decision, the holds too; the other two hold only what each
// kind of decision says. Nothing is made from a package that does not pass.
export function exportFiles(rows: AssetRow[], journal: LedgerJournal, scanId: number): ExportFile[] {
  accepted(rows, journal, scanId);
  const body = JSON.stringify({ schema_version: 1, scan_id: scanId, decisions: journal.decisions.map((d) => Object.fromEntries(DECISION_KEYS.map((k) => [k, (d as unknown as Record<string, unknown>)[k]]))) }, null, 2);
  return [
    { name: `ledger-review-scan${scanId}-journal.json`, mime: "application/json", text: `${body}\n` },
    { name: `ledger-review-scan${scanId}-changes.csv`, mime: "text/csv", text: changesCsv(rows, journal, scanId) },
    { name: `ledger-review-scan${scanId}-observations.csv`, mime: "text/csv", text: observationsCsv(rows, journal, scanId) },
  ];
}

// The Asset Check the journal was made on: the whole list, as it was given, so a person can keep the two together and the
// local application can check the journal against it. It is not edited here.
export interface LedgerCheck {
  scan_id?: number;
  rows: AssetRow[];
}

export function sourceFile(check: LedgerCheck, scanId: number): ExportFile {
  if (check.scan_id !== scanId) throw new Error(`The list is for scan ${String(check.scan_id)}, not scan ${scanId}.`);
  return { name: `ledger-review-scan${scanId}-source.json`, mime: "application/json", text: `${JSON.stringify(check, null, 2)}\n` };
}
