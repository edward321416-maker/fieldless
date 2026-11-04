// Work in progress on a review, kept in this browser so a reload or another scan does not lose it. One file per scan, bound to the
// scan and to the exact source snapshots (reviewSignature): work made on one version of a list is never restored onto another.
// The file is read back as untrusted input: every part is checked, decisions by the same rules as a journal, and it is restored
// whole or not at all. Nothing here is sent anywhere.
import { DEFAULT_QUEUE, QUERY_MAX, isFilter, type QueueSettings } from "./ledgerQuery";
import {
  CHOICES, EMPTY_DRAFT, EVIDENCES, IDENTITIES, NOTE_MAX, REVIEWER_MAX, isInstant, isRecord, reviewableRows, sourceOf, validateJournal, type Draft,
} from "./ledgerReview";
import type { AssetRow, LedgerDecision } from "./types";

export const RECOVERY_VERSION = 1;
export const RECOVERY_KIND = "fieldless-ledger-recovery";
export const RECOVERY_PREFIX = "fieldless.ledger.recovery.v1:";
// The three demo scans and the one being worked on.
export const RECOVERY_LIMIT = 4;
export const RECOVERY_MAX_CHARS = 2_000_000;

const FILE_KEYS = ["version", "kind", "scan_id", "signature", "saved_at", "reviewer", "drafts", "decisions", "selected", "queue"];
const DRAFT_KEYS = Object.keys(EMPTY_DRAFT);
const QUEUE_KEYS = Object.keys(DEFAULT_QUEUE);
const FIELD_MAX = 200;

// What is kept of a review: everything the reviewer typed or chose, and where they were.
export interface RecoverableState {
  reviewer: string;
  drafts: Record<string, Draft>; // by the source's review key
  decisions: Record<string, LedgerDecision>;
  selected: string; // a review key, "" for the first row of the queue
  queue: QueueSettings;
}

export function emptyState(): RecoverableState {
  return { reviewer: "", drafts: {}, decisions: {}, selected: "", queue: { ...DEFAULT_QUEUE } };
}

const sameDraft = (a: Draft) => DRAFT_KEYS.every((k) => a[k as keyof Draft] === EMPTY_DRAFT[k as keyof Draft]);

// Something a person would be sorry to lose: a recorded decision or a draft with any answer in it. A reviewer name alone is not.
export function hasMeaningfulWork(state: RecoverableState): boolean {
  return Object.keys(state.decisions).length > 0 || Object.values(state.drafts).some((d) => !sameDraft({ ...EMPTY_DRAFT, ...d }));
}

export function serializeRecovery(scanId: number, signature: string, state: RecoverableState, savedAt: string): string {
  return JSON.stringify({ version: RECOVERY_VERSION, kind: RECOVERY_KIND, scan_id: scanId, signature, saved_at: savedAt, reviewer: state.reviewer,
    drafts: state.drafts, decisions: state.decisions, selected: state.selected, queue: state.queue });
}

export type ParseResult =
  | { status: "restored"; state: RecoverableState; savedAt: string; decisions: number; drafts: number }
  | { status: "stale"; savedAt: string }
  | { status: "corrupt"; detail: string };

const corrupt = (detail: string): ParseResult => ({ status: "corrupt", detail });
const own = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);

function readDraft(value: unknown): Draft | string {
  if (!isRecord(value)) return "a draft is not an object";
  for (const k of Object.keys(value)) if (!DRAFT_KEYS.includes(k)) return `a draft has a key it should not have (${k})`;
  const d: Record<string, unknown> = { ...EMPTY_DRAFT };
  for (const k of DRAFT_KEYS) if (own(value, k)) d[k] = value[k];
  if (!["", ...IDENTITIES].includes(d.identity as string)) return "a draft has an identity answer that is not one of the choices";
  if (!["", ...EVIDENCES].includes(d.evidence as string)) return "a draft has an evidence answer that is not one of the choices";
  if (!["", ...CHOICES].includes(d.decision as string)) return "a draft has a decision that is not one of the choices";
  if (typeof d.field !== "string" || d.field.length > FIELD_MAX) return "a draft has a field that is not text or is too long";
  if (typeof d.proposed !== "string" || d.proposed.length > FIELD_MAX) return "a draft has a value that is not text or is too long";
  if (typeof d.neighbors !== "boolean") return "a draft has a neighbors answer that is not true or false";
  if (typeof d.note !== "string" || d.note.length > NOTE_MAX) return "a draft has a reason that is not text or is too long";
  if (isDangerous(d.field) ) return "a draft names a field it should not";
  return d as unknown as Draft;
}
const isDangerous = (k: string) => k === "__proto__" || k === "constructor" || k === "prototype";

