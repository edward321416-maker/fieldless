import type { Feature, Polygon } from "geojson";
import type { Bbox } from "./types";

export function bboxFromCorners(a: [number, number], b: [number, number]): Bbox {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

export function viewToBbox(west: number, south: number, east: number, north: number): Bbox {
  return [west, south, east, north];
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
