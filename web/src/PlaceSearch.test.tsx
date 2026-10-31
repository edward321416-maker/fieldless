import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import PlaceSearch, { parseNominatimResults, PRESET_PLACES } from "./PlaceSearch";

describe("PlaceSearch omnibox", () => {
  it("renders a street/neighborhood/city search input in the top bar", () => {
    const html = renderToStaticMarkup(
      <PlaceSearch
        demos={[{ scan_id: 17, name: "Seattle (Capitol Hill)", bbox: [-122.323, 47.613, -122.317, 47.6175] }]}
        activeScanId={17}
        onOpenDemo={() => undefined}
        onPickPlace={() => undefined}
      />,
    );
    expect(html).toContain('placeholder="Search street, neighborhood, or city…"');
    expect(html).toContain('role="search"');
  });

  it("parses Nominatim geocoder results into surveyable bounding boxes", () => {
    const parsed = parseNominatimResults([
      {
        place_id: 101,
        display_name: "Pike Place Market, First Avenue, Belltown, Seattle, King County, Washington, USA",
        lat: "47.6097",
        lon: "-122.3422",
      },
      { invalid: true },
    ]);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].title).toBe("Pike Place Market, First Avenue");
    expect(parsed[0].subtitle).toContain("Belltown");
    expect(parsed[0].bbox[0]).toBeLessThan(-122.3422);
    expect(parsed[0].bbox[2]).toBeGreaterThan(-122.3422);
    expect(PRESET_PLACES.length).toBeGreaterThan(0);
  });
});
