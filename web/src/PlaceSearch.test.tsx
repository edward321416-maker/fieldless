import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import PlaceSearch, {
  canStartSearch,
  decideEnter,
  filterPresets,
  initialSearchState,
  parseNominatimResults,
  PRESET_PLACES,
  searchPlaces,
  searchReducer,
  type PlaceOption,
  type SearchAction,
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
// presses Enter. Vitest runs in the node environment here (no DOM, no testing-library), so what
// Enter does is proven on the pure pieces the component is built from.
describe("PlaceSearch asks Nominatim on Enter", () => {
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

  it("rejects when the geocoder fails or answers with an error", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    fetcher.mockResolvedValueOnce(new Response("busy", { status: 429 }));
    await expect(searchPlaces("Ballard")).rejects.toThrow("429");
    fetcher.mockRejectedValueOnce(new Error("offline"));
    await expect(searchPlaces("Ballard")).rejects.toThrow("offline");
  });
});

// Source check. There is no DOM to type into, so this reads the component's source (comments
// stripped) and fails if the geocoder can be reached by anything other than the Enter path:
// searchPlaces( may be called only inside search(), search( only inside onSubmit, fetch( only
// inside searchPlaces, and no useEffect may depend on `query`.
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

// Index of the bracket that closes the one at `open`.
function closeOf(src: string, open: number): number {
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (c === "(" || c === "{" || c === "[") depth++;
    else if (c === ")" || c === "}" || c === "]") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// The `{ ... }` of a function whose header matches (the header must end with its `{`).
function bodyOf(src: string, header: RegExp): [number, number] | null {
  const m = header.exec(src);
  if (!m) return null;
  const open = m.index + m[0].length - 1;
  return [open, closeOf(src, open)];
}

// Where `name(` is called, leaving out its own `function name(` declaration and `.name(`.
function callsOf(src: string, name: string): number[] {
  const out: number[] = [];
  for (const m of src.matchAll(new RegExp(`(?<![.\\w])${name}\\s*\\(`, "g"))) {
    const at = m.index ?? 0;
    if (!src.slice(0, at).trimEnd().endsWith("function")) out.push(at);
  }
  return out;
}

function networkLeaks(raw: string): string[] {
  const src = stripComments(raw);
  const leaks: string[] = [];
  const geocoder = bodyOf(src, /\bfunction searchPlaces\s*\([^)]*\)[^{]*\{/);
  const search = bodyOf(src, /\bfunction search\s*\([^)]*\)\s*\{/);
  const submit = bodyOf(src, /\bfunction onSubmit\s*\([^)]*\)\s*\{/);
  const inside = (range: [number, number] | null, at: number) =>
    range !== null && at > range[0] && at < range[1];

  if (!geocoder) leaks.push("function searchPlaces not found");
  if (!search) leaks.push("function search not found");
  if (!submit) leaks.push("function onSubmit not found");
  if (callsOf(src, "searchPlaces").length === 0) leaks.push("searchPlaces is never called");
  for (const at of callsOf(src, "fetch")) {
    if (!inside(geocoder, at)) leaks.push(`fetch( outside searchPlaces at ${at}`);
  }
  for (const at of callsOf(src, "searchPlaces")) {
    if (!inside(search, at)) leaks.push(`searchPlaces( outside search at ${at}`);
  }
  for (const at of callsOf(src, "search")) {
    if (!inside(submit, at)) leaks.push(`search( outside onSubmit at ${at}`);
  }
  for (const m of src.matchAll(/\buseEffect\s*\(/g)) {
    const open = (m.index ?? 0) + m[0].length - 1;
    const call = src.slice(open, closeOf(src, open) + 1);
    const deps = /,\s*\[([^\]]*)\]\s*\)$/.exec(call);
    if (!deps) leaks.push("useEffect without a dependency list");
    else if (/\bquery\b/.test(deps[1])) leaks.push("useEffect depends on query");
  }
  return leaks;
}

describe("the geocoder is reachable only from Enter", () => {
  it("searchPlaces is called only inside search, search only inside onSubmit, and no useEffect depends on query", () => {
    expect(networkLeaks(source)).toEqual([]);
  });

  // The check above is only worth having if it can fail.
  const CLEAN = `
    export async function searchPlaces(t: string) { const r = await fetch("https://x/?q=" + t); return r.json(); }
    export default function PlaceSearch() {
      useEffect(() => { document.addEventListener("mousedown", f); }, []);
      async function search(t: string) { await searchPlaces(t); }
      function onSubmit(e: Event) { void search(query); }
    }`;
  const withLine = (line: string) => CLEAN.replace(/\}\s*$/, `${line}\n}`);

  it("accepts the Enter-only shape and ignores comments that mention the old one", () => {
    expect(networkLeaks(CLEAN)).toEqual([]);
    expect(
      networkLeaks(withLine("// useEffect(() => { void searchPlaces(query); }, [query]);")),
    ).toEqual([]);
    expect(networkLeaks(withLine("/* void searchPlaces(query); fetch(u) */"))).toEqual([]);
  });

  it("flags a debounced effect that asks the geocoder when the text changes", () => {
    const effect = withLine("useEffect(() => { void searchPlaces(query); }, [query]);");
    expect(networkLeaks(effect)).toEqual([
      expect.stringContaining("searchPlaces( outside search"),
      "useEffect depends on query",
    ]);
  });

  it("flags search() started from an effect, and a second fetch", () => {
    expect(networkLeaks(withLine("useEffect(() => { void search(query); }, [query]);"))).toEqual([
      expect.stringContaining("search( outside onSubmit"),
      "useEffect depends on query",
    ]);
    expect(networkLeaks(withLine("function other() { fetch(u); }"))).toEqual([
      expect.stringContaining("fetch( outside searchPlaces"),
    ]);
  });
});

