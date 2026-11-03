import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { assetCheckReportUrl, assetCheckTableUrl } from "./api";
import AssetCheckPane, { AssetRowCard, orderRows, rowTarget, seenHow } from "./AssetCheckPane";
import type { MissedRecord } from "./cityMatch";
import type { Finding } from "./items";
import type { AssetCheckSummary, AssetRow, Scan } from "./types";

const counts = { in_view: 107, seen: 70, seen_by: {}, not_determined: 37, not_listed: 41, not_listed_unsure: 13, seen_unsure: 4, newer: 44 };
const actions = { "Update the date": 26, "No change": 17, "Visit on site": 64, "Compare with the list": 26 };
const summary: AssetCheckSummary = {
  city: "Seattle",
  items: [{ item: "street_tree", asset: "Street tree", city_list: "SDOT street trees", ...counts, actions }],
  totals: counts,
  actions,
  no_visit: 43,
  compare_unsure: 7,
  left_out: { repeats: 6, wrong: 1 },
  trust: { photo_months: ["2022-08", "2025-07"], controls: [], looked_at: 25, sent_back: 3, outside_view: 6 },
};
const scan = { id: 17, items: [{ id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }] } as unknown as Scan;
const evidence = { url: "/api/evidence/scan17_look0_7.jpg", creator: "kev", captured_at: "2025-07-11T00:00:00Z",
  link: "https://www.mapillary.com/app/?pKey=7", license: "CC BY-SA 4.0", blurred: true };

function row(over: Partial<AssetRow> = {}): AssetRow {
  return { item: "street_tree", asset: "Street tree", city_list: "SDOT street trees", city_record: "TRE-1", city_location: "1 Main St",
    status: "Seen in photo", how_seen: "Second look, panorama", models_unsure: "", city_date: "2019-03",
    seen_in_photo_on: "2025-07-11", photo_newer_than_city_date: "yes", city_condition: "", lat: 47.6, lng: -122.3,
    position_source: "City record", position_accuracy: "City record", box: [100, 200, 300, 400], evidence,
    action: "Update the date", city_says: "LAST_VERIFY_DATE 2019-03", photo_shows: "Seen in a photo of 2025-07-11", ...over };
}

describe("AssetCheckPane", () => {
  const html = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} />);

  it("opens with how many records are candidates for desk review, not how many visits were avoided", () => {
    expect(html).toContain("<strong>107</strong> Seattle records in the photos&#x27; view");
    expect(html).toContain("<strong>43</strong> are candidates for desk review and <strong>64</strong> need a newer photo or a field check");
    expect(html).toContain("not a measured count of avoided site visits");
    expect(html).not.toContain("need no site visit");
    expect(html).toContain("<strong>26</strong> more objects are not on the city&#x27;s lists, 7 of them not settled");
  });

  it("says how many records in the box the photos reached, when the list knows", () => {
    const reached = { ...summary, totals: { ...counts, in_area: 3292 } };
    const out = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={reached} findings={[]} missed={[]} />);
    expect(out).toContain("Of <strong>3,292</strong> Seattle records in the survey&#x27;s box or its photos&#x27; view, the photos reached <strong>107</strong>:");
    expect(out).toContain("<strong>43</strong> are candidates for desk review and <strong>64</strong> need a newer photo or a field check");
  });

  it("gives every asset the same row, in columns that fit the panel", () => {
    expect(html).toContain('<th scope="row">Street tree</th><td>107</td><td>43</td><td>64</td><td>26</td>');
    expect(html).not.toContain("Correct the record");
  });

  it("has one filter per action with rows, and opens on a list of several assets", () => {
    for (const label of ["Update the date", "No change", "Visit on site", "Compare with the list"]) expect(html).toContain(label);
    expect(html).toContain('aria-pressed="true"><strong>26</strong><span>Update the date</span>');
    expect(html).toContain("<strong>64</strong><span>Visit on site</span>"); // the same number as the sentence above
  });

  it("says a row is a suggestion", () => {
    expect(html).toContain("Every row is a suggestion");
    expect(html).toContain("Nothing here changes the city");
  });

  it("offers the page and the table", () => {
    expect(html).toContain(`href="${assetCheckReportUrl(17, false)}"`);
    expect(html).toContain(`href="${assetCheckTableUrl(17, "csv")}"`);
    expect(html).toContain(`href="${assetCheckTableUrl(17, "geojson")}"`);
  });

  it("never gets ahead of the city's decision", () => {
    for (const word of ["Add to DB", "Unrecorded", "verified", "confirmed", "patch"]) expect(html).not.toContain(word);
  });
});

