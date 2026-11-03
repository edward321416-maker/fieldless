import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { NOW, row, source, treeRow } from "./ledgerFixtures";
import { EMPTY_DRAFT, type Draft } from "./ledgerReview";
import LedgerReviewPane, {
  LedgerReviewView, initialReviewState, journalOf, reviewReducer, runDownload, type ReviewState,
} from "./LedgerReviewPane";
import type { AssetRow, Scan } from "./types";

const scan = { id: 17, items: [{ id: "curb_ramp", target: "curb ramp", map_icon: "ramp", kind: "check", inventory: true },
  { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }] } as unknown as Scan;
const ramp = row();
const tree = treeRow(53943, ["TRE-53948 is 3 m away and was photographed 2 s apart."]);
const old = row({ city_record: "OLD-1" }, null);
const rows: AssetRow[] = [ramp, tree, old];
const chk = (list: AssetRow[] = rows) => ({ scan_id: 17, city: "Seattle", totals: { in_view: 3 }, rows: list });
const CHANGE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "change",
  field: "HAS_DETECTABLE_WARNING", proposed: "Yes", note: "The panel is visible." };

function render(state: ReviewState = initialReviewState(), list: AssetRow[] = rows, showFor?: (r: AssetRow) => (() => void) | undefined) {
  return renderToStaticMarkup(<LedgerReviewView scan={scan} city="Seattle" check={chk(list)} state={state} dispatch={() => {}} showFor={showFor} save={() => {}} />);
}
function run(actions: Parameters<typeof reviewReducer>[1][], from = initialReviewState()): ReviewState {
  return actions.reduce(reviewReducer, from);
}
const recordRamp = (note = "ok", reviewer = "A. Reviewer") => run([
  { type: "reviewer", value: reviewer },
  { type: "draft", key: ramp.ledger_source!.review_key, patch: { ...CHANGE, note } },
  { type: "record", row: ramp, now: NOW },
]);

const inputTag = (html: string, value: string) => (html.match(/<input[^>]*>/g) ?? []).find((t) => t.includes(`value="${value}"`)) ?? "";

