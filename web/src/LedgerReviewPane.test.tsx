import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { NOW, row, source, treeRow } from "./ledgerFixtures";
import { EMPTY_DRAFT, type Draft } from "./ledgerReview";
import LedgerReviewPane, {
  LedgerReviewView, captureOlder, initialReviewState, olderFile, journalOf, reviewReducer, runDownload, type ReviewState, type SessionUi,
} from "./LedgerReviewPane";
import { planImport } from "./ledgerImport";
import { createRecoveryStore, emptyState, serializeRecovery, RECOVERY_PREFIX, type RecoveryStorage } from "./ledgerRecovery";
import { startSession, syncRecovery } from "./ledgerSession";
import { reviewSignature } from "./ledgerReview";
import type { AssetRow, Scan } from "./types";

const scan = { id: 17, items: [{ id: "curb_ramp", target: "curb ramp", map_icon: "ramp", kind: "check", inventory: true },
  { id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }] } as unknown as Scan;
const ramp = row({ action: "Correct the record" });
const tree = treeRow(53943, ["TRE-53948 is 3 m away and was photographed 2 s apart."]);
const old = row({ city_record: "OLD-1" }, null);
const rows: AssetRow[] = [ramp, tree, old];
const chk = (list: AssetRow[] = rows) => ({ scan_id: 17, city: "Seattle", totals: { in_view: 3 }, rows: list });
const CHANGE: Draft = { ...EMPTY_DRAFT, identity: "confirmed", evidence: "clear", decision: "change",
  field: "HAS_DETECTABLE_WARNING", proposed: "Yes", note: "The panel is visible." };

function render(state: ReviewState = initialReviewState(), list: AssetRow[] = rows, showFor?: (r: AssetRow) => (() => void) | undefined, ui?: Partial<SessionUi>) {
  return renderToStaticMarkup(<LedgerReviewView scan={scan} city="Seattle" check={chk(list)} state={state} dispatch={() => {}} showFor={showFor} save={() => {}}
    ui={ui ? { ...QUIET, ...ui } : undefined} />);
}
const NOOP = () => {};
const QUIET: SessionUi = { onOverwrite: NOOP, onDownloadOlder: NOOP, notices: [], problem: "", plan: null, askingClear: false, onFile: NOOP, onConfirmImport: NOOP, onCancelImport: NOOP, onAskClear: NOOP,
  onKeepSaved: NOOP, onClear: NOOP };