describe("a city's credit for its records", () => {
  it("is shown with the numbers when the city's data asks for one, above the rows", () => {
    const credit = "City records: NYC Open Data.";
    const html = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={{ ...summary, credit }} findings={[]} missed={[]} />);
    expect(html).toContain(credit);
    expect(html.indexOf(credit)).toBeLessThan(html.indexOf('class="asset-table"'));
  });
});

describe("the page and the table", () => {
  it("says what the check of its findings found, when there was one", () => {
    const checked = { ...summary, trust: { ...summary.trust, findings: { n: 57, yes: 43, no: 3, unclear: 11 } } };
    expect(renderToStaticMarkup(<AssetCheckPane scan={scan} summary={checked} findings={[]} missed={[]} />))
      .toContain("An AI assistant checked 57 findings on their photos: 43 right, 3 wrong, 11 could not be told.");
    expect(renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} />))
      .not.toContain("An AI assistant checked");
  });
  it("says what the second looks cost on top of the survey, only when there were some", () => {
    const paid = { ...scan, cost_usd: 3.33 } as Scan;
    const looks = { ...summary, trust: { ...summary.trust, second_looks_usd: 2.3028 } };
    expect(renderToStaticMarkup(<AssetCheckPane scan={paid} summary={looks} findings={[]} missed={[]} />))
      .toContain("The survey cost $3.33. The second looks behind this list, run separately, cost $2.30 more.");
    expect(renderToStaticMarkup(<AssetCheckPane scan={paid} summary={summary} findings={[]} missed={[]} />))
      .not.toContain("second looks behind this list");
  });
  it("gives a visit the photos did not settle a survey photo from nearby, and says what it is", () => {
    const nearby_photo = { link: "https://www.mapillary.com/app/?pKey=5", creator: "kev", captured_at: "2026-07-01T00:00:00Z",
      license: "CC BY-SA 4.0", dist_m: 14.2 };
    const html = renderToStaticMarkup(<ul><AssetRowCard row={row({ evidence: null, box: null, nearby_photo })} scan={scan} /></ul>);
    expect(html).toContain("See a survey photo taken 14 m from the record&#x27;s position");
    expect(html).toContain("It may not show the record.");
    expect(html).toContain('href="https://www.mapillary.com/app/?pKey=5"');
    expect(html).toContain("Photo by kev, 2026-07");
    expect(html).toContain("View on Mapillary");
    expect(html).toContain("mapillary-logo");
    expect(html).toContain("CC BY-SA 4.0");
    expect(renderToStaticMarkup(<ul><AssetRowCard row={row()} scan={scan} /></ul>)).not.toContain("It may not show the record.");
  });
  it("offers the list on the map where the map can change", () => {
    expect(renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} onSwitchLens={() => undefined} />))
      .toContain("See the visits on the map");
    expect(renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} />))
      .not.toContain("See the visits on the map");
  });
  it("sit above the rows, so opening them takes no scrolling past the photos", () => {
    const html = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} />);
    expect(html.indexOf("Open the report")).toBeLessThan(html.indexOf('class="asset-rows"'));
    expect(html.indexOf('class="asset-filters"')).toBeLessThan(html.indexOf("Open the report"));
  });
});

