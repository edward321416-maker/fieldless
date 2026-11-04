import { describe, expect, it } from "vitest";
import { row, source, treeRow } from "./ledgerFixtures";
import { DEFAULT_QUEUE, FILTERS, filterCounts, itemsOf, priorityOf, queueRows, searchText, type QueueSettings } from "./ledgerQuery";
import { reviewableRows, sourceOf } from "./ledgerReview";
import type { AssetRow, LedgerDecision } from "./types";

const key = (r: AssetRow) => sourceOf(r)!.review_key;
const ids = (rs: AssetRow[]) => rs.map((r) => sourceOf(r)!.record_id);
const mk = (n: number, over: Partial<AssetRow> = {}) => row({ city_record: `CBR-${n}`, city_location: `${n} Pine St`, ...over },
  source({ record_id: `CBR-${n}`, record_key: `fid:${n}`, review_key: `rk-${n}-` + "d".repeat(20) }));
const settings = (over: Partial<QueueSettings> = {}): QueueSettings => ({ ...DEFAULT_QUEUE, ...over });
const decided = (r: AssetRow, decision: LedgerDecision["decision"]): Record<string, LedgerDecision> => ({
  [key(r)]: { review_key: key(r), record_id: sourceOf(r)!.record_id, item: r.item, identity: "uncertain", evidence: "unclear", decision,
    field: null, proposed_value: null, neighbors_checked: false, reviewer: "A", note: "n", reviewed_at: "2026-11-04T12:00:00.000Z" },
});

// 94 rows in a list; the one that corrects the record is the last, as the old order would have put it.
const many = Array.from({ length: 93 }, (_, i) => mk(30000 + i, { action: "Update the date" }));
const correction = mk(39290, { action: "Correct the record" });
const list = reviewableRows([...many, correction]);

describe("the order of the queue", () => {
  it("puts Correct the record first, then identity or attribute doubt, then other candidates, then the rest", () => {
    const a = mk(1, { action: "No change" });
    const b = mk(2, { action: "Visit on site" });
    const c = mk(3, { action: "Update the date", attribute_field: "X", attribute_state: "unsettled" });
    const d = mk(4, { action: "Correct the record" });
    const e = treeRow(5, ["TRE-6 is 3 m away and was photographed 2 s apart."]);
    const f = mk(7, { action: "Update the date", identity_state: "unresolved" });
    const rs = reviewableRows([a, b, c, d, e, f]);
    expect(queueRows(rs, {}, settings()).map((r) => priorityOf(r))).toEqual([0, 1, 1, 1, 2, 3]);
    expect(ids(queueRows(rs, {}, settings()))[0]).toBe("CBR-4");
    expect(ids(queueRows(rs, {}, settings())).slice(-2)).toEqual(["CBR-1", "CBR-2"]);
  });

  it("keeps the list's own order inside a rank, so the order is stable", () => {
    const rs = reviewableRows([mk(10, { action: "No change" }), mk(11, { action: "No change" }), mk(12, { action: "No change" })]);
    expect(ids(queueRows(rs, {}, settings()))).toEqual(["CBR-10", "CBR-11", "CBR-12"]);
  });

  it("opens the correction first in a list of 94 rows", () => {
    expect(ids(queueRows(list, {}, settings()))[0]).toBe("CBR-39290");
  });
});

describe("search", () => {
  it("finds a record by its ID, whatever the case", () => {
    expect(ids(queueRows(list, {}, settings({ query: "cbr-39290" })))).toEqual(["CBR-39290"]);
  });

  it("finds a record by part of its location", () => {
    expect(ids(queueRows(list, {}, settings({ query: "  39290 PINE " })))).toEqual(["CBR-39290"]);
  });

  it("finds a record by an ID of another record it could be confused with", () => {
    const r = mk(50, { competing_record_ids: ["CBR-777"] });
    expect(ids(queueRows(reviewableRows([r, mk(51)]), {}, settings({ query: "CBR-777" })))).toEqual(["CBR-50"]);
  });

  it("puts an exact ID before the rows that only contain it", () => {
    const rs = reviewableRows([mk(5, { action: "Correct the record" }), mk(55), mk(500)]);
    expect(ids(queueRows(rs, {}, settings({ query: "CBR-55" })))[0]).toBe("CBR-55");
  });

  it("finds nothing for text no row has, and never throws on odd input", () => {
    for (const q of ["zzz", "(", "[", "\\", ".*", "%", "\u0000"]) expect(() => queueRows(list, {}, settings({ query: q }))).not.toThrow();
    expect(queueRows(list, {}, settings({ query: "zzz" }))).toEqual([]);
  });

  it("searches the whole list even while an action is chosen", () => {
    expect(ids(queueRows(list, {}, settings({ action: "Update the date", query: "CBR-39290" })))).toEqual(["CBR-39290"]);
  });

  it("reads the text a row is searched by", () => {
    expect(searchText(correction)).toContain("cbr-39290");
    expect(searchText(correction)).toContain("pine st");
  });
});

