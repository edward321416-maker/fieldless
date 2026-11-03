import { describe, expect, it } from "vitest";
import { NOW, PHOTO, row, source, treeRow } from "./ledgerFixtures";
import {
  APPLICATION_STATUS, ASSERTION, DATE_MEANING, EMPTY_DRAFT, changesCsv, csvCell, decisionFromDraft, draftProblems, exportFiles, observationsCsv, photoDay, proposalChoices,
  reviewSignature, reviewableRows, sourceFile, sourceOf, validateJournal,
} from "./ledgerReview";
import type { Draft } from "./ledgerReview";
import type { LedgerDecision, LedgerJournal } from "./types";

const CHANGE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "change",
  field: "HAS_DETECTABLE_WARNING", proposed: "Yes", note: "The panel is visible on the ramp." };
const OBSERVE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "observe", note: "Seen in the photo." };
const HOLD: Draft = { ...EMPTY_DRAFT, decision: "hold", note: "Cannot tell yet." };

function decide(r = row(), draft: Draft = CHANGE, reviewer = "A. Reviewer"): LedgerDecision {
  const made = decisionFromDraft(r, draft, reviewer, NOW);
  if (!("decision" in made)) throw new Error(made.problems.join("; "));
  return made.decision;
}
const journal = (decisions: LedgerDecision[], scan_id = 17): LedgerJournal => ({ schema_version: 1, scan_id, decisions });

describe("a source snapshot", () => {
  it("is read only when it is the version 1 contract", () => {
    expect(sourceOf(row())?.record_id).toBe("RAMP-12");
    expect(sourceOf(row({}, null))).toBeNull();
    expect(sourceOf(row({}, { ...source(), version: 2 } as never))).toBeNull();
    expect(sourceOf(row({}, { ...source(), review_key: "" }))).toBeNull();
    expect(sourceOf(row({}, { ...source(), editable_fields: null } as never))).toBeNull();
    expect(sourceOf(row({}, { ...source(), identity_warnings: "near" } as never))).toBeNull();
  });

  it("makes a row unreviewable when it has none, or shares its review key with another row", () => {
    const a = row();
    const none = row({ city_record: "OLD" }, null);
    const twinA = row({ city_record: "T" }, source({ review_key: "same" }));
    const twinB = row({ city_record: "U" }, source({ review_key: "same", record_id: "RAMP-13" }));
    expect(reviewableRows([a, none, twinA, twinB])).toEqual([a]);
  });

  it("offers only the other yes/no value of an editable field, never a date or a free value", () => {
    const s = source({ editable_fields: {
      HAS_DETECTABLE_WARNING: { before: "No", yes: "Yes", no: "No", question: "q", suggested: null },
      LAST_VERIFY_DATE: { before: "No", yes: "Yes", no: "No", question: "q", suggested: null },
      INSPECTION_DATE: { before: "No", yes: "Yes", no: "No", question: "q", suggested: null },
      INSTALL_YEAR: { before: "No", yes: "Yes", no: "No", question: "q", suggested: null },
      UPDATED_BY: { before: "No", yes: "Yes", no: "No", question: "q", suggested: null },
      ODD: { before: "Maybe", yes: "Yes", no: "No", question: "q", suggested: null },
    } });
    expect(proposalChoices(s, "HAS_DETECTABLE_WARNING")).toEqual(["Yes"]);
    expect(proposalChoices(s, "LAST_VERIFY_DATE")).toEqual([]);
    expect(proposalChoices(s, "INSPECTION_DATE")).toEqual([]);
    expect(proposalChoices(s, "INSTALL_YEAR")).toEqual([]);
    expect(proposalChoices(s, "UPDATED_BY")).toEqual([]);
    expect(proposalChoices(s, "ODD")).toEqual([]); // the record's value is neither the city's yes nor no
    expect(proposalChoices(s, "OWNER")).toEqual([]);
  });
});