describe("the urls", () => {
  it("are files on the static demo", () => {
    expect(assetCheckReportUrl(17, false)).toBe("/api/scans/17/asset-check/report");
    expect(assetCheckReportUrl(17, true)).toBe("/api/scans/17/asset-check/report.html");
    expect(assetCheckTableUrl(17, "csv")).toBe("/api/scans/17/asset-check/table.csv");
  });
});

describe("AssetRowCard", () => {
  it("says what to do and shows both sides of a change", () => {
    const html = renderToStaticMarkup(<ul><AssetRowCard row={row()} scan={scan} /></ul>);
    expect(html).toContain("Update the date");
    expect(html).toContain("City says</dt><dd>LAST_VERIFY_DATE 2019-03");
    expect(html).toContain("Photo shows</dt><dd>Seen in a photo of 2025-07-11");
  });

  it("leaves out the sides of a row that changes nothing", () => {
    const html = renderToStaticMarkup(<ul><AssetRowCard row={row({ action: "No change", city_says: "", photo_shows: "" })} scan={scan} /></ul>);
    expect(html).not.toContain("City says");
    expect(html).not.toContain("Photo shows");
  });

  it("shows the photo with its credit and the two dates", () => {
    const html = renderToStaticMarkup(<ul><AssetRowCard row={row()} scan={scan} /></ul>);
    expect(html).toContain("TRE-1");
    expect(html).toContain("City date</dt><dd>2019-03");
    expect(html).toContain("Seen in photo on</dt><dd>2025-07-11");
    expect(html).toContain("Photo by kev");
    expect(html).toContain("View on Mapillary");
    expect(html).toContain("CC BY-SA 4.0");
  });

  it("says when a photo was cut from a panorama, and not otherwise", () => {
    const cut = "A flat view cut from a 360-degree photo.";
    expect(renderToStaticMarkup(<ul><AssetRowCard row={row()} scan={scan} /></ul>)).toContain(cut);
    expect(renderToStaticMarkup(<ul><AssetRowCard row={row({ how_seen: "Survey" })} scan={scan} /></ul>)).not.toContain(cut);
  });

  it("has no photo to credit when the photos settled nothing", () => {
    const html = renderToStaticMarkup(<ul><AssetRowCard row={row({ status: "Not determined", evidence: null, box: null, how_seen: "", seen_in_photo_on: "" })} scan={scan} /></ul>);
    expect(html).toContain("Not determined");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("Photo by");
  });

  it("says a row off the list has no city record and how far its position goes", () => {
    const html = renderToStaticMarkup(<ul><AssetRowCard row={row({ city_record: "", city_location: "", status: "Not on the city list", position_accuracy: "Unknown (within 50 ft)" })} scan={scan} /></ul>);
    expect(html).toContain("No city record");
    expect(html).toContain("Unknown (within 50 ft)");
  });

  it("offers the map only when the row has a place on it", () => {
    expect(renderToStaticMarkup(<ul><AssetRowCard row={row()} scan={scan} /></ul>)).not.toContain("Show on the map");
    expect(renderToStaticMarkup(<ul><AssetRowCard row={row()} scan={scan} onShow={() => {}} /></ul>)).toContain("Show on the map");
  });
});

describe("seenHow", () => {
  it("lines the count up with the survey's own", () => {
    expect(seenHow({ Survey: 45, "Second look": 9, "Second look, panorama": 16 }))
      .toBe("45 by the survey, 9 on a second look, 16 on a second look in panoramas");
    expect(seenHow({ Survey: 2 })).toBe("2 by the survey");
  });
});

describe("orderRows", () => {
  it("puts the newest evidence first and takes the assets in turn", () => {
    const a = row({ item: "a", city_record: "a1" });
    const a2 = row({ item: "a", city_record: "a2" });
    const b = row({ item: "b", city_record: "b1" });
    const old = row({ item: "a", city_record: "a3", how_seen: "Survey" });
    expect(orderRows([old, a, a2, b]).map((r) => r.city_record)).toEqual(["a1", "b1", "a2", "a3"]);
  });
});