describe("filters", () => {
  const unresolved = mk(20, { action: "Visit on site", identity_state: "unresolved", identity_reason: "two records" });
  const unclear = mk(21, { action: "Visit on site", evidence_quality: "unclear" });
  const warned = treeRow(22, ["TRE-23 is 3 m away and was photographed 2 s apart."]);
  const plain = mk(24, { action: "No change" });
  const fix = mk(25, { action: "Correct the record" });
  const rs = reviewableRows([unresolved, unclear, warned, plain, fix]);

  it("has the seven states of the spec", () => {
    expect([...FILTERS]).toEqual(["all", "candidates", "changes", "identity", "unclear", "hold", "unreviewed"]);
  });

  it("candidates: rows a desk review could settle", () => {
    expect(ids(queueRows(rs, {}, settings({ filter: "candidates" }))).sort()).toEqual(["CBR-24", "CBR-25", "TRE-22"]);
  });

  it("changes: rows that correct the record", () => {
    expect(ids(queueRows(rs, {}, settings({ filter: "changes" })))).toEqual(["CBR-25"]);
  });

  it("identity: unresolved identity, competing records or a neighbour warning", () => {
    expect(ids(queueRows(rs, {}, settings({ filter: "identity" }))).sort()).toEqual(["CBR-20", "TRE-22"]);
  });

  it("unclear: evidence that is not clear or an attribute that is open", () => {
    const open = mk(26, { attribute_state: "not_checked" });
    expect(ids(queueRows(reviewableRows([...rs, open]), {}, settings({ filter: "unclear" }))).sort()).toEqual(["CBR-21", "CBR-26"]);
  });

  it("not_flagged and not_reviewed are not doubt and not a reason to rank a row up", () => {
    const quiet = mk(27, { action: "No change", identity_state: "not_flagged", evidence_quality: "not_reviewed" });
    expect(priorityOf(quiet)).toBe(2);
    expect(queueRows(reviewableRows([quiet]), {}, settings({ filter: "identity" }))).toEqual([]);
    expect(queueRows(reviewableRows([quiet]), {}, settings({ filter: "unclear" }))).toEqual([]);
  });

  it("hold and unreviewed follow the recorded decisions, and are not changed by filtering", () => {
    const decisions = { ...decided(plain, "hold"), ...decided(fix, "observe") };
    expect(ids(queueRows(rs, decisions, settings({ filter: "hold" })))).toEqual(["CBR-24"]);
    expect(ids(queueRows(rs, decisions, settings({ filter: "unreviewed" }))).sort()).toEqual(["CBR-20", "CBR-21", "TRE-22"]);
    expect(Object.keys(decisions)).toHaveLength(2); // the decisions are only read
  });

  it("narrows by item and by action together with the state", () => {
    expect(ids(queueRows(rs, {}, settings({ item: "street_tree" })))).toEqual(["TRE-22"]);
    expect(ids(queueRows(rs, {}, settings({ action: "No change" })))).toEqual(["CBR-24"]);
    expect(ids(queueRows(rs, {}, settings({ item: "street_tree", filter: "changes" })))).toEqual([]);
  });

  it("counts every state for the controls", () => {
    expect(filterCounts(rs, {})).toMatchObject({ all: 5, candidates: 3, changes: 1, identity: 2, unclear: 1, hold: 0, unreviewed: 5 });
  });

  it("lists the items that are in the list, once each", () => {
    expect(itemsOf(rs)).toEqual([{ item: "curb_ramp", asset: "Curb ramp" }, { item: "street_tree", asset: "Street tree" }]);
  });
});
