import { useEffect, useReducer, useRef, useState } from "react";
import { bboxAroundPoint } from "./bbox";
import type { Bbox, DemoArea } from "./types";

export interface PlaceOption {
  id: string;
  title: string;
  subtitle: string;
  bbox: Bbox;
}

// Curated street-photo neighborhoods so clicking the omnibox offers instant street jumps even before typing.
export const PRESET_PLACES: PlaceOption[] = [
  {
    id: "preset-ballard",
    title: "NW Market St & 14th Ave NW",
    subtitle: "Ballard, Seattle · 2026 street photos",
    bbox: bboxAroundPoint(-122.3725, 47.6687),
  },
  {
    id: "preset-pike",
    title: "E Pike St & Broadway",
    subtitle: "Capitol Hill, Seattle · dense Mapillary coverage",
    bbox: bboxAroundPoint(-122.3202, 47.6141),
  },
  {
    id: "preset-pioneer",
    title: "Pioneer Square & 1st Ave S",
    subtitle: "Downtown Seattle · historic brick sidewalks",
    bbox: bboxAroundPoint(-122.3331, 47.6015),
  },
  {
    id: "preset-udistrict",
    title: "University Way NE (The Ave)",
    subtitle: "University District, Seattle · retail corridor",
    bbox: bboxAroundPoint(-122.3132, 47.6612),
  },
];

interface NominatimItem {
  place_id?: number | string;
  display_name?: string;
  lat?: string;
  lon?: string;
}

export function parseNominatimResults(raw: unknown): PlaceOption[] {
  if (!Array.isArray(raw)) return [];
  const out: PlaceOption[] = [];
  for (const item of raw as NominatimItem[]) {
    const lat = Number(item?.lat);
    const lng = Number(item?.lon);
    const full = typeof item?.display_name === "string" ? item.display_name.trim() : "";
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || !full) continue;
    const parts = full.split(",").map((s) => s.trim()).filter(Boolean);
    const title = parts.slice(0, 2).join(", ") || full;
    const subtitle = parts.slice(2, 5).join(", ") || "OpenStreetMap location";
    out.push({
      id: `nom-${item.place_id ?? `${lng},${lat}`}`,
      title,
      subtitle,
      bbox: bboxAroundPoint(lng, lat),
    });
  }
  return out;
}

// Nominatim's usage policy forbids autocomplete: it is asked once per search, when the user
// presses Enter, never while typing. This is the only place the component calls the network.
// It rejects when the request fails or Nominatim answers with an error status.
export async function searchPlaces(trimmed: string): Promise<PlaceOption[]> {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=${encodeURIComponent(trimmed)}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`Nominatim answered ${res.status}`);
  return parseNominatimResults(await res.json());
}

// Preset places filter locally on every keystroke, with no network.
export function filterPresets(query: string): PlaceOption[] {
  const q = query.trim().toLowerCase();
  return q
    ? PRESET_PLACES.filter((p) => `${p.title} ${p.subtitle}`.toLowerCase().includes(q))
    : PRESET_PLACES;
}

export type EnterAction =
  | { kind: "choose"; place: PlaceOption }
  | { kind: "search"; text: string }
  | { kind: "none" };

// What Enter does: open a preset typed in full, else open the first result already found for
// this text, else ask the geocoder (only for text long enough to be a place name).
export function decideEnter(query: string, results: PlaceOption[], presets: PlaceOption[]): EnterAction {
  const trimmed = query.trim();
  const preset = presets.find((p) => p.title.toLowerCase() === trimmed.toLowerCase());
  if (preset) return { kind: "choose", place: preset };
  if (results.length > 0) return { kind: "choose", place: results[0] };
  if (trimmed.length >= 2) return { kind: "search", text: trimmed };
  return { kind: "none" };
}

export interface SearchState {
  results: PlaceOption[];
  loading: boolean;
  noPlace: boolean;
  open: boolean;
  // Numbers the latest search; bumped by typing and picking too, so an older answer is dropped.
  seq: number;
}

export type SearchAction =
  | { type: "typed" } // the text changed or was cleared
  | { type: "started" } // Enter asked the geocoder
  | { type: "answered"; seq: number; found: PlaceOption[] }
  | { type: "failed"; seq: number }
  | { type: "picked" } // a place was chosen
  | { type: "opened" }
  | { type: "closed" };

export const initialSearchState: SearchState = {
  results: [],
  loading: false,
  noPlace: false,
  open: false,
  seq: 0,
};

// One search at a time: Nominatim allows about one request per second.
export function canStartSearch(state: SearchState): boolean {
  return !state.loading;
}

