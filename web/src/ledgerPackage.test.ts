// The one portable review package: source + journal + manifest + the two CSV files as a convenience. The source and the journal are the
// only inputs: the CSV strings and the manifest are checked against what is rebuilt, never trusted. tests/fixtures/ledger_interop/
// review-package.json is the file tests/eval/test_ledger_package.py reads and writes too.
import { beforeAll, describe, expect, it } from "vitest";
import { changesCsv, observationsCsv, type LedgerCheck } from "./ledgerReview";
import {
  PACKAGE_GUIDE_URL, PACKAGE_KIND, PACKAGE_MANIFEST, buildReviewPackage, previewOf, readReviewPackage,
} from "./ledgerPackage";
import type { LedgerJournal } from "./types";

type Pkg = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

let check: LedgerCheck;
let journal: LedgerJournal;
let shared: Pkg;
let scan = 0;
let changesBytes: number[] = [];
let observationsBytes: number[] = [];

beforeAll(async () => {
  // @ts-expect-error node:fs has no types here
  const { readFileSync } = await import("node:fs");
  const read = (name: string) => readFileSync(new URL(`../../tests/fixtures/ledger_interop/${name}`, import.meta.url));
  check = JSON.parse(read("check.json").toString("utf8"));
  journal = JSON.parse(read("journal.json").toString("utf8"));
  shared = JSON.parse(read("review-package.json").toString("utf8"));
  scan = check.scan_id as number;
  changesBytes = Array.from(read("changes.csv") as Uint8Array);
  observationsBytes = Array.from(read("observations.csv") as Uint8Array);
});

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const built = (): Pkg => JSON.parse(buildReviewPackage(check, journal, scan).text);
const refused = (value: unknown, fragment: string | RegExp) => expect(() => readReviewPackage(value)).toThrow(fragment);

describe("buildReviewPackage", () => {
  it("makes one UTF-8 JSON file named for the scan, with no byte-order mark", () => {
    const file = buildReviewPackage(check, journal, scan);
    expect(file.name).toBe(`ledger-review-scan${scan}-package.json`);
    expect(file.mime).toBe("application/json");
    expect(file.text.startsWith("﻿")).toBe(false);
    expect(file.text.endsWith("\n")).toBe(true);
  });

  it("holds exactly the documented keys, in order, with the full source and the journal", () => {
    const pkg = built();
    expect(Object.keys(pkg)).toEqual(["schema_version", "kind", "scan_id", "source", "journal", "manifest", "files"]);
    expect(pkg.schema_version).toBe(1);
    expect(pkg.kind).toBe("fieldless-ledger-review");
    expect(pkg.kind).toBe(PACKAGE_KIND);
    expect(pkg.scan_id).toBe(scan);
    expect(pkg.source).toEqual(check);
    expect(pkg.journal).toEqual(journal);
    expect(pkg.manifest).toEqual({ notice: "Prepared only; not applied to an official ledger.",
      reviewer_assertion: "Self-asserted; not authenticated municipal approval." });
    expect(pkg.manifest).toEqual(PACKAGE_MANIFEST);
    expect(Object.keys(pkg.files)).toEqual(["changes_csv", "observations_csv"]);
  });

  it("carries the same two CSV files the review pane downloads, BOM included", () => {
    const pkg = built();
    expect(pkg.files.changes_csv).toBe(changesCsv(check.rows, journal, scan));
    expect(pkg.files.observations_csv).toBe(observationsCsv(check.rows, journal, scan));
    expect(pkg.files.changes_csv.startsWith("﻿")).toBe(true);
  });

  it("is not made from a journal the review rules reject, or for another scan", () => {
    const bad = clone(journal);
    bad.decisions[0].identity = "uncertain";
    expect(() => buildReviewPackage(check, bad, scan)).toThrow(/rejected/);
    expect(() => buildReviewPackage(check, journal, scan + 1)).toThrow(/scan/);
    expect(() => buildReviewPackage({ ...check, scan_id: scan + 1 }, journal, scan)).toThrow(/scan/);
  });

  it("keeps only the contract's keys of each decision, as the journal download does", () => {
    const extra = clone(journal) as LedgerJournal & { decisions: Record<string, unknown>[] };
    extra.decisions[0].approved = true;
    expect(() => buildReviewPackage(check, extra, scan)).toThrow(/unknown key approved/);
  });
});

