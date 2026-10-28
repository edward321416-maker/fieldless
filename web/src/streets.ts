import { distanceToLineM, type LngLat } from "./geo";

// Street names for a finding's place, from the base map's own road-name layer (docs/specs ui-redesign 4): the road
// the photo was taken on, and the nearest other road, e.g. "11th Ave, near E Pike St". No name, no guess.
export interface NamedRoad {
  name: string;
  lines: LngLat[][];
}

const ON_ROAD_M = 40; // a photo further than this from every named road is placed by coordinates only
const CROSS_M = 80; // the "near" road

export function streetText(p: LngLat, roads: NamedRoad[]): string | null {
  const scored = roads
    .map((r) => ({ name: r.name, m: Math.min(...r.lines.map((l) => distanceToLineM(p, l))) }))
    .filter((r) => Number.isFinite(r.m))
    .sort((a, b) => a.m - b.m);
  const on = scored[0];
  if (!on || on.m > ON_ROAD_M) return null;
  const near = scored.find((r) => r.name !== on.name && r.m <= CROSS_M);
  return near ? `${on.name}, near ${near.name}` : on.name;
}

// Road features (GeoJSON LineString or MultiLineString with a name) merged by name.
export function namedRoads(features: { properties?: Record<string, unknown> | null; geometry: GeoJSON.Geometry }[]): NamedRoad[] {
  const byName = new Map<string, LngLat[][]>();
  for (const f of features) {
    const name = (f.properties?.["name:en"] || f.properties?.name) as string | undefined;
    if (!name) continue;
    const lines = f.geometry.type === "LineString" ? [f.geometry.coordinates as LngLat[]]
      : f.geometry.type === "MultiLineString" ? (f.geometry.coordinates as LngLat[][]) : [];
    if (lines.length) byName.set(name, [...(byName.get(name) ?? []), ...lines]);
  }
  return [...byName].map(([name, lines]) => ({ name, lines }));
}