describe("rowTarget", () => {
  const record: MissedRecord = { key: "record:street_tree:0", item: "street_tree", layer: "Trees", lat: 1, lng: 2,
    fields: [["COMPKEY", "9"], ["UNITID", "TRE-1"]] };
  const finding = { item: "street_tree", picked: { kind: "issue", item: { evidence } } } as unknown as Finding;

  it("finds the survey's finding on the same photo", () => {
    expect(rowTarget(row(), [finding], [record])).toEqual({ finding });
  });

  it("falls back on the city record the survey did not find", () => {
    expect(rowTarget(row(), [], [record])).toEqual({ record });
  });

  it("does not take a finding of another item on the same photo", () => {
    const other = { ...finding, item: "utility_pole" } as unknown as Finding;
    expect(rowTarget(row(), [other], [record])).toEqual({ record });
  });

  it("has nothing to show when neither is there", () => {
    expect(rowTarget(row({ evidence: null }), [finding], [])).toBeNull();
  });
});

describe("an object seen is not an attribute checked", () => {
  const open = row({ action: "Visit on site", attribute_field: "IS_STREETLIGHT", attribute_state: "unsettled",
    attribute_note: "Holds a street light: the photo is too unclear, cropped or far away to answer it",
    action_if_attribute_agrees: "Update the date",
    visit_reason: "The record is there in a photo, but the city's 'Holds a street light' is not settled. A newer photo or a field check is needed." });
  const html = renderToStaticMarkup(<AssetRowCard row={open} scan={scan} />);

  it("keeps the row's status and dates and names the attribute that is still open", () => {
    expect(html).toContain("Seen in photo");
    expect(html).toContain("2019-03");
    expect(html).toContain("IS_STREETLIGHT: unsettled");
    expect(html).toContain("too unclear, cropped or far away");
  });
  it("says why it is a visit and what it would be if the attribute agreed", () => {
    expect(html).toContain("A newer photo or a field check is needed.");
    expect(html).toContain("If the attribute agreed");
    expect(html).toContain("Update the date");
  });
  it("shows a row from before the attribute fields exactly as it was", () => {
    const plain = renderToStaticMarkup(<AssetRowCard row={row()} scan={scan} />);
    expect(plain).not.toContain("Attribute");
    expect(plain).not.toContain("Why a visit");
  });

  const withAttributes: AssetCheckSummary = { ...summary, actions: { "Visit on site": 64, "Compare with the list": 26 }, desk_review_candidates: 43, visits_attribute_unresolved: 14,
    attribute_checks: { required: 28, agree: 7, different: 1, unsettled: 14, not_checked: 6, open_by: { unclear: 14, no_check: 6 } } };
  const pane = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={withAttributes} findings={[]} missed={[]} />);
  it("says how many seen records have an attribute left open, and that seen is not checked", () => {
    expect(pane).toContain("Seen in a photo is not the same as checked: 20 of the 28 seen records with an attribute to check have it open");
    expect(pane).toContain("14 unsettled and 6 not checked");
  });
  it("names the visit rows as needing a newer photo or a field check", () => {
    expect(pane).toContain("These need a newer photo or a field check.");
    expect(pane).not.toContain("or a site visit");
  });
  it("does not mention attributes for a list made before them", () => {
    const old = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} />);
    expect(old).not.toContain("not the same as checked");
  });
  it("calls an AI assistant's check a prototype, not an independent evaluation", () => {
    const checked = { ...summary, trust: { ...summary.trust, findings: { n: 57, yes: 43, no: 3, unclear: 11 } } };
    const out = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={checked} findings={[]} missed={[]} />);
    expect(out).toContain("a prototype check by an AI assistant, not an independent evaluation");
  });
});
