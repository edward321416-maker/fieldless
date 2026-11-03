import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";
import LedgerHandoff, { downloadPackage } from "./LedgerHandoff";
import { PACKAGE_GUIDE_URL, readReviewPackage } from "./ledgerPackage";
import type { ExportFile, LedgerCheck } from "./ledgerReview";
import type { LedgerJournal } from "./types";

let check: LedgerCheck;
let journal: LedgerJournal;
let scan = 0;

beforeAll(async () => {
  // @ts-expect-error node:fs has no types here
  const { readFileSync } = await import("node:fs");
  const read = (name: string) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/ledger_interop/${name}`, import.meta.url)).toString("utf8"));
  check = read("check.json");
  journal = read("journal.json");
  scan = check.scan_id as number;
});

const html = (j: LedgerJournal = journal, c: LedgerCheck = check) =>
  renderToStaticMarkup(<LedgerHandoff check={c} journal={j} scanId={scan} />);
const text = (markup: string) => markup.replace(/<[^>]+>/g, " ").replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/\s+/g, " ");

describe("LedgerHandoff", () => {
  it("shows each proposed change as the record, the field, and its value before and after", () => {
    const t = text(html());
    expect(t).toContain("P1");
    expect(t).toContain("IS_LIGHT");
    expect(t).toMatch(/Before\s*YES/);
    expect(t).toMatch(/After\s*NO/);
  });

  it("counts the recorded decisions, changes, observations and holds", () => {
    const markup = html();
    const pairs = [...markup.matchAll(/<dt>([^<]+)<\/dt>\s*<dd>(\d+)<\/dd>/g)].map((m) => [m[1], Number(m[2])]);
    expect(Object.fromEntries(pairs)).toEqual({ "Recorded decisions": 4, "Proposed changes": 1, "Observations": 2, "On hold": 1 });
  });

  it("is a read-only preview: no input, and the only button is the package download", () => {
    const markup = html();
    expect(markup).not.toMatch(/<input|<textarea|<select/);
    expect([...markup.matchAll(/<button[^>]*>/g)].length).toBe(1);
    expect(markup).toMatch(/<button[^>]*type="button"[^>]*>Download review package<\/button>/);
  });

  it("links to the public guide in the code repository", () => {
    const markup = html();
    expect(markup).toContain(`href="${PACKAGE_GUIDE_URL}"`);
    expect(markup).toMatch(/rel="noopener noreferrer"/);
  });

  it("says it is a proposal preview, not a validation of the city's current GeoJSON, and where that check happens", () => {
    const t = text(html());
    expect(t).toMatch(/proposal/i);
    expect(t).toMatch(/does not check the city's current GeoJSON/i);
    expect(t).toContain("--package");
    expect(t).toContain("--dry-run");
    expect(t).toMatch(/self-asserted/i);
    expect(t).toMatch(/not signed/i);
    expect(t).toMatch(/not (an )?(authenticated )?municipal approval/i);
  });

  it("with no decision recorded there is nothing to preview and nothing to download", () => {
    const markup = html({ schema_version: 1, scan_id: scan, decisions: [] });
    expect(text(markup)).toMatch(/No decision has been recorded/);
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Download review package/);
  });

  it("with a journal the list does not allow, shows the reasons and does not offer the download", () => {
    const bad = JSON.parse(JSON.stringify(journal)) as LedgerJournal;
    bad.decisions[0].evidence = "unclear";
    const markup = html(bad);
    expect(markup).toMatch(/role="alert"/);
    expect(text(markup)).toMatch(/clear/);
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>Download review package/);
    expect(text(markup)).not.toMatch(/Before\s*YES/);
  });

  it("for another scan's journal, shows that mismatch instead of a preview", () => {
    const markup = html({ ...journal, scan_id: scan + 1 });
    expect(markup).toMatch(/role="alert"/);
    expect(text(markup)).toMatch(/scan/);
  });

  it("escapes what a record or a name contains", () => {
    const j = JSON.parse(JSON.stringify(journal)) as LedgerJournal;
    j.decisions[0].reviewer = "<script>alert(1)</script>";
    const markup = html(j);
    expect(markup).not.toContain("<script");
    expect(markup).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });
});

describe("downloadPackage", () => {
  it("hands the save function the package file, which reads back to the same source and journal", () => {
    const saved: ExportFile[] = [];
    downloadPackage(check, journal, scan, (f) => saved.push(f));
    expect(saved.length).toBe(1);
    expect(saved[0].name).toBe(`ledger-review-scan${scan}-package.json`);
    expect(readReviewPackage(saved[0].text)).toEqual({ source: check, journal });
  });

  it("saves nothing when there is nothing recorded or the journal is not valid", () => {
    const saved: ExportFile[] = [];
    expect(() => downloadPackage(check, { schema_version: 1, scan_id: scan, decisions: [] }, scan, (f) => saved.push(f))).toThrow(/nothing/i);
    const bad = JSON.parse(JSON.stringify(journal)) as LedgerJournal;
    bad.decisions[0].identity = "uncertain";
    expect(() => downloadPackage(check, bad, scan, (f) => saved.push(f))).toThrow(/rejected/);
    expect(saved).toEqual([]);
  });
});