// The stored text of one scan, checked against the list that is open now. Restored whole, or not at all.
export function parseRecovery(text: string, scanId: number, signature: string, rows: AssetRow[]): ParseResult {
  if (text.length > RECOVERY_MAX_CHARS) return corrupt("the saved file is far larger than any review");
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return corrupt("the saved file is not readable JSON");
  }
  if (!isRecord(raw)) return corrupt("the saved file is not an object");
  for (const k of Object.keys(raw)) if (!FILE_KEYS.includes(k)) return corrupt(`the saved file has a key it should not have (${k})`);
  for (const k of FILE_KEYS) if (!own(raw, k)) return corrupt(`the saved file has no ${k}`);
  if (raw.version !== RECOVERY_VERSION) return corrupt("the saved file is of another version");
  if (raw.kind !== RECOVERY_KIND) return corrupt("the saved file is not saved review work");
  if (raw.scan_id !== scanId) return corrupt("the saved file is for another scan");
  if (typeof raw.signature !== "string" || !isInstant(raw.saved_at)) return corrupt("the saved file has no valid source or time");
  if (typeof raw.reviewer !== "string" || raw.reviewer.length > REVIEWER_MAX) return corrupt("the saved reviewer name is not valid");
  if (typeof raw.selected !== "string" || !isRecord(raw.drafts) || !isRecord(raw.decisions) || !isRecord(raw.queue)) return corrupt("the saved file is not shaped like a review");
  const savedAt = raw.saved_at as string;
  if (raw.signature !== signature) return { status: "stale", savedAt };

  const rowsByKey = new Map(reviewableRows(rows).map((r) => [sourceOf(r)!.review_key, r]));
  const drafts: [string, Draft][] = [];
  for (const [key, value] of Object.entries(raw.drafts)) {
    if (!rowsByKey.has(key)) return corrupt("a saved draft is for a record that is not in this list");
    const draft = readDraft(value);
    if (typeof draft === "string") return corrupt(draft);
    drafts.push([key, draft]);
  }
  const decisions: [string, LedgerDecision][] = [];
  for (const [key, value] of Object.entries(raw.decisions)) {
    if (!rowsByKey.has(key) || !isRecord(value) || value.review_key !== key) return corrupt("a saved decision is not filed under its own record");
    decisions.push([key, value as unknown as LedgerDecision]);
  }
  // A decision is held to the rules of a journal, whoever saved it: the confirmations are not saved, they are checked.
  const check = validateJournal(rows, { schema_version: 1, scan_id: scanId, decisions: decisions.map(([, d]) => d) }, scanId);
  if (!check.ok) return corrupt(`a saved decision does not pass the rules: ${check.errors.slice(0, 2).join(" ")}`);
  if (raw.selected !== "" && !rowsByKey.has(raw.selected)) return corrupt("the saved selection is not a record of this list");

  const q = raw.queue;
  for (const k of Object.keys(q)) if (!QUEUE_KEYS.includes(k)) return corrupt(`the saved search has a key it should not have (${k})`);
  if (typeof q.query !== "string" || q.query.length > QUERY_MAX) return corrupt("the saved search text is not valid");
  if (!isFilter(q.filter)) return corrupt("the saved filter is not one of the filters");
  const items = new Set(rows.map((r) => r.item));
  const actions = new Set(rows.map((r) => r.action));
  if (typeof q.item !== "string" || (q.item !== "" && !items.has(q.item))) return corrupt("the saved item is not in this list");
  if (typeof q.action !== "string" || (q.action !== "" && !actions.has(q.action))) return corrupt("the saved action is not in this list");

  const state: RecoverableState = { reviewer: raw.reviewer, drafts: Object.fromEntries(drafts), decisions: Object.fromEntries(decisions),
    selected: raw.selected, queue: { query: q.query, item: q.item, filter: q.filter, action: q.action } };
  return { status: "restored", state, savedAt, decisions: decisions.length, drafts: drafts.length };
}

// The part of Storage that is used, so a test (or a browser that has none) can stand in.
export interface RecoveryStorage {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function browserStorage(): RecoveryStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // the browser refuses access (private mode, blocked site data)
  }
}

export type LoadResult = { status: "none" } | { status: "unavailable" } | ParseResult;
export type SaveReason = "full" | "unavailable" | "conflict";
export type SaveResult =
  | { ok: true; persistent: boolean; evicted: number[] }
  | { ok: false; persistent: false; reason: SaveReason; evicted: number[] };

function failure(error: unknown): "full" | "unavailable" {
  const e = error as { name?: string; code?: number } | null;
  return e && (e.name === "QuotaExceededError" || e.name === "NS_ERROR_DOM_QUOTA_REACHED" || e.code === 22 || e.code === 1014) ? "full" : "unavailable";
}