describe("the review pane, before anyone has chosen anything", () => {
  const html = render();

  it("shows the record's ID and position, the layer, and the city's own values", () => {
    expect(html).toContain("RAMP-12");
    expect(html).toContain("curb_ramps.geojson");
    expect(html).toContain("1 Main St");
    expect(html).toContain("47.60000");
    expect(html).toContain("HAS_DETECTABLE_WARNING");
  });

  it("shows the real photo with its creator, the Mapillary mark and link, and the licence", () => {
    expect(html).toContain('src="/api/evidence/scan17_look0_7.jpg"');
    expect(html).toContain("Photo by kev");
    expect(html).toContain("View on Mapillary");
    expect(html).toContain('href="https://www.mapillary.com/app/?pKey=7"');
    expect(html).toContain("CC BY-SA 4.0");
  });

  it("keeps the photo day apart from the city's date", () => {
    expect(html).toContain("2025-07-11");
    expect(html).toMatch(/not (the city's|an) (official )?inspection/i);
  });

  it("has no answer chosen and nothing approved", () => {
    expect(html).not.toMatch(/checked=""/);
    expect(html).not.toMatch(/selected=""/);
    expect(html).toContain("Not reviewed");
    expect(html).toMatch(/<dt>Changes<\/dt><dd>0<\/dd>/);
    expect(html).toMatch(/<dt>Observations<\/dt><dd>0<\/dd>/);
    expect(html).toMatch(/<dt>Holds<\/dt><dd>0<\/dd>/);
  });

  it("says the changes are prepared, not applied, and that no name here is a municipal approval", () => {
    expect(html).toContain("Prepared changes · not applied");
    expect(html).toMatch(/typed by you/i);
    expect(html).toMatch(/not (a )?verified/i);
    expect(html).toMatch(/Download .* to keep/i);
    expect(html).toMatch(/Nothing is sent/i);
  });

  it("limits the reviewer name and the reason to the lengths the backend accepts", () => {
    const html = render();
    expect(html).toMatch(/<input[^>]*id="ledger-reviewer"[^>]*maxLength="200"|<input[^>]*maxLength="200"[^>]*id="ledger-reviewer"/);
    expect(html).toMatch(/<textarea[^>]*maxLength="2000"/);
  });

  it("cannot record yet, and says what is missing", () => {
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Record decision<\/button>/);
    expect(html).toContain("Enter the reviewer name.");
    expect(html).toContain("Choose a decision");
  });

  it("offers no download while there is nothing to download", () => {
    for (const label of ["Download journal", "Download changes", "Download observations"]) {
      expect(html).toMatch(new RegExp(`<button[^>]*disabled[^>]*>${label}`));
    }
  });

  it("counts a row with no source apart, and does not give it a form", () => {
    expect(html).toMatch(/1 (record|row) has no source snapshot/i);
    expect(html).not.toContain("OLD-1");
  });
});

describe("a list with no source snapshots", () => {
  it("offers nothing to review", () => {
    const html = render(initialReviewState(), [old]);
    expect(html).toMatch(/no source snapshot/i);
    expect(html).not.toContain('type="radio"');
    expect(html).not.toContain("Record decision");
    expect(html).not.toMatch(/<button[^>]*>Download/);
  });
});

describe("a record with nothing a photo can change", () => {
  const state = run([{ type: "go", index: 1, count: 2 }]);
  const html = render(state);

  it("shows the neighbouring-record warning and a box to confirm it was checked", () => {
    expect(html).toContain("TRE-53948 is 3 m away");
    expect(html).toMatch(/type="checkbox"/);
  });

  it("does not offer a change, only an observation or a hold", () => {
    expect(inputTag(html, "change")).toContain('disabled=""');
    expect(inputTag(html, "observe")).not.toContain("disabled");
    expect(inputTag(html, "hold")).not.toContain("disabled");
    expect(html).toMatch(/no attribute a photo can change/i);
  });
});

describe("a change to a field", () => {
  it("shows the city's question and the values, and only suggests the model's answer", () => {
    const key = ramp.ledger_source!.review_key;
    const html = render(run([{ type: "draft", key, patch: { decision: "change", field: "HAS_DETECTABLE_WARNING" } }]));
    expect(html).toContain("Is a detectable warning surface present?");
    expect(html).toMatch(/<option[^>]*value="HAS_DETECTABLE_WARNING"/);
    expect(html).not.toMatch(/<option[^>]*value="LAST_VERIFY_DATE"/);
    expect(html).toMatch(/suggested/i);
    expect(inputTag(html, "Yes")).toBeTruthy(); // the other value is offered...
    expect(inputTag(html, "Yes")).not.toContain("checked"); // ...and not chosen
    expect(inputTag(html, "No")).toBe(""); // the record already says No
  });
});

describe("recording decisions", () => {
  it("records nothing until the reviewer says so", () => {
    const key = ramp.ledger_source!.review_key;
    const s = run([{ type: "reviewer", value: "A. Reviewer" }, { type: "draft", key, patch: CHANGE }]);
    expect(Object.keys(s.decisions)).toEqual([]);
    expect(journalOf(rows, 17, s).decisions).toEqual([]);
  });

  it("records a change with the reviewer, reason and time", () => {
    const s = recordRamp();
    const j = journalOf(rows, 17, s);
    expect(j).toMatchObject({ schema_version: 1, scan_id: 17 });
    expect(j.decisions).toHaveLength(1);
    expect(j.decisions[0]).toMatchObject({ decision: "change", field: "HAS_DETECTABLE_WARNING", proposed_value: "Yes", reviewer: "A. Reviewer",
      note: "ok", reviewed_at: NOW, review_key: ramp.ledger_source!.review_key });
    expect(render(s)).toMatch(/<dt>Changes<\/dt><dd>1<\/dd>/);
  });

  it("refuses to record an incomplete draft", () => {
    const key = ramp.ledger_source!.review_key;
    const s = run([{ type: "draft", key, patch: { ...CHANGE, evidence: "unclear" } }, { type: "reviewer", value: "A" }, { type: "record", row: ramp, now: NOW }]);
    expect(s.decisions).toEqual({});
  });

  it("records a hold without a claim, and keeps it out of the changes", () => {
    const key = ramp.ledger_source!.review_key;
    const s = run([{ type: "reviewer", value: "A" }, { type: "draft", key, patch: { decision: "hold", note: "wait" } },
      { type: "record", row: ramp, now: NOW }]);
    const files: { name: string; text: string }[] = [];
    runDownload("journal", chk(), 17, s, (f) => files.push(f));
    runDownload("changes", chk(), 17, s, (f) => files.push(f));
    expect(JSON.parse(files[0].text).decisions).toHaveLength(1);
    expect(JSON.parse(files[0].text).decisions[0]).toMatchObject({ decision: "hold", field: null, proposed_value: null });
    expect(files[1].text.trim().split("\r\n")).toHaveLength(1);
  });

  it("takes an edit after recording as not recorded", () => {
    const key = ramp.ledger_source!.review_key;
    const s = run([{ type: "draft", key, patch: { note: "changed my mind" } }], recordRamp());
    expect(s.decisions).toEqual({});
  });

  it("removes a decision on request", () => {
    expect(run([{ type: "remove", key: ramp.ledger_source!.review_key }], recordRamp()).decisions).toEqual({});
  });

  it("goes only to rows that exist", () => {
    expect(run([{ type: "go", index: 5, count: 2 }]).index).toBe(1);
    expect(run([{ type: "go", index: -3, count: 2 }]).index).toBe(0);
  });
});

describe("downloads", () => {
  it("hand over the journal, the changes and the observations with the recorded content", () => {
    const key = tree.ledger_source!.review_key;
    const s = run([
      { type: "reviewer", value: "A. Reviewer" },
      { type: "draft", key, patch: { identity: "confirmed", evidence: "clear", decision: "observe", neighbors: true, note: "Same tree." } },
      { type: "record", row: tree, now: NOW },
    ], recordRamp("=1+1"));
    const save = vi.fn();
    runDownload("journal", chk(), 17, s, save);
    runDownload("changes", chk(), 17, s, save);
    runDownload("observations", chk(), 17, s, save);
    const [journal, changes, observations] = save.mock.calls.map((c) => c[0]);
    expect(journal.name).toBe("ledger-review-scan17-journal.json");
    expect(JSON.parse(journal.text).decisions.map((d: { record_id: string }) => d.record_id)).toEqual(["RAMP-12", "TRE-53943"]);
    expect(changes.name).toBe("ledger-review-scan17-changes.csv");
    expect(changes.text).toContain(",'=1+1,"); // a formula stays text
    expect(changes.text).not.toContain("TRE-53943");
    expect(observations.name).toBe("ledger-review-scan17-observations.csv");
    expect(observations.text).toContain("TRE-53943");
    expect(observations.text).toContain("2025-07-11");
    expect(observations.text).toContain("kev");
    expect(observations.text).not.toContain("RAMP-12");
  });

  it("are enabled only for what was recorded", () => {
    const html = render(recordRamp());
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Download journal/);
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Download changes/);
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Download observations/);
  });

  it("hand over the whole list the journal was made on, untouched, even before anything is recorded", () => {
    const save = vi.fn();
    runDownload("source", chk(), 17, initialReviewState(), save);
    const file = save.mock.calls[0][0];
    expect(file.name).toBe("ledger-review-scan17-source.json");
    expect(file.mime).toBe("application/json");
    expect(JSON.parse(file.text)).toEqual(JSON.parse(JSON.stringify(chk()))); // every key and row, the ones with no source too
  });

  it("offer the list as a file next to the journal, and say they belong together", () => {
    const html = render();
    expect(html).toMatch(/<button[^>]*>Download source list \(\.json\)<\/button>/);
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Download source list/);
    expect(html).toMatch(/journal .* checked against .* list/i);
    expect(render(initialReviewState(), [old])).not.toContain("Download source list");
  });

  it("do not include a decision for a row that is no longer in the list", () => {
    const s = recordRamp();
    const next = [treeRow(53948)];
    expect(journalOf(next, 17, s).decisions).toEqual([]);
    expect(() => runDownload("journal", chk(next), 17, s, () => {})).toThrow(/nothing/i);
  });

  it("are for the scan on screen", () => {
    expect(journalOf(rows, 18, recordRamp()).scan_id).toBe(18);
    expect(journalOf(rows, 18, recordRamp()).decisions).toHaveLength(1);
  });
});

