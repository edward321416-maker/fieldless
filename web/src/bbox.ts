import type { Feature, Polygon } from "geojson";
import type { Bbox, Walk } from "./types";

export function bboxFromCorners(a: [number, number], b: [number, number]): Bbox {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

export function viewToBbox(west: number, south: number, east: number, north: number): Bbox {
  return [west, south, east, north];
}

// A street-level survey box (~500 m across, 0.000027 deg² <= max_bbox_deg2 0.0001) centred on a searched place.
export function bboxAroundPoint(lng: number, lat: number, halfLng = 0.003, halfLat = 0.00225): Bbox {
  return [lng - halfLng, lat - halfLat, lng + halfLng, lat + halfLat];
}

// When the map view is wider than maxDeg2, fit a surveyable box in the centre of the view so "Survey this view"
// works at once without forcing the user to zoom in by trial and error.
export function clampBboxToMax(b: Bbox, maxDeg2: number): Bbox {
  const area = bboxArea(b);
  if (area <= maxDeg2 && !bboxTooSmall(b)) return b;
  const cx = (b[0] + b[2]) / 2;
  const cy = (b[1] + b[3]) / 2;
  if (bboxTooSmall(b)) return bboxAroundPoint(cx, cy);
  const scale = Math.sqrt((maxDeg2 * 0.85) / area);
  const hw = ((b[2] - b[0]) * scale) / 2;
  const hh = ((b[3] - b[1]) * scale) / 2;
  return [cx - hw, cy - hh, cx + hw, cy + hh];
}

// The streets a survey photographed: what the map shows first, rather than the drawn box with its empty corners.
export function contentBounds(walks: Walk[]): Bbox | null {
  const pts = walks.flatMap((w) => w.points);
  if (pts.length === 0) return null;
  const lngs = pts.map((p) => p[0]);
  const lats = pts.map((p) => p[1]);
  return [Math.min(...lngs), Math.min(...lats), Math.max(...lngs), Math.max(...lats)];
}

export function bboxArea(b: Bbox): number {
  return (b[2] - b[0]) * (b[3] - b[1]);
}

export function bboxTooLarge(b: Bbox, maxDeg2: number): boolean {
  return bboxArea(b) > maxDeg2;
}

// Narrower than about 1 m on a side: a click, not a drag.
export function bboxTooSmall(b: Bbox): boolean {
  return b[2] - b[0] < 1e-5 || b[3] - b[1] < 1e-5;
}

export function bboxPolygon(b: Bbox): Feature<Polygon> {
  const [w, s, e, n] = b;
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
  };
}