export function createRecoveryStore(getStorage: () => RecoveryStorage | null, now: () => string = () => new Date().toISOString()) {
  // A copy for this page only, used when the browser cannot keep one: moving to another scan and back still finds the work.
  const memory = new Map<number, string>();
  const unsynced = new Set<number>();
  // The stored text of each scan as this page last read or wrote it. Another tab that saved since is a conflict, not something to overwrite.
  const known = new Map<number, string | null>();
  const storage = (): RecoveryStorage | null => {
    try {
      return getStorage();
    } catch {
      return null;
    }
  };
  const keyOf = (scanId: number) => `${RECOVERY_PREFIX}${scanId}`;
  // undefined: the browser cannot be read
  const stored = (scanId: number): string | null | undefined => {
    const st = storage();
    if (!st) return undefined;
    try {
      return st.getItem(keyOf(scanId));
    } catch {
      return undefined;
    }
  };

  function entries(): { scanId: number; savedAt: string }[] {
    const found = new Map<number, string>();
    const readSaved = (text: string | null) => {
      try {
        const at = (JSON.parse(text ?? "") as { saved_at?: unknown }).saved_at;
        return typeof at === "string" ? at : "";
      } catch {
        return "";
      }
    };
    const st = storage();
    if (st) {
      try {
        for (let i = 0; i < st.length; i += 1) {
          const k = st.key(i);
          if (!k || !k.startsWith(RECOVERY_PREFIX)) continue;
          const id = Number(k.slice(RECOVERY_PREFIX.length));
          if (Number.isInteger(id)) found.set(id, readSaved(st.getItem(k)));
        }
      } catch {
        /* unreadable: the copy in memory is all there is */
      }
    }
    for (const [id, text] of memory) if (!found.has(id)) found.set(id, readSaved(text));
    return [...found].map(([scanId, savedAt]) => ({ scanId, savedAt }));
  }

  function removeOne(scanId: number): boolean {
    memory.delete(scanId);
    unsynced.delete(scanId);
    const st = storage();
    if (!st) return true;
    try {
      st.removeItem(keyOf(scanId));
      known.set(scanId, null);
      return true;
    } catch {
      return false;
    }
  }

  return {
    entries,

    // The stored text of one scan exactly as it is, for a person who wants to keep what new work is about to replace.
    raw(scanId: number): string | null {
      return stored(scanId) ?? memory.get(scanId) ?? null;
    },

    load(scanId: number, signature: string, rows: AssetRow[]): LoadResult {
      const text0 = stored(scanId);
      const readable = text0 !== undefined;
      if (readable) known.set(scanId, text0);
      // Work this page could not write is newer than whatever the browser still has.
      const text = unsynced.has(scanId) || (!readable && memory.has(scanId)) ? memory.get(scanId) ?? text0 ?? null : text0 ?? null;
      if (text === null) return readable ? { status: "none" } : { status: "unavailable" };
      return parseRecovery(text, scanId, signature, rows);
    },

    // Keeps the work, or removes the entry when there is no work left. Never throws: a failure is the result. A write goes first and
    // older scans are dropped only after it succeeded; work another tab saved since this page looked is never overwritten unless asked.
    save(scanId: number, signature: string, state: RecoverableState, options: { force?: boolean } = {}): SaveResult {
      const st = storage();
      const current = stored(scanId);
      const seen = known.get(scanId);
      const changedElsewhere = current !== undefined && (seen === undefined ? current !== null : current !== seen);
      if (!hasMeaningfulWork(state)) {
        const leave = changedElsewhere; // not this page's to delete
        const removed = leave ? true : removeOne(scanId);
        return { ok: true, persistent: Boolean(st) && removed, evicted: [] };
      }
      const text = serializeRecovery(scanId, signature, state, now());
      memory.set(scanId, text);
      const fail = (reason: SaveReason): SaveResult => {
        unsynced.add(scanId);
        return { ok: false, persistent: false, reason, evicted: [] };
      };
      if (!st) return fail("unavailable");
      if (changedElsewhere && !options.force && seen !== undefined) return fail("conflict");
      if (text.length > RECOVERY_MAX_CHARS) return fail("full");
      try {
        st.setItem(keyOf(scanId), text);
      } catch (error) {
        return fail(failure(error));
      }
      unsynced.delete(scanId);
      known.set(scanId, text);
      const evicted: number[] = [];
      const others = entries().filter((e) => e.scanId !== scanId).sort((a, b) => a.savedAt.localeCompare(b.savedAt));
      while (others.length + 1 > RECOVERY_LIMIT) {
        const old = others.shift()!;
        if (removeOne(old.scanId)) evicted.push(old.scanId);
      }
      return { ok: true, persistent: true, evicted };
    },

    clear(scanId: number): { ok: boolean } {
      return { ok: removeOne(scanId) };
    },

    clearAll(): { ok: boolean } {
      let ok = true;
      for (const { scanId } of entries()) ok = removeOne(scanId) && ok;
      memory.clear();
      unsynced.clear();
      return { ok };
    },
  };
}

// One store for the page, so what is kept in memory when the browser cannot keep it survives moving between scans.
export const recoveryStore = createRecoveryStore(browserStorage);