describe("nothing is approved by default", () => {
  it("starts every choice blank", () => {
    expect(EMPTY_DRAFT).toEqual({ identity: "", evidence: "", decision: "", field: "", proposed: "", neighbors: false, note: "" });
  });

  it("an untouched row cannot be recorded, and says what is missing", () => {
    const problems = draftProblems(row(), EMPTY_DRAFT, "");
    expect(problems.join(" | ")).toMatch(/reviewer/i);
    expect(problems.join(" | ")).toMatch(/decision/i);
    expect(problems.join(" | ")).toMatch(/reason/i);
    expect(decisionFromDraft(row(), EMPTY_DRAFT, "", NOW)).toHaveProperty("problems");
  });

  it("an empty journal exports zero changes and zero observations", () => {
    const check = validateJournal([row()], journal([]), 17);
    expect(check).toMatchObject({ ok: true, errors: [], changes: [], observations: [], holds: [] });
    expect(changesCsv([row()], journal([]), 17).trim().split("\r\n")).toHaveLength(1); // the header only
  });
});

describe("recording a decision", () => {
  it("makes a change only from a confirmed identity, clear evidence, an editable field and another value", () => {
    const d = decide();
    expect(Object.keys(d)).toEqual(["review_key", "record_id", "item", "identity", "evidence", "decision", "field", "proposed_value",
      "neighbors_checked", "reviewer", "note", "reviewed_at"]);
    expect(d).toMatchObject({ review_key: source().review_key, record_id: "RAMP-12", item: "curb_ramp", identity: "confirmed",
      evidence: "clear", decision: "change", field: "HAS_DETECTABLE_WARNING", proposed_value: "Yes", neighbors_checked: false,
      reviewer: "A. Reviewer", note: "The panel is visible on the ramp.", reviewed_at: NOW });
  });

  it("takes the review key from the source untouched, and trims the reviewer and the reason", () => {
    const d = decide(row(), { ...CHANGE, note: "  spaced  " }, "  A. Reviewer  ");
    expect(d.review_key).toBe(source().review_key);
    expect(d.reviewer).toBe("A. Reviewer");
    expect(d.note).toBe("spaced");
  });

  it.each([
    ["an uncertain identity", { identity: "uncertain" }, /same facility/i],
    ["a different object", { identity: "different" }, /same facility/i],
    ["unclear evidence", { evidence: "unclear" }, /clear/i],
    ["no identity answer", { identity: "" }, /same facility/i],
    ["no evidence answer", { evidence: "" }, /clear/i],
    ["no field", { field: "" }, /field/i],
    ["a field not on the list", { field: "OWNER" }, /field/i],
    ["no proposed value", { proposed: "" }, /value/i],
    ["a value that is not the city's yes or no", { proposed: "2025-07-11" }, /value/i],
    ["the value the record already has", { proposed: "No" }, /already/i],
    ["a blank reason", { note: "   " }, /reason/i],
  ] as [string, Partial<Draft>, RegExp][])("will not change anything on %s", (_name, over, message) => {
    const problems = draftProblems(row(), { ...CHANGE, ...over }, "A. Reviewer");
    expect(problems.join(" | ")).toMatch(message);
    expect(decisionFromDraft(row(), { ...CHANGE, ...over }, "A. Reviewer", NOW)).toHaveProperty("problems");
  });

  it("keeps the reviewer to 200 characters and the reason to 2000, as the backend does", () => {
    expect(draftProblems(row(), CHANGE, "x".repeat(201)).join(" ")).toMatch(/reviewer/i);
    expect(draftProblems(row(), CHANGE, "x".repeat(200))).toEqual([]);
    expect(draftProblems(row(), { ...CHANGE, note: "x".repeat(2001) }, "A").join(" ")).toMatch(/reason/i);
    expect(draftProblems(row(), { ...CHANGE, note: "x".repeat(2000) }, "A")).toEqual([]);
  });

  it("needs a reviewer name", () => {
    expect(draftProblems(row(), CHANGE, "   ").join(" ")).toMatch(/reviewer/i);
  });

  it("needs a valid time of review", () => {
    for (const bad of ["", "yesterday", "2026-13-01T00:00:00Z", "2026-02-30T00:00:00Z", "2026-11-04", "2026-11-04T12:00:00"]) {
      const made = decisionFromDraft(row(), CHANGE, "A. Reviewer", bad);
      expect(made, bad).toHaveProperty("problems");
    }
    expect(decisionFromDraft(row(), CHANGE, "A. Reviewer", "2026-11-04T21:00:00+09:00")).toHaveProperty("decision");
  });

  it("takes the photo day from the photo's own time in UTC, as the backend does, not from a city field", () => {
    expect(photoDay(row())).toBe("2025-07-11");
    expect(photoDay(row({ evidence: { ...PHOTO, captured_at: "2025-07-11T23:30:00-07:00" } }))).toBe("2025-07-12");
    expect(photoDay(row({ seen_in_photo_on: "2019-03-01", evidence: { ...PHOTO, captured_at: null } }))).toBe("");
    expect(draftProblems(row({ evidence: { ...PHOTO, link: "" } }), OBSERVE, "A").join(" ")).toMatch(/photo/i);
  });

  it("needs a photo and a photo date to change or observe, and a source for anything", () => {
    const noPhoto = row({ evidence: null });
    expect(draftProblems(noPhoto, CHANGE, "A").join(" ")).toMatch(/photo/i);
    expect(draftProblems(noPhoto, OBSERVE, "A").join(" ")).toMatch(/photo/i);
    expect(draftProblems(noPhoto, HOLD, "A")).toEqual([]);
    const noSource = row({}, null);
    expect(draftProblems(noSource, HOLD, "A").join(" ")).toMatch(/source/i);
    expect(draftProblems(noSource, CHANGE, "A").join(" ")).toMatch(/source/i);
  });

  it("a record with no editable attribute, such as a tree, can only be observed or held", () => {
    const tree = treeRow();
    expect(draftProblems(tree, { ...CHANGE, field: "LAST_VERIFY_DATE", proposed: "2025-07-11" }, "A").join(" ")).toMatch(/field/i);
    expect(draftProblems(tree, OBSERVE, "A")).toEqual([]);
    expect(draftProblems(tree, HOLD, "A")).toEqual([]);
    const d = decide(tree, OBSERVE);
    expect(d).toMatchObject({ decision: "observe", field: null, proposed_value: null });
  });

  it("makes an observation from a confirmed identity and clear evidence, with no field", () => {
    expect(draftProblems(row(), { ...OBSERVE, identity: "uncertain" }, "A").join(" ")).toMatch(/same facility/i);
    expect(draftProblems(row(), { ...OBSERVE, evidence: "" }, "A").join(" ")).toMatch(/clear/i); // the answer is needed...
    expect(draftProblems(row(), { ...OBSERVE, evidence: "unclear" }, "A").join(" ")).toMatch(/clear/i); // an unclear photo is held, not observed
    expect(decide(row(), { ...OBSERVE, field: "HAS_DETECTABLE_WARNING", proposed: "Yes" })).toMatchObject({ field: null, proposed_value: null });
  });

  it("holds without any claim about the facility", () => {
    const d = decide(row(), { ...HOLD, identity: "uncertain", field: "HAS_DETECTABLE_WARNING", proposed: "Yes" });
    expect(d).toMatchObject({ decision: "hold", identity: "uncertain", evidence: "unclear", field: null, proposed_value: null });
  });

  it("needs the neighbouring records checked when the source warns about them, for a change or an observation", () => {
    const near = row({}, source({ identity_warnings: ["TRE-53948 is 3 m away and was photographed 2 s apart"] }));
    expect(draftProblems(near, CHANGE, "A").join(" ")).toMatch(/neighbo/i);
    expect(draftProblems(near, OBSERVE, "A").join(" ")).toMatch(/neighbo/i);
    expect(draftProblems(near, { ...CHANGE, neighbors: true }, "A")).toEqual([]);
    expect(draftProblems(near, HOLD, "A")).toEqual([]);
    expect(decide(near, { ...CHANGE, neighbors: true }).neighbors_checked).toBe(true);
    expect(draftProblems(row(), CHANGE, "A")).toEqual([]); // no warning: nothing extra to confirm
  });
});

