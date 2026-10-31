import { useEffect, useRef, useState } from "react";
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
  {
    id: "preset-seongsu",
    title: "Yeonmujang-gil, Seongsu-dong",
    subtitle: "Seongdong-gu, Seoul · shared street",
    bbox: [127.055161, 37.546707, 127.058991, 37.550062],
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

interface Props {
  demos: DemoArea[];
  activeScanId: number | null;
  onOpenDemo: (scanId: number) => void;
  onPickPlace: (bbox: Bbox, label: string) => void;
}

export default function PlaceSearch({ demos, activeScanId, onOpenDemo, onPickPlace }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<PlaceOption[]>([]);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=${encodeURIComponent(trimmed)}`;
        const res = await fetch(url, { headers: { Accept: "application/json" } });
        if (!res.ok || cancelled) return;
        const data: unknown = await res.json();
        if (!cancelled) setResults(parseNominatimResults(data));
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 280);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  const filteredPresets = query.trim()
    ? PRESET_PLACES.filter((p) =>
        `${p.title} ${p.subtitle}`.toLowerCase().includes(query.trim().toLowerCase()))
    : PRESET_PLACES;

  const shownPlaces = results.length > 0 ? results : filteredPresets;

  function choosePlace(p: PlaceOption) {
    setQuery(p.title);
    setOpen(false);
    onPickPlace(p.bbox, p.title);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (shownPlaces.length > 0) {
      choosePlace(shownPlaces[0]);
    }
  }

  return (
    <div className="place-omnibox" ref={wrapRef}>
      <form className="place-search-form" onSubmit={onSubmit} role="search" aria-label="Search a place to survey">
        <span className="place-search-icon" aria-hidden="true">🔍</span>
        <input
          type="search"
          className="place-search-input"
          placeholder="Search street, neighborhood, or city…"
          aria-label="Search street, neighborhood, or city"
          value={query}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
        />
        {query && (
          <button
            type="button"
            className="place-clear-btn"
            aria-label="Clear search"
            onClick={() => {
              setQuery("");
              setResults([]);
              setOpen(true);
            }}
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
                        setOpen(false);
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
