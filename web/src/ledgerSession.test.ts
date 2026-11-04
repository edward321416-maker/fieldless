import { describe, expect, it } from "vitest";
import { NOW, row, source, treeRow } from "./ledgerFixtures";
import { DEFAULT_QUEUE } from "./ledgerQuery";
import { createRecoveryStore, emptyState, type RecoverableState, type RecoveryStorage } from "./ledgerRecovery";
import { EMPTY_DRAFT, decisionFromDraft, reviewSignature, sourceOf, type Draft } from "./ledgerReview";
import { clearSaved, needsLeaveWarning, startSession, syncRecovery } from "./ledgerSession";
import type { AssetRow } from "./types";

class Mem implements RecoveryStorage {
  data = new Map<string, string>();
  full = false;
  get length() { return this.data.size; }
  key(i: number) { return [...this.data.keys()][i] ?? null; }
  getItem(k: string) { return this.data.get(k) ?? null; }
  setItem(k: string, v: string) {
    if (this.full) throw new DOMException("full", "QuotaExceededError");
    this.data.set(k, v);
  }
  removeItem(k: string) { this.data.delete(k); }
}
const make = (m: RecoveryStorage | null = new Mem()) => createRecoveryStore(() => m, () => NOW);

const ramp = row({ action: "Correct the record" });
const tree = treeRow(53943, ["TRE-53948 is 3 m away and was photographed 2 s apart."]);
const rows: AssetRow[] = [ramp, tree];
const SCAN = 17;
const sig = reviewSignature(SCAN, rows);
const rk = sourceOf(ramp)!.review_key;
const CHANGE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "change", field: "HAS_DETECTABLE_WARNING", proposed: "Yes", note: "Visible." };
function worked(): RecoverableState {
  const made = decisionFromDraft(ramp, CHANGE, "Dana", NOW);
  if (!("decision" in made)) throw new Error("fixture");
  return { ...emptyState(), reviewer: "Dana", drafts: { [rk]: CHANGE }, decisions: { [rk]: made.decision }, selected: rk };
}

describe("starting a session", () => {
  it("begins empty, with nothing to say, when nothing was saved", () => {
    const s = startSession(make(), SCAN, sig, rows, "", null);
    expect(s.state).toEqual(emptyState());
    expect(s.notices).toEqual([]);
    expect(s.problem).toBe("");
    expect(s.owns).toBe(true);
  });

  it("restores saved work and says what came back, and that the decisions are still self-asserted", () => {
    const st = make();
    st.save(SCAN, sig, worked());
    const s = startSession(st, SCAN, sig, rows, "", null);
    expect(s.state).toEqual(worked());
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].text).toContain("Restored");
    expect(s.notices[0].text).toContain("1 recorded decision");
    expect(s.notices[0].text).toMatch(/not (a )?municipal/i);
  });

  it("does not restore work from an older version of the list, says so, and leaves the file alone", () => {
    const m = new Mem();
    const st = make(m);
    st.save(SCAN, "an older signature", worked());
    const s = startSession(st, SCAN, sig, rows, "", null);
    expect(s.state).toEqual(emptyState());
    expect(s.notices[0].text).toMatch(/unavailable for this version/i);
    expect(s.notices[0].text).toMatch(/import/i);
    expect(s.notices[0].tone).toBe("warn");
    expect(s.owns).toBe(false);
    expect(m.data.size).toBe(1);
  });

  it("does not restore a damaged file, and says so", () => {
    const m = new Mem();
    m.data.set("fieldless.ledger.recovery.v1:17", "{broken");
    const s = startSession(make(m), SCAN, sig, rows, "", null);
    expect(s.state).toEqual(emptyState());
    expect(s.notices[0].text).toMatch(/could not be read/i);
    expect(s.owns).toBe(false);
  });

  it("tells the reviewer when the browser cannot keep anything", () => {
    const s = startSession(make(null), SCAN, sig, rows, "", null);
    expect(s.problem).toBe("unavailable");
    expect(s.notices[0].text).toMatch(/cannot keep|can't keep/i);
    expect(s.notices[0].text).toMatch(/download/i);
  });

  it("opens on the action chosen elsewhere, with the whole list one choice away", () => {
    const s = startSession(make(), SCAN, sig, rows, "Update the date", null);
    expect(s.state.queue).toEqual({ ...DEFAULT_QUEUE, action: "Update the date" });
  });

  it("lets restored queue settings win over the action chosen elsewhere", () => {
    const st = make();
    st.save(SCAN, sig, { ...worked(), queue: { ...DEFAULT_QUEUE, action: "" } });
    expect(startSession(st, SCAN, sig, rows, "Update the date", null).state.queue.action).toBe("");
  });

  it("keeps the work already in the page, and does not announce a restore again", () => {
    const st = make();
    st.save(SCAN, sig, { ...worked(), reviewer: "from storage" });
    const s = startSession(st, SCAN, sig, rows, "", { ...worked(), reviewer: "in the page" });
    expect(s.state.reviewer).toBe("in the page");
    expect(s.notices).toEqual([]);
  });
});

