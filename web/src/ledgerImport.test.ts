import { describe, expect, it } from "vitest";
import { IMPORT_MAX_CHARS, applyImport, planImport, type ImportPlan } from "./ledgerImport";
import { NOW, row, source, treeRow } from "./ledgerFixtures";
import { DEFAULT_QUEUE } from "./ledgerQuery";
import { buildReviewPackage, readReviewPackage } from "./ledgerPackage";
import { emptyState, hasMeaningfulWork, type RecoverableState } from "./ledgerRecovery";
import { EMPTY_DRAFT, buildJournal, decisionFromDraft, exportFiles, reviewSignature, sourceOf, type Draft, type LedgerCheck } from "./ledgerReview";
import type { AssetRow, LedgerDecision, LedgerJournal } from "./types";

const ramp = row();
const tree = treeRow(53943, ["TRE-53948 is 3 m away and was photographed 2 s apart."]);
const rows: AssetRow[] = [ramp, tree];
const SCAN = 17;
const check: LedgerCheck = { scan_id: SCAN, rows };
const rk = (r: AssetRow) => sourceOf(r)!.review_key;
const CHANGE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "change", field: "HAS_DETECTABLE_WARNING", proposed: "Yes", note: "Visible." };
const OBSERVE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "observe", neighbors: true, note: "Same tree." };

function decide(r: AssetRow, d: Draft, who = "Dana"): LedgerDecision {
  const made = decisionFromDraft(r, d, who, NOW);
  if (!("decision" in made)) throw new Error(made.problems.join(" "));
  return made.decision;
}
const journal = (...decisions: LedgerDecision[]): LedgerJournal => buildJournal(SCAN, decisions);
const text = (v: unknown) => JSON.stringify(v);
const readPackage = (value: unknown) => {
  const v = value as { source: LedgerCheck; journal: LedgerJournal };
  if ((value as { kind?: string }).kind !== "fieldless-ledger-review") throw new Error("not a package");
  return { source: v.source, journal: v.journal };
};
const pkg = (over: Record<string, unknown> = {}) => ({ schema_version: 1, kind: "fieldless-ledger-review", scan_id: SCAN, source: check,
  journal: journal(decide(ramp, CHANGE)), manifest: {}, files: {}, ...over });
const plan = (input: string, current: RecoverableState = emptyState(), rs: AssetRow[] = rows) =>
  planImport(input, { scanId: SCAN, rows: rs, current, readPackage });
const good = (p: ImportPlan) => {
  if (!p.ok) throw new Error(p.errors.join(" "));
  return p;
};

