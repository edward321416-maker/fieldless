// Where a photo looks and where its object is, for the map (docs/specs/2026-10-28-ui-redesign-design.md 3.3).

export const FOV_DEG = 70; // degrees across a photo, as eval/city.py FOV
const EARTH_M = 6_371_000;

export type LngLat = [number, number];

const norm = (deg: number) => ((deg % 360) + 360) % 360;
const rad = (deg: number) => (deg * Math.PI) / 180;

// The bearing from the camera to the object: the photo's compass plus how far right of centre the model's box is
// (eval/city.py row_direction, the same sum). Null without a compass or a box.
export function targetBearing(heading: number | null, box: number[] | null, fov = FOV_DEG): number | null {
  if (heading === null || !box || box.length !== 4) return null;
  return norm(heading + ((box[0] + box[2]) / 2000 - 0.5) * fov);
}

// The point `meters` from (lat, lng) along `bearing`.
export function destination(lat: number, lng: number, bearing: number, meters: number): LngLat {
  const d = meters / EARTH_M;
  const b = rad(bearing);
  const p1 = rad(lat);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = rad(lng) + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [((l2 * 180) / Math.PI + 540) % 360 - 180, (p2 * 180) / Math.PI];
}

// What the photo shows: a wedge of `fov` degrees around its heading, `meters` deep, as a closed ring.
export function viewWedge(lat: number, lng: number, heading: number, meters = 25, fov = FOV_DEG): LngLat[] {
  const ring: LngLat[] = [[lng, lat]];
  const steps = 12;
  for (let i = 0; i <= steps; i++) ring.push(destination(lat, lng, heading - fov / 2 + (fov * i) / steps, meters));
  ring.push([lng, lat]);
  return ring;
}

export function distanceM(a: LngLat, b: LngLat): number {
  const dLat = rad(b[1] - a[1]);
  const dLng = rad(b[0] - a[0]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_M * Math.asin(Math.sqrt(h));
}

// The shortest distance in metres from a point to a polyline (flat-earth within a street's few hundred metres).
export function distanceToLineM(p: LngLat, line: LngLat[]): number {
  const kx = Math.cos(rad(p[1])) * 111_320;
  const ky = 110_574;
  const xy = (q: LngLat) => [(q[0] - p[0]) * kx, (q[1] - p[1]) * ky];
  let best = Infinity;
  for (let i = 0; i < line.length; i++) {
    const [ax, ay] = xy(line[i]);
    if (i === line.length - 1) {
      best = Math.min(best, Math.hypot(ax, ay));
      break;
    }
    const [bx, by] = xy(line[i + 1]);
    const dx = bx - ax;
    const dy = by - ay;
    const len = dx * dx + dy * dy;
    const t = len === 0 ? 0 : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len));
    best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
  }
  return best;
}

const COMPASS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"];

export function compassWord(bearing: number): string {
  return COMPASS[Math.round(norm(bearing) / 45) % 8];
}