describe("TypeScript and Python read and write the same package", () => {
  it("builds what the shared fixture holds (the fixture is what Python builds from the same check and journal)", () => {
    expect(built()).toEqual(shared);
  });

  it("builds convenience files that are byte for byte the fixture's CSV files", () => {
    const bytes = (text: string) => Array.from(new TextEncoder().encode(text));
    expect(bytes(built().files.changes_csv)).toEqual(changesBytes);
    expect(bytes(built().files.observations_csv)).toEqual(observationsBytes);
  });

  it("reads the package Python wrote", () => {
    const { source, journal: read } = readReviewPackage(shared);
    expect(source).toEqual(check);
    expect(read).toEqual(journal);
  });
});

describe("readReviewPackage", () => {
  it("reads back what was built, from a parsed value or from the file's text", () => {
    const file = buildReviewPackage(check, journal, scan);
    expect(readReviewPackage(JSON.parse(file.text))).toEqual({ source: check, journal });
    expect(readReviewPackage(file.text)).toEqual({ source: check, journal });
  });

  it("accepts a package with no decisions", () => {
    const empty = { schema_version: 1, scan_id: scan, decisions: [] };
    const pkg = JSON.parse(buildReviewPackage(check, empty as LedgerJournal, scan).text);
    expect(readReviewPackage(pkg).journal.decisions).toEqual([]);
  });

  it("accepts a package without manifest and files: the inputs are enough", () => {
    const pkg = built();
    delete pkg.manifest;
    delete pkg.files;
    expect(readReviewPackage(pkg).journal).toEqual(journal);
  });

  it.each([null, [], 3, true, "{not json", "[]"])("refuses %p", (value) => {
    refused(value, /package/);
  });

  it.each([
    ["kind", { kind: "something-else" }],
    ["schema_version", { schema_version: 2 }],
    ["schema_version", { schema_version: "1" }],
    ["scan_id", { scan_id: 99 }],
    ["scan_id", { scan_id: "7" }],
    ["unknown key", { approved: true }],
    ["source", { source: [] }],
    ["source", { source: { rows: "x" } }],
    ["journal", { journal: "x" }],
    ["files", { files: [] }],
    ["changes_csv", { files: { changes_csv: 1, observations_csv: "" } }],
    ["unknown key", { files: { changes_csv: "", observations_csv: "", zip: "" } }],
  ])("refuses a malformed package (%s)", (fragment, over) => {
    refused({ ...built(), ...over }, fragment);
  });

  it.each(["source", "journal", "kind", "schema_version", "scan_id"])("refuses a package missing %s", (key) => {
    const pkg = built();
    delete pkg[key];
    refused(pkg, key);
  });

  it("refuses a package whose scan is not the scan of its source or its journal", () => {
    const a = built();
    a.journal.scan_id = scan + 1;
    refused(a, /scan_id/);
    const b = built();
    b.source.scan_id = scan + 1;
    refused(b, /scan_id/);
  });

  it("refuses a journal the source's rows do not allow: the review rules still apply", () => {
    const pkg = built();
    pkg.journal.decisions[0].identity = "uncertain";
    refused(pkg, /confirmed|rejected/);
    const stray = built();
    stray.journal.decisions[0].review_key = "0".repeat(64);
    refused(stray, /not in this list|rejected/);
  });

  it.each(["changes_csv", "observations_csv"])("refuses a %s that differs from the one rebuilt, not repairing it", (name) => {
    const pkg = built();
    pkg.files[name] += "P9,forged,row\r\n";
    refused(pkg, name);
  });

  it("refuses a forged CSV row that tries to add a change the journal does not have", () => {
    const pkg = built();
    pkg.files.changes_csv = pkg.files.changes_csv.replace(/\r\n/, "\r\nforged,P9,pole,poles.geojson,IS_LIGHT,YES,NO\r\n");
    refused(pkg, "changes_csv");
  });

  it.each(["notice", "reviewer_assertion"])("refuses a false manifest (%s)", (field) => {
    const pkg = built();
    pkg.manifest[field] = "Approved by the city.";
    refused(pkg, "manifest");
  });

  it("refuses a manifest with another key or a missing one", () => {
    const more = built();
    more.manifest.approved = true;
    refused(more, "manifest");
    const less = built();
    delete less.manifest.notice;
    refused(less, "manifest");
  });

  it("refuses a source edited after it was made only in ways the review rules see", () => {
    const pkg = built();
    pkg.source.rows = pkg.source.rows.concat(pkg.source.rows.filter((r: Pkg) => r.ledger_source)[0]); // a repeated review key
    delete pkg.files;
    refused(pkg,/rejected|not in this list|more than once/);
  });

  it("does not turn __proto__ or constructor keys into assignments: such a package is refused", () => {
    const text = buildReviewPackage(check, journal, scan).text.replace('"manifest": {', '"manifest": {"__proto__": {"approved": true},');
    refused(text, "__proto__");
    const nested = built();
    nested.source.rows[0].extra = JSON.parse('{"__proto__": {"x": 1}}');
    refused(nested, "__proto__");
    expect(({} as Record<string, unknown>).approved).toBeUndefined();
    expect(({} as Record<string, unknown>).x).toBeUndefined();
  });

  it("refuses a package nested absurdly deep instead of overflowing the stack", () => {
    let deep: unknown = 1;
    for (let i = 0; i < 5000; i++) deep = { a: deep };
    refused({ ...built(), source: deep }, /package/);
  });

  it("does not change the value it is given", () => {
    const pkg = built();
    const before = JSON.stringify(pkg);
    readReviewPackage(pkg);
    expect(JSON.stringify(pkg)).toBe(before);
  });
});