// The search state machine, moved out of the component so its transitions can be tested.
describe("searchReducer", () => {
  const ballard: PlaceOption = parseNominatimResults([
    { place_id: 1, display_name: "Ballard, Seattle, Washington, USA", lat: "47.668", lon: "-122.384" },
  ])[0];
  const run = (...actions: SearchAction[]) => actions.reduce(searchReducer, initialSearchState);

  it("starts closed and idle", () => {
    expect(initialSearchState).toEqual({ results: [], loading: false, noPlace: false, open: false, seq: 0 });
  });

  it("starting a search sets loading and numbers the search", () => {
    expect(run({ type: "started" })).toMatchObject({ loading: true, noPlace: false, seq: 1 });
  });

  it("shows what an answer found and opens the list", () => {
    expect(run({ type: "started" }, { type: "answered", seq: 1, found: [ballard] })).toEqual({
      results: [ballard],
      loading: false,
      noPlace: false,
      open: true,
      seq: 1,
    });
  });

  it("ignores an answer carrying an old sequence number", () => {
    const typedAfter = run({ type: "started" }, { type: "typed" });
    expect(searchReducer(typedAfter, { type: "answered", seq: 1, found: [ballard] })).toBe(typedAfter);
    expect(searchReducer(typedAfter, { type: "failed", seq: 1 })).toBe(typedAfter);
    expect(typedAfter.results).toEqual([]);
    expect(typedAfter.noPlace).toBe(false);
  });

  it("ignores the answer of a search that was running when a place was picked", () => {
    const picked = run({ type: "started" }, { type: "picked" });
    expect(searchReducer(picked, { type: "answered", seq: 1, found: [ballard] })).toBe(picked);
    expect(picked.open).toBe(false);
  });

  it("sets noPlace when the answer is empty", () => {
    expect(run({ type: "started" }, { type: "answered", seq: 1, found: [] })).toMatchObject({
      results: [],
      loading: false,
      noPlace: true,
      open: true,
    });
  });

  it("sets noPlace when the search failed", () => {
    expect(run({ type: "started" }, { type: "failed", seq: 1 })).toMatchObject({
      results: [],
      loading: false,
      noPlace: true,
      open: true,
    });
  });

  it("typing clears the results and noPlace and opens the list", () => {
    const found = run({ type: "started" }, { type: "answered", seq: 1, found: [ballard] }, { type: "typed" });
    expect(found).toMatchObject({ results: [], noPlace: false, loading: false, open: true, seq: 2 });
    const none = run({ type: "started" }, { type: "answered", seq: 1, found: [] }, { type: "typed" });
    expect(none).toMatchObject({ results: [], noPlace: false, open: true });
  });

  it("refuses to start a search while one is loading", () => {
    const loading = run({ type: "started" });
    expect(canStartSearch(loading)).toBe(false);
    expect(searchReducer(loading, { type: "started" })).toBe(loading);
    expect(loading.seq).toBe(1);
  });

  it("allows a new search once the last one is answered, failed or typed over", () => {
    expect(canStartSearch(initialSearchState)).toBe(true);
    expect(canStartSearch(run({ type: "started" }, { type: "answered", seq: 1, found: [] }))).toBe(true);
    expect(canStartSearch(run({ type: "started" }, { type: "failed", seq: 1 }))).toBe(true);
    expect(canStartSearch(run({ type: "started" }, { type: "typed" }))).toBe(true);
  });

  it("starting again clears an earlier noPlace", () => {
    const again = run({ type: "started" }, { type: "failed", seq: 1 }, { type: "started" });
    expect(again).toMatchObject({ loading: true, noPlace: false, seq: 2 });
  });

  it("picking a place clears the results and closes the list", () => {
    const picked = run({ type: "started" }, { type: "answered", seq: 1, found: [ballard] }, { type: "picked" });
    expect(picked).toMatchObject({ results: [], noPlace: false, loading: false, open: false });
  });

  it("opens and closes the list without touching the search", () => {
    const found = run({ type: "started" }, { type: "answered", seq: 1, found: [ballard] });
    expect(searchReducer(found, { type: "closed" })).toEqual({ ...found, open: false });
    expect(searchReducer({ ...found, open: false }, { type: "opened" })).toEqual(found);
  });
});