describe("the map and the keyboard", () => {
  it("shows a button to show the record on the map when the list can", () => {
    const onShow = vi.fn();
    const html = render(initialReviewState(), rows, () => onShow);
    expect(html).toContain("Show on the map");
    expect(render()).not.toContain("Show on the map");
  });

  it("is made of native fields in a sensible order: grouped radios, labelled inputs, buttons that do not submit", () => {
    const html = render(recordRamp("x"));
    expect(html).toMatch(/<fieldset/);
    expect(html).toMatch(/<legend>/);
    expect(html).not.toMatch(/tabindex="[1-9]/i);
    expect(html).not.toMatch(/<div[^>]*onclick/i);
    for (const b of html.match(/<button[^>]*>/g) ?? []) expect(b).toContain('type="button"');
    for (const input of html.match(/<input[^>]*>/g) ?? []) {
      const id = /id="([^"]+)"/.exec(input)?.[1];
      if (/type="radio"|type="checkbox"/.test(input)) continue; // wrapped by their labels
      expect(id, input).toBeTruthy();
      expect(html).toContain(`for="${id}"`);
    }
  });

  it("steps between rows with buttons", () => {
    const html = render();
    expect(html).toMatch(/Row 1 of 2/);
    expect(html).toContain("Previous");
    expect(html).toContain("Next");
  });
});