export function searchReducer(state: SearchState, action: SearchAction): SearchState {
  switch (action.type) {
    case "typed":
      return { ...state, results: [], loading: false, noPlace: false, open: true, seq: state.seq + 1 };
    case "picked":
      return { ...state, results: [], loading: false, noPlace: false, open: false, seq: state.seq + 1 };
    case "started":
      if (!canStartSearch(state)) return state;
      return { ...state, loading: true, noPlace: false, seq: state.seq + 1 };
    case "answered":
      if (action.seq !== state.seq) return state;
      return {
        ...state,
        results: action.found,
        loading: false,
        noPlace: action.found.length === 0,
        open: true,
      };
    case "failed":
      if (action.seq !== state.seq) return state;
      return { ...state, results: [], loading: false, noPlace: true, open: true };
    case "opened":
      return { ...state, open: true };
    case "closed":
      return { ...state, open: false };
  }
}

interface Props {
  demos: DemoArea[];
  activeScanId: number | null;
  onOpenDemo: (scanId: number) => void;
  onPickPlace: (bbox: Bbox, label: string) => void;
}

export default function PlaceSearch({ demos, activeScanId, onOpenDemo, onPickPlace }: Props) {
  const [query, setQuery] = useState("");
  const [state, dispatch] = useReducer(searchReducer, initialSearchState);
  const { results, loading, noPlace, open } = state;
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        dispatch({ type: "closed" });
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const filteredPresets = filterPresets(query);

  const shownPlaces = results.length > 0 ? results : filteredPresets;

  function changeQuery(text: string) {
    setQuery(text);
    dispatch({ type: "typed" });
  }

  function choosePlace(p: PlaceOption) {
    setQuery(p.title);
    dispatch({ type: "picked" });
    onPickPlace(p.bbox, p.title);
  }

  async function search(trimmed: string) {
    const seq = state.seq + 1;
    dispatch({ type: "started" });
    try {
      dispatch({ type: "answered", seq, found: await searchPlaces(trimmed) });
    } catch {
      dispatch({ type: "failed", seq });
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const action = decideEnter(query, results, filteredPresets);
    if (action.kind === "choose") choosePlace(action.place);
    else if (action.kind === "search" && canStartSearch(state)) void search(action.text);
  }

  return (
    <div className="place-omnibox" ref={wrapRef}>
      <form className="place-search-form" onSubmit={onSubmit} role="search" aria-label="Search a place to survey">
        <span className="place-search-icon" aria-hidden="true">🔍</span>
        <input
          type="search"
          className="place-search-input"
          placeholder="Search a street or city, then press Enter"
          aria-label="Search street, neighborhood, or city"
          value={query}
          onFocus={() => dispatch({ type: "opened" })}
          onChange={(e) => changeQuery(e.target.value)}
        />
        {query && (
          <button
            type="button"
            className="place-clear-btn"
            aria-label="Clear search"
            onClick={() => changeQuery("")}
          >
            ×
          </button>
        )}
      </form>
      {open && (
        <div className="place-dropdown" role="listbox" aria-label="Place suggestions">
          <div className="place-group-label">
            {loading
              ? "Searching OpenStreetMap…"
              : results.length > 0
                ? "OpenStreetMap places (click to jump & select area)"
                : "Streets with Mapillary photos (click to select area)"}
          </div>
          {noPlace && (
            <p className="hint small" role="status">No place found. Try a street or neighborhood.</p>
          )}
          <ul className="place-list">
            {shownPlaces.map((p) => (
              <li key={p.id}>
                <button type="button" className="place-option" onClick={() => choosePlace(p)}>
                  <span className="place-option-main">
                    <strong>📍 {p.title}</strong>
                    <small>{p.subtitle}</small>
                  </span>
                  <span className="place-option-cta">Select area →</span>
                </button>
              </li>
            ))}
          </ul>
          {demos.length > 0 && (
            <>
              <div className="place-group-label place-group-divider">Completed surveys (instant results)</div>
              <ul className="place-list">
                {demos.map((d) => (
                  <li key={d.scan_id}>
                    <button
                      type="button"
                      className={`place-option${d.scan_id === activeScanId ? " place-option-active" : ""}`}
                      onClick={() => {
                        dispatch({ type: "closed" });
                        onOpenDemo(d.scan_id);
                      }}
                    >
                      <span className="place-option-main">
                        <strong>✓ {d.name}</strong>
                        <small>Completed survey · view findings on map</small>
                      </span>
                      <span className="place-option-cta">Open results</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
