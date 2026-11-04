import { describe, expect, it } from "vitest";
import { NOW, row, source, treeRow } from "./ledgerFixtures";
import { DEFAULT_QUEUE } from "./ledgerQuery";
import {
  RECOVERY_LIMIT, RECOVERY_PREFIX, createRecoveryStore, emptyState, hasMeaningfulWork, parseRecovery, serializeRecovery,
  type RecoverableState, type RecoveryStorage,
} from "./ledgerRecovery";
import { EMPTY_DRAFT, decisionFromDraft, reviewSignature, sourceOf, type Draft } from "./ledgerReview";
import type { AssetRow, LedgerDecision } from "./types";

class FakeStorage implements RecoveryStorage {
  data = new Map<string, string>();
  failWrites: "full" | "denied" | null = null;
  failReads = false;
  get length() { return this.data.size; }
  key(i: number) { return [...this.data.keys()][i] ?? null; }
  getItem(k: string) {
    if (this.failReads) throw new DOMException("denied", "SecurityError");
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    if (this.failWrites === "full") throw new DOMException("full", "QuotaExceededError");
    if (this.failWrites === "denied") throw new DOMException("denied", "SecurityError");
    this.data.set(k, v);
  }
  removeItem(k: string) { this.data.delete(k); }
}

const ramp = row();
const tree = treeRow(53943, ["TRE-53948 is 3 m away and was photographed 2 s apart."]);
const rows: AssetRow[] = [ramp, tree];
const SCAN = 17;
const sig = reviewSignature(SCAN, rows);
const rkRamp = sourceOf(ramp)!.review_key;
const rkTree = sourceOf(tree)!.review_key;
const CHANGE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "change", field: "HAS_DETECTABLE_WARNING", proposed: "Yes",
  note: "The panel is visible." };

function decide(r: AssetRow, draft: Draft): LedgerDecision {
  const made = decisionFromDraft(r, draft, "A. Reviewer", NOW);
  if (!("decision" in made)) throw new Error(made.problems.join(" "));
  return made.decision;
}
function work(over: Partial<RecoverableState> = {}): RecoverableState {
  return { ...emptyState(), reviewer: "A. Reviewer", drafts: { [rkRamp]: CHANGE, [rkTree]: { ...EMPTY_DRAFT, note: "still thinking" } },
    decisions: { [rkRamp]: decide(ramp, CHANGE) }, selected: rkTree, queue: { ...DEFAULT_QUEUE, filter: "identity", item: "street_tree", query: "TRE" },
    ...over };
}
const store = (s: FakeStorage | null = new FakeStorage()) => ({ s, store: createRecoveryStore(() => s, () => NOW) });
const file = (over: Record<string, unknown> = {}) => JSON.stringify({ ...JSON.parse(serializeRecovery(SCAN, sig, work(), NOW)), ...over });

describe("saving and restoring work", () => {
  it("restores drafts, recorded decisions, the selected record and the queue settings of the same source", () => {
    const { store: st } = store();
    expect(st.save(SCAN, sig, work())).toMatchObject({ ok: true, persistent: true });
    const got = st.load(SCAN, sig, rows);
    expect(got.status).toBe("restored");
    if (got.status !== "restored") return;
    expect(got.state).toEqual(work());
    expect(got.decisions).toBe(1);
    expect(got.drafts).toBe(2);
    expect(got.savedAt).toBe(NOW);
  });

  it("keeps each scan on its own", () => {
    const { store: st } = store();
    st.save(17, sig, work());
    st.save(18, "other", work({ reviewer: "B" }));
    expect(st.entries().map((e) => e.scanId).sort()).toEqual([17, 18]);
    expect(st.load(17, sig, rows).status).toBe("restored");
    expect(st.load(19, sig, rows).status).toBe("none");
  });

  it("has nothing to restore before anything was saved", () => {
    expect(store().store.load(SCAN, sig, rows)).toEqual({ status: "none" });
  });

  it("does not store a reviewer name alone, and removes the entry once the work is gone", () => {
    const { s, store: st } = store();
    expect(hasMeaningfulWork({ ...emptyState(), reviewer: "A" })).toBe(false);
    st.save(SCAN, sig, { ...emptyState(), reviewer: "A" });
    expect(s!.data.size).toBe(0);
    st.save(SCAN, sig, work());
    expect(s!.data.size).toBe(1);
    st.save(SCAN, sig, { ...emptyState(), reviewer: "A" });
    expect(s!.data.size).toBe(0);
  });

  it("counts a draft with only a reason, and a decision, as work", () => {
    expect(hasMeaningfulWork({ ...emptyState(), drafts: { a: { ...EMPTY_DRAFT, note: "x" } } })).toBe(true);
    expect(hasMeaningfulWork({ ...emptyState(), drafts: { a: { ...EMPTY_DRAFT } } })).toBe(false);
    expect(hasMeaningfulWork(work({ drafts: {} }))).toBe(true);
  });
});

