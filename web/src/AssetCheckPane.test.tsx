import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { assetCheckReportUrl, assetCheckTableUrl } from "./api";
import AssetCheckPane, { AssetRowCard, orderRows, rowTarget, seenHow } from "./AssetCheckPane";
import type { MissedRecord } from "./cityMatch";
import type { Finding } from "./items";
import type { AssetCheckSummary, AssetRow, Scan } from "./types";

const counts = { in_view: 107, seen: 70, seen_by: {}, not_determined: 37, not_listed: 41, not_listed_unsure: 13, seen_unsure: 4, newer: 44 };
const summary: AssetCheckSummary = {
  city: "Seattle",
  items: [{ item: "street_tree", asset: "Street tree", city_list: "SDOT street trees", ...counts }],
  totals: counts,
  trust: { photo_months: ["2022-08", "2025-07"], controls: [], looked_at: 25, sent_back: 3, outside_view: 6 },
};
const scan = { id: 17, items: [{ id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }] } as unknown as Scan;
const evidence = { url: "/api/evidence/scan17_look0_7.jpg", creator: "kev", captured_at: "2025-07-11T00:00:00Z",
  link: "https://www.mapillary.com/app/?pKey=7", license: "CC BY-SA 4.0", blurred: true };

function row(over: Partial<AssetRow> = {}): AssetRow {
  return { item: "street_tree", asset: "Street tree", city_list: "SDOT street trees", city_record: "TRE-1", city_location: "1 Main St",
    status: "Seen in photo", how_seen: "Second look, panorama", models_unsure: "", city_date: "2019-03",
    seen_in_photo_on: "2025-07-11", photo_newer_than_city_date: "yes", city_condition: "", lat: 47.6, lng: -122.3,
    position_source: "City record", position_accuracy: "City record", box: [100, 200, 300, 400], evidence, ...over };
}

describe("AssetCheckPane", () => {
  const html = renderToStaticMarkup(<AssetCheckPane scan={scan} summary={summary} findings={[]} missed={[]} />);

  it("says what the photos settled and what they did not", () => {
    expect(html).toContain("<strong>107</strong> Seattle records in the photos&#x27; view");
    expect(html).toContain("<strong>70</strong> were seen and <strong>37</strong> could not be settled");
    expect(html).toContain("<strong>41</strong> more objects are not on the city&#x27;s lists");
    expect(html).toContain("the models were unsure of 13 of those");
  });

  it("gives every asset the same row", () => {
    expect(html).toContain('<th scope="row">Street tree</th><td>107</td><td>70</td><td>37</td><td>41</td>');
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

describe("the urls", () => {
  it("are files on the static demo", () => {
    expect(assetCheckReportUrl(17, false)).toBe("/api/scans/17/asset-check/report");
    expect(assetCheckReportUrl(17, true)).toBe("/api/scans/17/asset-check/report.html");
    expect(assetCheckTableUrl(17, "csv")).toBe("/api/scans/17/asset-check/table.csv");
  });
});

describe("AssetRowCard", () => {
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
