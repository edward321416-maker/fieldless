import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import CityRecordCard from "./CityRecordCard";
import type { MissedRecord } from "./cityMatch";
import type { ItemMeta } from "./types";

const record: MissedRecord = { key: "record:street_tree:0", item: "street_tree", layer: "SDOT street trees",
  lat: 47.61, lng: -122.32, fields: [["COMPKEY", "222410"], ["UNITID", "TRE-54698"]] };
const items: ItemMeta[] = [{ id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }];

describe("a city record the survey did not find", () => {
  it("names the item and the city's list, gives the city's ids, says what to do, and closes", () => {
    const html = renderToStaticMarkup(<CityRecordCard record={record} items={items} onClose={() => undefined} />);
    expect(html).toContain("Street tree");
    expect(html).toContain("SDOT street trees");
    expect(html).toContain("<dt>COMPKEY</dt>");
    expect(html).toContain("<dd>222410</dd>");
    expect(html).toContain("TRE-54698");
    expect(html).toContain(
      "In the city&#x27;s list and in a photo&#x27;s view; not found by this survey. Check on site or in the photos.");
    expect(html).toMatch(/<button type="button"[^>]*>Close<\/button>/);
  });
  it("says what the update list's row says when a photo settled the record, not to check on site", () => {
    const row = { item: "street_tree", city_record: "TRE-54698", action: "Update the date", how_seen: "Second look, panorama",
      seen_in_photo_on: "2025-09-04" } as never;
    const html = renderToStaticMarkup(<CityRecordCard record={record} items={items} row={row} onClose={() => undefined} />);
    expect(html).toContain("Seen in a photo of 2025-09-04, on a second look in a panorama; the survey itself did not find it. Suggestion: Update the date.");
    expect(html).not.toContain("Check on site");
  });
  it("keeps asking for a look when the row is a visit", () => {
    const row = { item: "street_tree", city_record: "TRE-54698", action: "Visit on site", how_seen: "", seen_in_photo_on: "" } as never;
    expect(renderToStaticMarkup(<CityRecordCard record={record} items={items} row={row} onClose={() => undefined} />))
      .toContain("Check on site or in the photos.");
  });
});