describe("previewOf: what the package would propose, read-only", () => {
  it("lists every change as before and after, and counts observations and holds", () => {
    const p = previewOf(check, journal, scan);
    expect(p.ok).toBe(true);
    expect(p.errors).toEqual([]);
    expect(p.changes.length).toBe(1);
    const c = p.changes[0];
    expect(c).toMatchObject({ field: "IS_LIGHT", before: expect.any(String), after: expect.any(String) });
    expect(c.before).not.toBe(c.after);
    expect(p.observations).toBe(2);
    expect(p.holds).toBe(1);
    expect(p.decisions).toBe(4);
  });

  it("reports the reasons instead of a preview when the journal is not valid for the list", () => {
    const bad = clone(journal);
    bad.decisions[0].evidence = "unclear";
    const p = previewOf(check, bad, scan);
    expect(p.ok).toBe(false);
    expect(p.errors.join(" ")).toMatch(/clear/);
    expect(p.changes).toEqual([]);
    expect(previewOf(check, journal, scan + 1).ok).toBe(false);
  });

  it("an empty journal is a valid, empty preview", () => {
    const p = previewOf(check, { schema_version: 1, scan_id: scan, decisions: [] }, scan);
    expect(p).toMatchObject({ ok: true, decisions: 0, observations: 0, holds: 0 });
    expect(p.changes).toEqual([]);
  });

  it("points to the public guide in the code repository", () => {
    expect(PACKAGE_GUIDE_URL).toBe("https://github.com/edward321416-maker/fieldless/blob/main/docs/ledger-workflow.md");
  });
});