describe("a source that changed", () => {
  it("is not restored: old work is unavailable for this version, and left where it is", () => {
    const { s, store: st } = store();
    st.save(SCAN, sig, work());
    const newer = reviewSignature(SCAN, [row({}, source({ properties_sha256: "f".repeat(64) })), tree]);
    expect(st.load(SCAN, newer, rows)).toMatchObject({ status: "stale" });
    expect(s!.data.size).toBe(1);
  });

  it("is told apart from damage", () => {
    expect(parseRecovery(serializeRecovery(SCAN, sig, work(), NOW), SCAN, "changed", rows).status).toBe("stale");
  });
});

describe("a stored file that cannot be trusted", () => {
  const bad = (text: string) => parseRecovery(text, SCAN, sig, rows);
  const draftOf = (over: Record<string, unknown>) => file({ drafts: { [rkRamp]: { ...CHANGE, ...over } } });

  it.each([
    ["not JSON", "{not json"],
    ["an array", "[]"],
    ["null", "null"],
    ["another version", file({ version: 2 })],
    ["another kind", file({ kind: "something-else" })],
    ["another scan", file({ scan_id: 18 })],
    ["a key it does not have", file({ extra: true })],
    ["a missing key", JSON.stringify({ ...JSON.parse(file()), queue: undefined })],
    ["a time that is not an instant", file({ saved_at: "yesterday" })],
    ["a reviewer that is too long", file({ reviewer: "x".repeat(201) })],
    ["a draft for a record that is not in the list", file({ drafts: { "rk-not-here": CHANGE } })],
    ["a draft with a key it does not have", draftOf({ approved: true })],
    ["a draft with a wrong choice", draftOf({ decision: "approve" })],
    ["a draft with a note that is too long", draftOf({ note: "x".repeat(2001) })],
    ["a draft with a note that is not text", draftOf({ note: 5 })],
    ["a draft whose neighbors is not true or false", draftOf({ neighbors: "yes" })],
    ["a draft field that is far too long", draftOf({ field: "f".repeat(500) })],
    ["a selected record that is not in the list", file({ selected: "rk-not-here" })],
    ["a filter it does not know", file({ queue: { ...DEFAULT_QUEUE, filter: "everything" } })],
    ["a search that is too long", file({ queue: { ...DEFAULT_QUEUE, query: "q".repeat(500) } })],
    ["an item the list does not have", file({ queue: { ...DEFAULT_QUEUE, item: "bridge" } })],
    ["a decision the rules reject", file({ decisions: { [rkRamp]: { ...decide(ramp, CHANGE), evidence: "unclear" } } })],
    ["a decision filed under another record's key", file({ decisions: { [rkTree]: decide(ramp, CHANGE) } })],
    ["a decision for a record that is not in the list", file({ decisions: { "rk-not-here": { ...decide(ramp, CHANGE), review_key: "rk-not-here" } } })],
    ["a decision with a key it does not have", file({ decisions: { [rkRamp]: { ...decide(ramp, CHANGE), approved: true } } })],
    ["a file far too large", `{"x":"${"y".repeat(3_000_000)}"}`],
  ])("rejects %s", (_name, text) => {
    expect(bad(text).status).toBe("corrupt");
  });

  it("does not let a dangerous key become an assignment", () => {
    for (const k of ["__proto__", "constructor", "prototype"]) {
      const text = `{"version":1,"kind":"fieldless-ledger-recovery","scan_id":17,"signature":${JSON.stringify(sig)},"saved_at":"${NOW}","reviewer":"",`
        + `"drafts":{"${k}":{"note":"x"}},"decisions":{},"selected":"","queue":${JSON.stringify(DEFAULT_QUEUE)}}`;
      expect(bad(text).status).toBe("corrupt");
      expect(({} as Record<string, unknown>).note).toBeUndefined();
      expect(Object.keys(Object.prototype)).toEqual([]);
    }
  });

  it("restores nothing at all when one part is bad", () => {
    const got = bad(file({ decisions: { [rkRamp]: { ...decide(ramp, CHANGE), evidence: "unclear" } } }));
    expect(got).toMatchObject({ status: "corrupt" });
    expect("state" in got).toBe(false);
  });

  it("never turns a draft into a recorded decision", () => {
    const got = bad(file({ decisions: {} }));
    expect(got.status).toBe("restored");
    if (got.status === "restored") {
      expect(got.state.decisions).toEqual({});
      expect(Object.keys(got.state.drafts)).toHaveLength(2);
    }
  });

  it("rejects a source row with a dangerous review key, so it can never be a key", () => {
    const hostile = row({}, source({ review_key: "__proto__" }));
    expect(sourceOf(hostile)).toBeNull();
    expect(sourceOf(row({}, source({ editable_fields: { constructor: { before: "No", yes: "Yes", no: "No", question: "?", suggested: null } } })))).toBeNull();
  });

  it("is reported on load and left in place so it can be cleared on purpose", () => {
    const { s, store: st } = store();
    s!.data.set(`${RECOVERY_PREFIX}${SCAN}`, "{not json");
    expect(st.load(SCAN, sig, rows)).toMatchObject({ status: "corrupt" });
    expect(s!.data.size).toBe(1);
    expect(st.clear(SCAN).ok).toBe(true);
    expect(s!.data.size).toBe(0);
  });
});

