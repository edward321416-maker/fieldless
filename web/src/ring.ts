import { PALETTE } from "./items";

// A cluster's ring: its border split by the items inside, in proportion (docs/specs/2026-10-28-design-pass2-design.md
// 3). The map names each ring image by its slot counts, so one drawing serves every cluster with the same mix.
export const RING_SLOTS = PALETTE.length;

export const ringName = (counts: number[]) => `ring:${counts.join(",")}`;

export function ringCounts(name: string): number[] | null {
  if (!name.startsWith("ring:")) return null;
  const counts = name.slice(5).split(",").map(Number);
  return counts.length === RING_SLOTS && counts.every((n) => Number.isInteger(n) && n >= 0) ? counts : null;
}

// Arcs in radians, clockwise from the top (canvas angles start at 3 o'clock, so the top is -pi/2).
export function ringSegments(counts: number[]): { colour: string; start: number; end: number }[] {
  const total = counts.reduce((a, b) => a + b, 0);
  if (total === 0) return [];
  const out: { colour: string; start: number; end: number }[] = [];
  let at = -Math.PI / 2;
  counts.forEach((n, slot) => {
    if (n === 0) return;
    const end = at + (2 * Math.PI * n) / total;
    out.push({ colour: PALETTE[slot], start: at, end });
    at = end;
  });
  return out;
}