describe("a pane with a different source", () => {
  it("starts empty: a decision recorded for one list is not in the next", () => {
    const html = renderToStaticMarkup(<LedgerReviewPane scan={{ ...scan, id: 18 } as Scan} city="Seattle" check={chk([row({}, source({ review_key: "rk-other" }))])} />);
    expect(html).toMatch(/<dt>Changes<\/dt><dd>0<\/dd>/);
    expect(html).toContain("Not reviewed");
  });
});

describe("the layout at 390px", () => {
  // The project has no node typings, and vitest hands back an empty string for a css import: read the file itself.
  let css = "";
  beforeAll(async () => {
    // @ts-expect-error node:fs has no types here
    const { readFileSync } = await import("node:fs");
    css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  });
  const block = (name: string) => [...css.matchAll(new RegExp(`${name.replace(".", "\\.")}[^{]*\\{[^}]*\\}`, "g"))].map((m) => m[0]).join("\n");

  it("lets long names and links wrap, and stacks the fields", () => {
    expect(css).toContain(".ledger-review");
    expect(css).toMatch(/\.ledger-review[^{]*\{[^}]*min-width:\s*0/);
    expect(css).toMatch(/overflow-wrap:\s*anywhere/);
    expect(css).toMatch(/@media \(max-width: 480px\)\s*\{[\s\S]*\.ledger-review/);
  });

  it("has no fixed width wider than a phone", () => {
    for (const m of block(".ledger-").matchAll(/(?<!max-|min-)width:\s*(\d+)px/g)) expect(Number(m[1])).toBeLessThanOrEqual(360);
  });
});