describe("storage that fails", () => {
  it("says so when the browser has no storage, and still keeps the work for this page", () => {
    const { store: st } = store(null);
    expect(st.save(SCAN, sig, work())).toMatchObject({ ok: false, reason: "unavailable", persistent: false });
    expect(st.load(SCAN, sig, rows).status).toBe("restored"); // moving to another scan and back keeps it
  });

  it("says so when storage is full, and does not throw", () => {
    const { s, store: st } = store();
    s!.failWrites = "full";
    expect(st.save(SCAN, sig, work())).toMatchObject({ ok: false, reason: "full", persistent: false });
    expect(st.load(SCAN, sig, rows).status).toBe("restored");
  });

  it("says so when writing is refused", () => {
    const { s, store: st } = store();
    s!.failWrites = "denied";
    expect(st.save(SCAN, sig, work())).toMatchObject({ ok: false, reason: "unavailable" });
  });

  it("says so when reading is refused", () => {
    const { s, store: st } = store();
    st.save(SCAN, sig, work());
    s!.failReads = true;
    expect(st.load(SCAN, sig, rows).status === "unavailable" || st.load(SCAN, sig, rows).status === "restored").toBe(true);
    expect(() => st.entries()).not.toThrow();
    expect(() => st.clearAll()).not.toThrow();
  });

  it("does not throw when the browser refuses access to storage at all", () => {
    const st = createRecoveryStore(() => { throw new DOMException("denied", "SecurityError"); }, () => NOW);
    expect(st.save(SCAN, sig, work()).ok).toBe(false);
    expect(st.load(19, sig, rows).status).toBe("unavailable");
  });
});

describe("how much is kept", () => {
  it("keeps the three demo scans and the active one, and drops the oldest of the others", () => {
    let t = 0;
    const s = new FakeStorage();
    const st = createRecoveryStore(() => s, () => new Date(Date.UTC(2026, 10, 4, 12, 0, t++)).toISOString());
    for (const id of [1, 2, 3, 4]) st.save(id, `sig${id}`, work());
    expect(RECOVERY_LIMIT).toBe(4);
    const result = st.save(5, "sig5", work());
    expect(result).toMatchObject({ ok: true, evicted: [1] });
    expect(st.entries().map((e) => e.scanId).sort()).toEqual([2, 3, 4, 5]);
  });

  it("never drops the scan being saved", () => {
    let t = 0;
    const st = createRecoveryStore(() => new FakeStorage(), () => new Date(Date.UTC(2026, 10, 4, 12, 0, t++)).toISOString());
    for (const id of [1, 2, 3, 4]) st.save(id, `sig${id}`, work());
    st.save(1, "sig1", work({ reviewer: "later" }));
    expect(st.entries().map((e) => e.scanId)).toContain(1);
  });
});

describe("clearing saved work", () => {
  it("clears one scan or every scan, and touches nothing else in storage", () => {
    const { s, store: st } = store();
    s!.data.set("unrelated", "keep me");
    st.save(17, sig, work());
    st.save(18, "x", work());
    expect(st.clear(17).ok).toBe(true);
    expect(st.entries().map((e) => e.scanId)).toEqual([18]);
    expect(st.load(17, sig, rows).status).toBe("none");
    st.clearAll();
    expect(st.entries()).toEqual([]);
    expect(s!.data.get("unrelated")).toBe("keep me");
  });

  it("also clears what was only kept in memory", () => {
    const { store: st } = store(null);
    st.save(SCAN, sig, work());
    st.clear(SCAN);
    expect(st.load(SCAN, sig, rows)).toMatchObject({ status: expect.stringMatching(/none|unavailable/) });
  });
});

