import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import PlaceSearch, {
  decideEnter,
  filterPresets,
  parseNominatimResults,
  PRESET_PLACES,
  searchPlaces,
} from "./PlaceSearch";
import source from "./PlaceSearch.tsx?raw";

afterEach(() => {
  vi.restoreAllMocks();
});

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
    expect(html).toContain('placeholder="Search a street or city, then press Enter"');
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

// Nominatim's usage policy forbids autocomplete, so the geocoder is asked only when the user
// presses Enter. Vitest runs in the node environment here (no DOM, no testing-library), so the
// behaviour is proven on the pure pieces the component is built from instead of by typing into
// a rendered input.
describe("PlaceSearch asks Nominatim on Enter only", () => {
  it("does not call the geocoder while typing", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    // What the component computes on every keystroke: local preset filtering, nothing else.
    for (const typed of ["B", "Ba", "Bal", "Ball", "Balla", "Ballar", "Ballard"]) {
      filterPresets(typed);
    }
    await new Promise((r) => setTimeout(r, 350));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps the old debounce out of the component, so no timer can ask the geocoder", () => {
    // Stand-in for typing into a mounted input: the component source must hold exactly one
    // fetch, inside searchPlaces, and no timer that could fire it after a keystroke.
    expect(source.match(/\bfetch\(/g)).toHaveLength(1);
    expect(source).not.toContain("setTimeout");
  });

  it("calls the geocoder once on Enter", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[]"));
    const action = decideEnter("Ballard", [], filterPresets("Ballard"));
    expect(action).toEqual({ kind: "search", text: "Ballard" });
    if (action.kind === "search") await searchPlaces(action.text);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String(fetcher.mock.calls[0][0])).toContain("q=Ballard");
  });

  it("filters the preset places locally while typing, with no network", () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    expect(filterPresets("pike").map((p) => p.id)).toEqual(["preset-pike"]);
    expect(filterPresets("  ")).toEqual(PRESET_PLACES);
    expect(filterPresets("zzzz-no-such-street")).toEqual([]);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("opens a preset on Enter without asking the geocoder", () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    const pike = PRESET_PLACES[0];
    expect(decideEnter(` ${pike.title.toUpperCase()} `, [], filterPresets(pike.title))).toEqual({
      kind: "choose",
      place: pike,
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("picks the first result on a second Enter instead of asking again", () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    const found = parseNominatimResults([
      { place_id: 1, display_name: "Ballard, Seattle, Washington, USA", lat: "47.668", lon: "-122.384" },
    ]);
    expect(decideEnter("Ballard", found, filterPresets("Ballard"))).toEqual({
      kind: "choose",
      place: found[0],
    });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("ignores Enter on text too short to search", () => {
    expect(decideEnter("", [], PRESET_PLACES)).toEqual({ kind: "none" });
    expect(decideEnter(" B ", [], filterPresets("B"))).toEqual({ kind: "none" });
  });

  it("returns parsed places from the geocoder response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          { place_id: 7, display_name: "Ballard, Seattle, King County, Washington, USA", lat: "47.668", lon: "-122.384" },
        ]),
      ),
    );
    const found = await searchPlaces("Ballard");
    expect(found.map((p) => p.id)).toEqual(["nom-7"]);
  });

  it("returns no places when the geocoder fails or answers with an error", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    fetcher.mockResolvedValueOnce(new Response("busy", { status: 429 }));
    expect(await searchPlaces("Ballard")).toEqual([]);
    fetcher.mockRejectedValueOnce(new Error("offline"));
    expect(await searchPlaces("Ballard")).toEqual([]);
  });
});