const rkOf = (r: AssetRow) => r.ledger_source!.review_key;
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
    expect(html.slice(html.indexOf("ledger-card"))).not.toMatch(/selected=""/);
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
  const state = run([{ type: "select", key: rkOf(tree) }]);
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

  it("selects a row by its review key, not by its place in the list", () => {
    expect(run([{ type: "select", key: rkOf(tree) }]).selected).toBe(rkOf(tree));
    const html = render(run([{ type: "select", key: rkOf(tree) }]), [tree, ramp]);
    expect(html).toContain("TRE-53943");
    expect(html).toMatch(/Row \d of 2/);
  });

  it("shows the first row of the queue when the selected row is not in it", () => {
    const html = render(run([{ type: "select", key: "rk-gone" }]));
    expect(html).toContain("RAMP-12");
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

describe("finding a record", () => {
  const many: AssetRow[] = Array.from({ length: 93 }, (_, i) => row({ city_record: `CBR-${30000 + i}`, city_location: `${i} Pine St` },
    source({ record_id: `CBR-${30000 + i}`, record_key: `fid:${i}`, review_key: `rk-${30000 + i}-` + "d".repeat(20) })));
  const fix = row({ action: "Correct the record", city_record: "CBR-39290", city_location: "9290 Pine St" },
    source({ record_id: "CBR-39290", record_key: "fid:39290", review_key: "rk-39290-" + "d".repeat(20) }));
  const all = [...many, fix];

  it("opens the correction first in a list of 94 rows, not after 93 Next", () => {
    const html = render(initialReviewState(), all);
    expect(html).toContain("CBR-39290");
    expect(html).toMatch(/Row 1 of 94/);
  });

  it("opens a record by its ID", () => {
    const html = render(run([{ type: "queue", patch: { query: "cbr-30041" } }]), all);
    expect(html).toContain("CBR-30041");
    expect(html).toMatch(/Row 1 of 1/);
  });

  it("opens the record that was typed even while an action is chosen elsewhere, and says it searched the whole list", () => {
    const html = render(run([{ type: "queue", patch: { action: "No change", query: "CBR-39290" } }]), all);
    expect(html).toContain("CBR-39290");
    expect(html).toMatch(/whole list/i);
  });

  it("says nothing matched and offers to clear the search", () => {
    const html = render(run([{ type: "queue", patch: { query: "zzz" } }]), all);
    expect(html).toMatch(/No record matches/i);
    expect(html).toContain("Clear search and filters");
    expect(html).not.toContain("Record decision");
  });

  it("offers the search, the item, the state and the whole-list control as native, labelled fields", () => {
    const html = render();
    expect(html).toMatch(/<input[^>]*id="ledger-search"/);
    expect(html).toMatch(/<label[^>]*for="ledger-search"/);
    expect(html).toMatch(/<select[^>]*id="ledger-item"/);
    expect(html).toMatch(/<select[^>]*id="ledger-state"/);
    for (const label of ["All", "Candidates", "Corrections", "Identity doubt", "Unclear", "On hold", "Not reviewed"]) expect(html).toContain(label);
  });

  it("shows how many rows each state holds", () => {
    expect(render()).toMatch(/Identity doubt \(1\)/);
    expect(render()).toMatch(/Corrections \(1\)/);
  });

  it("keeps the action chosen elsewhere as a scope, with the whole list one button away", () => {
    const html = render(run([{ type: "queue", patch: { action: "Update the date" } }]));
    expect(html).toContain("Review newer photo evidence");
    expect(html).toContain("Show the whole list");
    expect(html).toMatch(/Row 1 of 1/);
    expect(render()).not.toContain("Show the whole list");
  });

  it("filters never move a decision: a hidden decision stays in the counts and in the files", () => {
    const s = run([{ type: "queue", patch: { filter: "unreviewed" } }], recordRamp());
    expect(render(s)).toMatch(/<dt>Changes<\/dt><dd>1<\/dd>/);
    expect(journalOf(rows, 17, s).decisions).toHaveLength(1);
    expect(render(s)).not.toContain("RAMP-12</h4>");
  });

  it("resets the selection when the search changes, so the best match is shown", () => {
    const s = run([{ type: "select", key: rkOf(tree) }, { type: "queue", patch: { query: "RAMP" } }]);
    expect(s.selected).toBe("");
    expect(s.queue.query).toBe("RAMP");
  });
});

describe("what the list says about identity and photo clarity", () => {
  const quality = (over: Partial<AssetRow>) => render(initialReviewState(), [row({ action: "Visit on site", ...over })]);

  it("shows an unresolved identity with its reason and the other records it could be", () => {
    const html = quality({ identity_state: "unresolved", identity_reason: "Two tree records 3 m apart were photographed 2 s apart.", competing_record_ids: ["TRE-53948", "TRE-53949"] });
    expect(html).toContain("Two tree records 3 m apart");
    expect(html).toContain("TRE-53948");
    expect(html).toContain("TRE-53949");
    expect(html).toMatch(/not settled/i);
  });

  it("shows unclear photo evidence with its reason", () => {
    const html = quality({ evidence_quality: "unclear", evidence_quality_reason: "An AI visual audit could not tell the ramp apart." });
    expect(html).toContain("An AI visual audit could not tell the ramp apart.");
    expect(html).toMatch(/not clear/i);
  });

  it("never reads not_flagged or not_reviewed as verified", () => {
    const html = quality({ identity_state: "not_flagged", evidence_quality: "not_reviewed" });
    expect(html).toMatch(/does not verify|not a verification|not verified/i);
    expect(html).toMatch(/not been reviewed/i);
    expect(html).not.toMatch(/identity (is )?verified|verified identity|photo is clear/i);
  });

  it("says nothing when the list has no quality fields", () => {
    expect(render()).not.toMatch(/Identity not settled|Photo clarity/);
  });

  it("does not add an approval rule of its own: the existing confirmations still decide", () => {
    const s = run([{ type: "reviewer", value: "A" }, { type: "draft", key: rkOf(ramp), patch: { ...CHANGE, identity: "uncertain" } },
      { type: "record", row: ramp, now: NOW }]);
    expect(s.decisions).toEqual({});
  });
});

describe("saved work, notices and import", () => {
  it("shows what was restored or could not be", () => {
    const html = render(initialReviewState(), rows, undefined, { notices: [{ id: "stale", tone: "warn", text: "Old work is unavailable for this version." }] });
    expect(html).toContain("Old work is unavailable for this version.");
  });

  it("warns, where it can be seen, when this browser cannot keep the work", () => {
    for (const problem of ["full", "unavailable"] as const) {
      const html = render(initialReviewState(), rows, undefined, { problem });
      expect(html).toMatch(/role="alert"[^>]*>[^<]*(could not be saved|cannot save|not saved)/i);
      expect(html).toMatch(/<button[^>]*>Download source list/); // manual export stays
    }
    expect(render(initialReviewState(), rows, undefined, { problem: "full" })).toMatch(/full/i);
    expect(render()).not.toMatch(/could not be saved/i);
  });

  it("says when another tab saved over this scan, and offers to keep this tab's work instead", () => {
    const html = render(initialReviewState(), rows, undefined, { problem: "conflict" });
    expect(html).toMatch(/role="alert"[^>]*>[^<]*another tab/i);
    expect(html).toMatch(/<button[^>]*>Save this tab&#x27;s work over it<\/button>/);
    expect(render()).not.toContain("Save this tab");
  });

  it("offers to download an older saved file before new work replaces it", () => {
    const html = render(initialReviewState(), rows, undefined, { notices: [{ id: "stale", tone: "warn", text: "Old.", older: true }] });
    expect(html).toMatch(/<button[^>]*>Download the older saved work<\/button>/);
    expect(render(initialReviewState(), rows, undefined, { notices: [{ id: "restored", tone: "info", text: "Back." }] })).not.toContain("older saved work");
  });

  it("says where the work is kept and what is in it", () => {
    const html = render();
    expect(html).toMatch(/saved in this browser/i);
    expect(html).toMatch(/reviewer name/i);
    expect(html).toMatch(/notes/i);
    expect(html).toMatch(/not sent/i);
  });

  it("offers an explicit clear, and asks before it removes anything", () => {
    expect(render()).toMatch(/<button[^>]*>Clear saved work…<\/button>/);
    const html = render(initialReviewState(), rows, undefined, { askingClear: true });
    expect(html).toContain("Clear this scan");
    expect(html).toContain("Clear all scans");
    expect(html).toContain("Keep my work");
  });

  it("offers import of a journal or a package, and says what a journal lacks", () => {
    const html = render();
    expect(html).toMatch(/<input[^>]*type="file"[^>]*id="ledger-import"|<input[^>]*id="ledger-import"[^>]*type="file"/);
    expect(html).toMatch(/<label[^>]*for="ledger-import"/);
    expect(html).toMatch(/journal or review package/i);
  });

  it("previews an import before anything is replaced, and says what would be lost", () => {
    const made = journalOf(rows, 17, recordRamp());
    const plan = planImport(JSON.stringify(made), { scanId: 17, rows, current: { ...initialReviewState(), drafts: { x: { ...EMPTY_DRAFT, note: "n" } }, decisions: {} } });
    const html = render(recordRamp(), rows, undefined, { plan });
    expect(html).toContain("Import preview");
    expect(html).toContain("HAS_DETECTABLE_WARNING");
    expect(html).toMatch(/<button[^>]*>Replace my work with this file<\/button>/);
    expect(html).toMatch(/<button[^>]*>Cancel<\/button>/);
    expect(html).toMatch(/replace/i);
  });

  it("explains a rejected file and says the current work is untouched", () => {
    const plan = planImport("{broken", { scanId: 17, rows, current: initialReviewState() });
    const html = render(recordRamp(), rows, undefined, { plan });
    expect(html).toMatch(/role="alert"/);
    expect(html).toContain("not readable JSON");
    expect(html).toMatch(/current work is unchanged|nothing was imported/i);
    expect(html).not.toContain("Replace my work with this file");
  });

  it("replaces the whole state in one step, or not at all", () => {
    const next = { ...initialReviewState(), reviewer: "Z" };
    expect(run([{ type: "replace", state: next }], recordRamp())).toBe(next);
  });
});

describe("a pane that opens on an action", () => {
  it("starts in that action's rows, with nothing recorded", () => {
    const html = renderToStaticMarkup(<LedgerReviewPane scan={scan} city="Seattle" check={chk()} initialAction="Update the date" />);
    expect(html).toContain("Show the whole list");
    expect(html).toContain("TRE-53943");
    expect(html).toMatch(/<dt>Changes<\/dt><dd>0<\/dd>/);
  });
});

describe("the shared action button", () => {
  let css = "";
  beforeAll(async () => {
    // @ts-expect-error node:fs has no types here
    const { readFileSync } = await import("node:fs");
    css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  });

  it("keeps its blue hover where a button is also matched by the plain button hover rule", () => {
    // button:hover:not(:disabled) is more specific than .action-export-btn:hover and used to turn the white label light on light.
    expect(css).toMatch(/\.action-export-btn:hover:not\(:disabled\)\s*\{[^}]*background:\s*#1e40af/);
    expect(css).toMatch(/\.action-export-btn:hover:not\(:disabled\)\s*\{[^}]*color:\s*#fff/);
    expect(css).toMatch(/\.action-export-btn:disabled:hover\s*\{[^}]*background:\s*#1d4ed8/);
  });
});

describe("the layout of the new controls", () => {
  let css = "";
  beforeAll(async () => {
    // @ts-expect-error node:fs has no types here
    const { readFileSync } = await import("node:fs");
    css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");
  });
  it("stacks the queue controls at 390px and lets the preview wrap", () => {
    expect(css).toMatch(/\.ledger-queue[^{]*\{[^}]*min-width:\s*0/);
    expect(css).toMatch(/@media \(max-width: 480px\)\s*\{[\s\S]*\.ledger-queue/);
    expect(css).toMatch(/\.ledger-preview[^{]*\{[^}]*overflow-wrap:\s*anywhere/);
  });
});

describe("the handoff", () => {
  it("is part of the files to keep, with the recorded decisions in its preview and the package to download", () => {
    const html = render(recordRamp());
    expect(html).toContain("Handoff package");
    expect(html).toContain("HAS_DETECTABLE_WARNING");
    expect(html).toMatch(/<button[^>]*>Download review package<\/button>/);
    expect(html).toContain("docs/ledger-workflow.md");
  });

  it("offers no package before a decision is recorded", () => {
    expect(render()).toMatch(/<button[^>]*disabled[^>]*>Download review package<\/button>/);
  });
});

describe("the older saved work link keeps pointing at the older copy (review finding A-7)", () => {
  class Mem implements RecoveryStorage {
    data = new Map<string, string>();
    get length() { return this.data.size; }
    key(i: number) { return [...this.data.keys()][i] ?? null; }
    getItem(k: string) { return this.data.get(k) ?? null; }
    setItem(k: string, v: string) { this.data.set(k, v); }
    removeItem(k: string) { this.data.delete(k); }
  }
  const work = (note: string) => run([{ type: "reviewer", value: "A. Reviewer" }, { type: "draft", key: rkOf(ramp), patch: { ...CHANGE, note } }]);

  it("downloads the old saved text after new work replaced it in the browser, and restores no decision from it", () => {
    const mem = new Mem();
    const store = createRecoveryStore(() => mem, () => NOW);
    // work saved on an older version of the list: stale for this one
    const oldText = serializeRecovery(17, "OLD-SIGNATURE", { ...emptyState(), reviewer: "Old Reviewer", drafts: { [rkOf(ramp)]: { ...CHANGE, note: "OLD note" } } }, NOW);
    mem.data.set(`${RECOVERY_PREFIX}17`, oldText);
    const signature = reviewSignature(17, rows);
    const begin = startSession(store, 17, signature, rows, "", null);
    expect(begin.notices.some((n) => n.older)).toBe(true);
    const older = captureOlder(store, 17, begin.notices); // what the session does at its start

    // the reviewer types new work: the browser's copy is replaced
    const mine = work("NEW note");
    syncRecovery(store, 17, signature, mine, begin.owns);
    expect(store.raw(17)).not.toBe(oldText);
    expect(JSON.parse(store.raw(17)!).drafts[rkOf(ramp)].note).toBe("NEW note");

    const file = olderFile(17, older);
    expect(file).not.toBeNull();
    expect(file!.text).toBe(oldText);
    expect(file!.name).toBe("ledger-review-scan17-older-saved-work.json");
    // nothing from the old file came back as work or as a recorded decision
    expect(begin.state.decisions).toEqual({});
    expect(begin.state.drafts).toEqual({});
    expect(Object.keys(mine.decisions)).toEqual([]);
  });

  it("captures nothing when the notice does not point at an older copy, so no link claims one", () => {
    const store = createRecoveryStore(() => new Mem(), () => NOW);
    expect(captureOlder(store, 17, [{ id: "restored", tone: "info", text: "Back." }])).toBeNull();
    expect(olderFile(17, null)).toBeNull();
  });
});