describe("the newest work wins", () => {
  it("restores the newer work kept in memory when the latest write failed after an earlier success", () => {
    const { s, store: st } = store();
    st.save(SCAN, sig, work({ drafts: { [rkTree]: { ...EMPTY_DRAFT, note: "v1 older" } }, decisions: {} }));
    s!.failWrites = "full";
    expect(st.save(SCAN, sig, work({ drafts: { [rkTree]: { ...EMPTY_DRAFT, note: "v2 newer" } }, decisions: {} })).ok).toBe(false);
    const got = st.load(SCAN, sig, rows);
    expect(got.status).toBe("restored");
    if (got.status === "restored") expect(got.state.drafts[rkTree].note).toBe("v2 newer");
  });

  it("goes back to the disk copy once a later write succeeds", () => {
    const { s, store: st } = store();
    st.save(SCAN, sig, work({ reviewer: "one" }));
    s!.failWrites = "full";
    st.save(SCAN, sig, work({ reviewer: "two" }));
    s!.failWrites = null;
    st.save(SCAN, sig, work({ reviewer: "three" }));
    const got = st.load(SCAN, sig, rows);
    if (got.status === "restored") expect(got.state.reviewer).toBe("three");
    expect(got.status).toBe("restored");
  });
});

describe("a failed write deletes nothing", () => {
  const fill = () => {
    let t = 0;
    const s = new FakeStorage();
    const st = createRecoveryStore(() => s, () => new Date(Date.UTC(2026, 10, 4, 12, 0, t++)).toISOString());
    for (const id of [1, 2, 3, 4]) st.save(id, `s${id}`, work());
    return { s, st };
  };

  it("keeps the older scans when the fifth cannot be written", () => {
    const { s, st } = fill();
    s.failWrites = "full";
    const r = st.save(5, "s5", work());
    expect(r).toMatchObject({ ok: false, evicted: [] });
    expect([...s.data.keys()].sort()).toEqual([1, 2, 3, 4].map((id) => `${RECOVERY_PREFIX}${id}`)); // what was saved is all still there
    expect(st.load(5, "s5", rows).status).not.toBe("none"); // and the fifth is kept for this page
  });

  it("drops the oldest only after the fifth is written", () => {
    const { st } = fill();
    expect(st.save(5, "s5", work())).toMatchObject({ ok: true, evicted: [1] });
    expect(st.entries().map((e) => e.scanId).sort()).toEqual([2, 3, 4, 5]);
  });
});

describe("another tab on the same storage", () => {
  const two = () => {
    const s = new FakeStorage();
    return { s, a: createRecoveryStore(() => s, () => NOW), b: createRecoveryStore(() => s, () => NOW) };
  };

  it("is not overwritten by a tab that opened earlier and has nothing", () => {
    const { s, a, b } = two();
    b.load(SCAN, sig, rows); // tab B opened before A saved anything
    a.load(SCAN, sig, rows);
    a.save(SCAN, sig, work());
    b.save(SCAN, sig, emptyState()); // B's page has no work: it must not delete A's
    expect(s.data.size).toBe(1);
    expect(a.load(SCAN, sig, rows).status).toBe("restored");
  });

  it("reports a conflict instead of overwriting work it has not seen", () => {
    const { s, a, b } = two();
    b.load(SCAN, sig, rows);
    a.load(SCAN, sig, rows);
    a.save(SCAN, sig, work());
    const before = [...s.data.values()][0];
    expect(b.save(SCAN, sig, work({ reviewer: "B" }))).toMatchObject({ ok: false, reason: "conflict", persistent: false });
    expect([...s.data.values()][0]).toBe(before);
  });

  it("overwrites on purpose when asked", () => {
    const { s, a, b } = two();
    b.load(SCAN, sig, rows);
    a.save(SCAN, sig, work());
    expect(b.save(SCAN, sig, work({ reviewer: "B" }), { force: true })).toMatchObject({ ok: true });
    expect(JSON.parse([...s.data.values()][0]).reviewer).toBe("B");
  });

  it("keeps saving its own work without a conflict", () => {
    const { a } = two();
    a.load(SCAN, sig, rows);
    expect(a.save(SCAN, sig, work({ reviewer: "1" })).ok).toBe(true);
    expect(a.save(SCAN, sig, work({ reviewer: "2" })).ok).toBe(true);
  });
});

describe("the older file that new work would replace", () => {
  it("can be read back as it was stored, whatever is wrong with it", () => {
    const { s, store: st } = store();
    s!.data.set(`${RECOVERY_PREFIX}${SCAN}`, "{broken");
    expect(st.raw(SCAN)).toBe("{broken");
    expect(store().store.raw(SCAN)).toBeNull();
  });
});