describe("coming back to the page after the map", () => {
  it("still says an older file is unavailable, and still does not own it", () => {
    const m = new Mem();
    const st = make(m);
    st.save(SCAN, "an older signature", worked());
    const s = startSession(st, SCAN, sig, rows, "", emptyState());
    expect(s.notices.map((n) => n.id)).toEqual(["stale"]);
    expect(s.owns).toBe(false);
    syncRecovery(st, SCAN, sig, s.state, s.owns);
    expect(m.data.size).toBe(1); // nothing removed behind the reviewer's back
  });
});

describe("keeping work as it changes", () => {
  it("saves meaningful work and reports no problem", () => {
    const m = new Mem();
    const r = syncRecovery(make(m), SCAN, sig, worked(), true);
    expect(r).toMatchObject({ owns: true, problem: "" });
    expect(m.data.size).toBe(1);
  });

  it("does not touch a file it could not restore until there is new work to put in its place", () => {
    const m = new Mem();
    const st = make(m);
    st.save(SCAN, "older", worked());
    const before = [...m.data.values()][0];
    expect(syncRecovery(st, SCAN, sig, emptyState(), false)).toMatchObject({ owns: false });
    expect([...m.data.values()][0]).toBe(before);
    expect(syncRecovery(st, SCAN, sig, worked(), false)).toMatchObject({ owns: true });
    expect([...m.data.values()][0]).not.toBe(before);
  });

  it("reports a full or unavailable browser store, and keeps the work for this page", () => {
    const m = new Mem();
    m.full = true;
    const st = make(m);
    expect(syncRecovery(st, SCAN, sig, worked(), true).problem).toBe("full");
    expect(syncRecovery(make(null), SCAN, sig, worked(), true).problem).toBe("unavailable");
    expect(startSession(st, SCAN, sig, rows, "", null).state).toEqual(worked()); // back from another scan
  });

  it("reports the scans it had to drop to stay within its limit", () => {
    const st = make();
    for (const id of [1, 2, 3, 4]) st.save(id, `s${id}`, worked());
    expect(syncRecovery(st, 5, "s5", worked(), true).evicted).toHaveLength(1);
  });
});

describe("leaving the page", () => {
  it("warns only when there is work that could not be saved", () => {
    expect(needsLeaveWarning(worked(), "")).toBe(false);
    expect(needsLeaveWarning(emptyState(), "full")).toBe(false);
    expect(needsLeaveWarning({ ...emptyState(), reviewer: "A" }, "unavailable")).toBe(false);
    expect(needsLeaveWarning(worked(), "full")).toBe(true);
    expect(needsLeaveWarning(worked(), "unavailable")).toBe(true);
  });
});

describe("clearing saved work", () => {
  it("clears this scan or all of them", () => {
    const st = make();
    st.save(17, sig, worked());
    st.save(18, "x", worked());
    expect(clearSaved(st, 17, false).ok).toBe(true);
    expect(st.entries().map((e) => e.scanId)).toEqual([18]);
    clearSaved(st, 17, true);
    expect(st.entries()).toEqual([]);
  });
});

it("builds on the fixtures the other tests use", () => {
  expect(source().version).toBe(1);
});