describe("a journal is checked as the backend will check it", () => {
  const rows = [row(), treeRow()];
  const good = () => [decide(rows[0]), decide(rows[1], OBSERVE)];

  it("passes the decisions made here, with the changes, observations and holds kept apart", () => {
    const hold = decide(row({ city_record: "RAMP-99" }, source({ review_key: "rk-99", record_id: "RAMP-99", record_key: "fid:99" })), HOLD);
    const all = [...rows, row({ city_record: "RAMP-99" }, source({ review_key: "rk-99", record_id: "RAMP-99", record_key: "fid:99" }))];
    const out = validateJournal(all, journal([...good(), hold]), 17);
    expect(out.errors).toEqual([]);
    expect(out.changes).toHaveLength(1);
    expect(out.changes[0]).toMatchObject({ record_id: "RAMP-12", layer: "curb_ramps.geojson", field: "HAS_DETECTABLE_WARNING",
      before: "No", after: "Yes", photo_observed_on: "2025-07-11", photo_creator: "kev", photo_link: PHOTO.link, photo_license: "CC BY-SA 4.0" });
    expect(out.observations).toHaveLength(1);
    expect(out.observations[0]).toMatchObject({ record_id: "TRE-53943", layer: "street_trees.geojson", photo_observed_on: "2025-07-11" });
    expect(out.holds).toHaveLength(1);
  });

  it("rejects the whole package from another scan", () => {
    expect(validateJournal(rows, journal(good(), 18), 17).errors.join(" ")).toMatch(/scan/i);
    expect(validateJournal(rows, journal(good(), 18), 17).changes).toEqual([]);
  });

  it("rejects a journal with a key the contract does not have", () => {
    expect(validateJournal(rows, { ...journal(good()), approved_by: "city" } as never, 17).ok).toBe(false);
  });

  it("rejects a wrong schema version or a malformed package", () => {
    expect(validateJournal(rows, { ...journal(good()), schema_version: 2 } as never, 17).ok).toBe(false);
    expect(validateJournal(rows, { schema_version: 1, scan_id: 17 } as never, 17).ok).toBe(false);
    expect(validateJournal(rows, null as never, 17).ok).toBe(false);
  });

  it("rejects a decision whose review key is not one of this list's rows (an old or changed source)", () => {
    const stale = { ...good()[0], review_key: "rk-ramp-12-from-an-earlier-scan" };
    const out = validateJournal(rows, journal([stale]), 17);
    expect(out.ok).toBe(false);
    expect(out.errors.join(" ")).toMatch(/not in this list|source/i);
    expect(out.changes).toEqual([]);
  });

  it("rejects a duplicated review key", () => {
    const d = good()[0];
    const out = validateJournal(rows, journal([d, { ...d, note: "again" }]), 17);
    expect(out.ok).toBe(false);
    expect(out.errors.join(" ")).toMatch(/more than once|duplicate/i);
  });

  it("rejects two changes to the same field of the same record", () => {
    const twin = row({ city_record: "RAMP-12" }, source({ review_key: "rk-ramp-12-second-photo" }));
    const out = validateJournal([rows[0], twin], journal([decide(rows[0]), decide(twin)]), 17);
    expect(out.ok).toBe(false);
    expect(out.errors.join(" ")).toMatch(/same field|conflict/i);
  });

  it("rejects a decision changed after it was recorded: another record, field, value, or a date", () => {
    const d = good()[0];
    const bad = (over: Partial<LedgerDecision>) => validateJournal(rows, journal([{ ...d, ...over }]), 17);
    expect(bad({ record_id: "RAMP-13" }).ok).toBe(false);
    expect(bad({ item: "street_tree" }).ok).toBe(false);
    expect(bad({ field: "OWNER" }).ok).toBe(false);
    expect(bad({ field: "LAST_VERIFY_DATE", proposed_value: "2025-07-11" }).ok).toBe(false);
    expect(bad({ proposed_value: "2025-07-11" }).ok).toBe(false);
    expect(bad({ proposed_value: "No" }).ok).toBe(false); // the same as before
    expect(bad({ identity: "uncertain" }).ok).toBe(false);
    expect(bad({ evidence: "unclear" }).ok).toBe(false); // a change needs a clear photo
    expect(bad({ reviewer: " " }).ok).toBe(false);
    expect(bad({ note: "" }).ok).toBe(false);
    expect(bad({ reviewed_at: "soon" }).ok).toBe(false);
    expect(bad({ decision: "approve" as never }).ok).toBe(false);
    expect(bad({ neighbors_checked: "yes" as never }).ok).toBe(false);
  });

  it("rejects an observation recorded with unclear evidence", () => {
    const o = { ...decide(row(), OBSERVE), evidence: "unclear" as const };
    expect(validateJournal([row()], journal([o]), 17).ok).toBe(false);
  });

  it("rejects a decision with a key the contract does not have", () => {
    const out = validateJournal(rows, journal([{ ...good()[0], LAST_VERIFY_DATE: "2025-07-11" } as never]), 17);
    expect(out.ok).toBe(false);
    expect(out.errors.join(" ")).toMatch(/LAST_VERIFY_DATE|unknown key/i);
  });

  it("rejects an observation that carries a field, and a change whose row lost its photo", () => {
    const o = good()[1];
    expect(validateJournal(rows, journal([{ ...o, field: "LAST_VERIFY_DATE", proposed_value: "x" }]), 17).ok).toBe(false);
    const lost = [row({ evidence: null }), rows[1]];
    expect(validateJournal(lost, journal([good()[0]]), 17).ok).toBe(false);
  });

  it("rejects a decision that needs a neighbour check that was not made", () => {
    const near = row({}, source({ identity_warnings: ["TRE-53948 is 3 m away"] }));
    const unchecked = { ...decide(near, { ...CHANGE, neighbors: true }), neighbors_checked: false };
    expect(validateJournal([near], journal([unchecked]), 17).errors.join(" ")).toMatch(/neighbo/i);
  });

  it("does not take a row with no source into the package", () => {
    const old = row({ city_record: "OLD" }, null);
    expect(validateJournal([old], journal([good()[0]]), 17).ok).toBe(false);
  });
});