describe("importing a journal", () => {
  it("accepts a journal made on this list and previews each decision", () => {
    const p = good(plan(text(journal(decide(ramp, CHANGE), decide(tree, OBSERVE)))));
    expect(p.kind).toBe("journal");
    expect(p.counts).toEqual({ changes: 1, observations: 1, holds: 0 });
    expect(p.lines).toHaveLength(2);
    expect(p.lines[0].text).toContain("RAMP-12");
    expect(p.lines[0].text).toContain("HAS_DETECTABLE_WARNING");
    expect(p.lines[0].text).toContain("No → Yes");
    expect(p.lines[1].text).toContain("TRE-53943");
    expect(p.lines[1].text).toMatch(/observe/i);
  });

  it("accepts a journal exactly as the app exports it", () => {
    const j = journal(decide(ramp, CHANGE));
    const exported = exportFiles(rows, j, SCAN)[0].text;
    expect(good(plan(exported)).counts.changes).toBe(1);
  });

  it("says a journal holds no drafts, and that restored decisions are the reviewer's own word", () => {
    const p = good(plan(text(journal(decide(ramp, CHANGE)))));
    expect(p.notes.join(" ")).toMatch(/drafts/i);
    expect(p.notes.join(" ")).toMatch(/self-asserted|not .*municipal/i);
  });

  it("rejects a journal for another scan", () => {
    const p = plan(text({ ...journal(decide(ramp, CHANGE)), scan_id: 18 }));
    expect(p.ok).toBe(false);
    if (!p.ok) expect(p.errors.join(" ")).toMatch(/scan 18/);
  });

  it("rejects a decision for a record that is not in the open list", () => {
    const other = row({}, source({ review_key: "rk-other-" + "e".repeat(20), record_id: "RAMP-99" }));
    const j = journal(decide(other, CHANGE));
    expect(plan(text(j)).ok).toBe(false);
  });

  it("rejects a decision the rules reject: no one is approved by a file", () => {
    const weak = { ...decide(ramp, CHANGE), evidence: "unclear" };
    expect(plan(text(journal(weak as LedgerDecision))).ok).toBe(false);
    const noNeighbors = { ...decide(tree, OBSERVE), neighbors_checked: false };
    expect(plan(text(journal(noNeighbors))).ok).toBe(false);
    const p = plan(text(journal({ ...decide(ramp, CHANGE), approved: true } as LedgerDecision)));
    expect(p.ok).toBe(false);
  });

  it("rejects a repeated record and a journal with a key the contract does not have", () => {
    expect(plan(text(journal(decide(ramp, CHANGE), decide(ramp, CHANGE)))).ok).toBe(false);
    expect(plan(text({ ...journal(), auto_approve: true })).ok).toBe(false);
  });

  it("rejects text that is not JSON, not an object, or too large, without throwing", () => {
    for (const bad of ["", "{", "[]", "null", "7", '"x"', "x".repeat(IMPORT_MAX_CHARS + 1)]) {
      const p = plan(bad);
      expect(p.ok, bad.slice(0, 10)).toBe(false);
    }
  });

  it("does not let a dangerous key through", () => {
    const hostile = `{"schema_version":1,"scan_id":17,"decisions":[],"__proto__":{"polluted":true}}`;
    expect(plan(hostile).ok).toBe(false);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
});

describe("importing a review package", () => {
  it("accepts a package whose source is the list that is open", () => {
    const p = good(plan(text(pkg())));
    expect(p.kind).toBe("package");
    expect(p.counts.changes).toBe(1);
  });

  it("rejects a package made on another scan or another version of the list, and never switches the source", () => {
    expect(plan(text(pkg({ source: { ...check, scan_id: 18 } }))).ok).toBe(false);
    const newer: LedgerCheck = { scan_id: SCAN, rows: [row({}, source({ properties_sha256: "f".repeat(64) })), tree] };
    const p = plan(text(pkg({ source: newer })));
    expect(p.ok).toBe(false);
    if (!p.ok) expect(p.errors.join(" ")).toMatch(/not the list that is open|another version|different source/i);
  });

  it("rejects a package its reader rejects, with the reason", () => {
    const p = planImport(text(pkg()), { scanId: SCAN, rows, current: emptyState(), readPackage: () => { throw new Error("The package was changed after it was made."); } });
    expect(p.ok).toBe(false);
    if (!p.ok) expect(p.errors.join(" ")).toContain("changed after it was made");
  });

  it("checks a package's decisions against the open list, not against the list inside the package", () => {
    const weak = { ...decide(ramp, CHANGE), evidence: "unclear" } as LedgerDecision;
    expect(plan(text(pkg({ journal: journal(weak) }))).ok).toBe(false);
  });

  it("has no way to read a package when there is no reader", () => {
    const p = planImport(text(pkg()), { scanId: SCAN, rows, current: emptyState() });
    expect(p.ok).toBe(false);
  });
});

describe("what a replace would lose", () => {
  const mine: RecoverableState = { ...emptyState(), reviewer: "Me", decisions: { [rk(ramp)]: decide(ramp, CHANGE, "Me") },
    drafts: { [rk(ramp)]: CHANGE, [rk(tree)]: { ...EMPTY_DRAFT, note: "half written" } } };

  it("counts current decisions and drafts that no decision stands for", () => {
    const p = good(plan(text(journal(decide(tree, OBSERVE))), mine));
    expect(p.replaces).toEqual({ decisions: 1, drafts: 1 });
    expect(p.replaces.decisions + p.replaces.drafts).toBeGreaterThan(0);
  });

  it("replaces nothing when there is no work", () => {
    expect(good(plan(text(journal(decide(ramp, CHANGE))))).replaces).toEqual({ decisions: 0, drafts: 0 });
  });

  it("leaves the current work untouched when the import is only planned", () => {
    const before = JSON.stringify(mine);
    plan(text(journal(decide(tree, OBSERVE))), mine);
    plan("{broken", mine);
    expect(JSON.stringify(mine)).toBe(before);
  });
});

describe("applying an import", () => {
  const p = () => good(plan(text(journal(decide(ramp, CHANGE, "Dana"), decide(tree, OBSERVE, "Dana")))));
  const mine: RecoverableState = { ...emptyState(), reviewer: "Me", decisions: { [rk(tree)]: decide(tree, OBSERVE, "Me") }, drafts: { [rk(tree)]: OBSERVE },
    queue: { ...DEFAULT_QUEUE, filter: "identity" }, selected: rk(tree) };

  it("replaces decisions and drafts with the imported ones, keeping the reviewer and the queue settings", () => {
    const next = applyImport(p(), mine, rows);
    expect(Object.keys(next.decisions).sort()).toEqual([rk(ramp), rk(tree)].sort());
    expect(next.decisions[rk(tree)].reviewer).toBe("Dana");
    expect(next.reviewer).toBe("Me");
    expect(next.queue).toEqual(mine.queue);
    expect(next.drafts[rk(ramp)]).toMatchObject({ decision: "change", field: "HAS_DETECTABLE_WARNING", proposed: "Yes", identity: "confirmed" });
  });

  it("takes the reviewer from the file when there is none yet", () => {
    expect(applyImport(p(), emptyState(), rows).reviewer).toBe("Dana");
  });

  it("makes drafts that match the decisions, so what is shown is what is recorded", () => {
    const next = applyImport(p(), emptyState(), rows);
    for (const r of rows) {
      const again = decisionFromDraft(r, next.drafts[rk(r)], next.reviewer, NOW);
      expect("decision" in again).toBe(true);
    }
    expect(hasMeaningfulWork(next)).toBe(true);
  });

  it("keeps the selected record only if it is still in the list", () => {
    expect(applyImport(p(), { ...mine, selected: "rk-gone" }, rows).selected).toBe("");
    expect(applyImport(p(), mine, rows).selected).toBe(rk(tree));
  });

  it("is a no-op for a plan that was not accepted", () => {
    const bad = plan("{");
    expect(applyImport(bad, mine, rows)).toBe(mine);
  });
});

describe("the signature the package is compared by", () => {
  it("is the one the review is saved under", () => {
    expect(reviewSignature(SCAN, check.rows)).toBe(reviewSignature(SCAN, rows));
  });
});

describe("the package module of the handoff", () => {
  const made = () => buildReviewPackage(check, journal(decide(ramp, CHANGE), decide(tree, OBSERVE)), SCAN).text;
  const real = (input: string, rs: AssetRow[] = rows) => planImport(input, { scanId: SCAN, rows: rs, current: emptyState(), readPackage: readReviewPackage });

  it("imports the package the app itself downloads", () => {
    const p = good(real(made()));
    expect(p.kind).toBe("package");
    expect(p.counts).toEqual({ changes: 1, observations: 1, holds: 0 });
  });

  it("refuses a package whose CSV was edited, and says so", () => {
    const tampered = JSON.parse(made());
    tampered.files.changes_csv += "RAMP-12,injected\r\n";
    const p = real(JSON.stringify(tampered));
    expect(p.ok).toBe(false);
    if (!p.ok) expect(p.errors.join(" ")).toMatch(/rebuilt|edited|not trusted/i);
  });

  it("refuses a package that claims approval", () => {
    const edited = JSON.parse(made());
    edited.manifest.reviewer_assertion = "Approved by the city.";
    expect(real(JSON.stringify(edited)).ok).toBe(false);
  });

  it("refuses a package made on another version of the list, without switching to it", () => {
    const other: AssetRow[] = [row({}, source({ properties_sha256: "f".repeat(64) })), tree];
    const foreign = buildReviewPackage({ scan_id: SCAN, rows: other }, journal(decide(other[0], CHANGE)), SCAN).text;
    const p = real(foreign);
    expect(p.ok).toBe(false);
    if (!p.ok) expect(p.errors.join(" ")).toMatch(/different source/i);
  });

  it("refuses a package for another scan", () => {
    const edited = JSON.parse(made());
    edited.scan_id = 18;
    edited.source.scan_id = 18;
    edited.journal.scan_id = 18;
    expect(real(JSON.stringify(edited)).ok).toBe(false);
  });

  it("hands a decision made by hand, in the package, through the same rules as a journal", () => {
    const edited = JSON.parse(made());
    edited.journal.decisions[0].evidence = "unclear";
    expect(real(JSON.stringify(edited)).ok).toBe(false);
  });
});