describe("the CSV files", () => {
  it("keep a cell that would run as a formula as text, with the backend's apostrophe", () => {
    for (const lead of ["=", "+", "-", "@", "\t", "\r", "\n"]) {
      expect(csvCell(`${lead}SUM(A1)`).replace(/^"/, "").startsWith("'" + lead), JSON.stringify(lead)).toBe(true);
    }
    expect(csvCell("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell("")).toBe("");
    expect(csvCell(true)).toBe("true");
    expect(csvCell(17)).toBe("17");
    expect(csvCell(null)).toBe("");
    expect(csvCell("a,b")).toBe('"a,b"');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("two\nlines")).toBe('"two\nlines"');
    expect(csvCell("\nlead")).toBe(`"'\nlead"`);
  });

  it("keep Unicode and quotes as typed", () => {
    expect(csvCell("보도 경사로 “확인” – ok")).toBe("보도 경사로 “확인” – ok");
    expect(csvCell("=HYPERLINK(\"http://x\")")).toBe(`"'=HYPERLINK(""http://x"")"`);
  });

  const rows = [row(), treeRow()];
  const decisions = () => [decide(rows[0], { ...CHANGE, note: '=cmd|"x", é 한글' }, "+Reviewer"), decide(rows[1], OBSERVE)];

  it("list only validated changes in the changes file, with the backend's columns (the ones in interface-checkpoint.md)", () => {
    const hold = row({ city_record: "RAMP-99" }, source({ review_key: "rk-99", record_id: "RAMP-99" }));
    const csv = changesCsv([...rows, hold], journal([...decisions(), decide(hold, HOLD)]), 17);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("﻿review_key,record_id,item,layer,field,before,after,identity,evidence,neighbors_checked,reviewer,"
      + "reviewer_assertion,note,reviewed_at,application_status,photo_observed_on,date_meaning,photo_creator,photo_link,photo_license,"
      + "scan_id,record_key,properties_sha256");
    expect(lines).toHaveLength(3); // header, one change, the closing line break
    expect(lines[1]).toContain("RAMP-12,curb_ramp,curb_ramps.geojson,HAS_DETECTABLE_WARNING,No,Yes,confirmed,clear,false,'+Reviewer,");
    expect(lines[1]).toContain(`${ASSERTION},`);
    expect(lines[1]).toContain(`,${APPLICATION_STATUS},2025-07-11,${DATE_MEANING},kev,https://www.mapillary.com/app/?pKey=7,CC BY-SA 4.0,17,fid:12,`);
    expect(lines[1]).toContain(`"'=cmd|""x"", é 한글"`);
    expect(lines[1]).not.toContain("RAMP-99");
    expect(lines[1]).not.toContain("TRE-53943");
  });

  it("keep the photo date and credit in the observations file, without holds or changes", () => {
    const csv = observationsCsv(rows, journal(decisions()), 17);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("﻿review_key,record_id,item,layer,photo_observed_on,date_meaning,photo_creator,photo_link,photo_license,"
      + "identity,evidence,neighbors_checked,reviewer,reviewer_assertion,note,reviewed_at,application_status,scan_id,record_key,"
      + "properties_sha256");
    expect(lines).toHaveLength(3);
    expect(lines[1]).toContain(`TRE-53943,street_tree,street_trees.geojson,2025-07-11,${DATE_MEANING},kev,https://www.mapillary.com/app/?pKey=7,CC BY-SA 4.0,confirmed,clear`);
    expect(lines[1]).toContain(`${ASSERTION},`);
    expect(lines[1]).toContain(`,${APPLICATION_STATUS},17,`);
    expect(lines[1]).not.toContain("RAMP-12");
  });

  it("are not made from a package the backend would reject", () => {
    const bad = journal([{ ...decisions()[0], proposed_value: "No" }]);
    expect(() => changesCsv(rows, bad, 17)).toThrow(/rejected|invalid/i);
  });
});

describe("the files to download", () => {
  const rows = [row(), treeRow()];

  it("are the journal with every decision, the changes and the observations, named for the scan", () => {
    const hold = row({ city_record: "RAMP-99" }, source({ review_key: "rk-99", record_id: "RAMP-99" }));
    const j = journal([decide(rows[0]), decide(rows[1], OBSERVE), decide(hold, HOLD)]);
    const files = exportFiles([...rows, hold], j, 17);
    expect(files.map((f) => f.name)).toEqual(["ledger-review-scan17-journal.json", "ledger-review-scan17-changes.csv",
      "ledger-review-scan17-observations.csv"]);
    expect(files.map((f) => f.mime)).toEqual(["application/json", "text/csv", "text/csv"]);
    const parsed = JSON.parse(files[0].text);
    expect(Object.keys(parsed)).toEqual(["schema_version", "scan_id", "decisions"]);
    expect(parsed.decisions.map((d: LedgerDecision) => d.decision)).toEqual(["change", "observe", "hold"]); // the hold is kept here
    expect(files[1].text).not.toContain("RAMP-99");
    expect(files[2].text).not.toContain("RAMP-99");
  });
});

describe("the source list file", () => {
  it("is the whole check as it was given, named for the scan", () => {
    const check = { scan_id: 17, city: "Seattle", totals: { in_view: 2 }, rows: [row(), treeRow()] };
    const file = sourceFile(check, 17);
    expect(file.name).toBe("ledger-review-scan17-source.json");
    expect(file.mime).toBe("application/json");
    expect(JSON.parse(file.text)).toEqual(JSON.parse(JSON.stringify(check)));
    expect(file.text.endsWith("\n")).toBe(true);
  });

  it("is not made from another scan's list", () => {
    expect(() => sourceFile({ scan_id: 18, rows: [row()] }, 17)).toThrow(/scan/i);
    expect(() => sourceFile({ rows: [row()] }, 17)).toThrow(/scan/i);
  });
});

describe("a different scan or source", () => {
  it("has a different signature, so nothing carries over", () => {
    const a = [row(), treeRow()];
    expect(reviewSignature(17, a)).toBe(reviewSignature(17, [...a]));
    expect(reviewSignature(18, a)).not.toBe(reviewSignature(17, a));
    expect(reviewSignature(17, [a[0], treeRow(53948)])).not.toBe(reviewSignature(17, a));
    const changed = row({}, source({ properties_sha256: "d".repeat(64) }));
    expect(reviewSignature(17, [changed, a[1]])).not.toBe(reviewSignature(17, a));
  });
});
