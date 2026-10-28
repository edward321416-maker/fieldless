import { describe, expect, it } from "vitest";
import { PALETTE, slotOf } from "./items";
import { RING_SLOTS, ringCounts, ringName, ringSegments } from "./ring";
import type { ItemMeta } from "./types";

const item = (id: string): ItemMeta => ({ id, target: id, map_icon: "", kind: "check", inventory: true });

describe("cluster rings", () => {
  it("gives each item its palette slot, the same order colourOf uses", () => {
    const items = [item("a"), item("b"), item("c")];
    expect(slotOf("b", items)).toBe(1);
    expect(slotOf("zz", items)).toBe(3); // unknown: the next slot, as colourOf does
  });
  it("names a ring by its slot counts and reads the name back", () => {
    const counts = [2, 0, 1, 0, 0, 0, 1, 0];
    expect(ringName(counts)).toBe("ring:2,0,1,0,0,0,1,0");
    expect(ringCounts("ring:2,0,1,0,0,0,1,0")).toEqual(counts);
    expect(ringCounts("m:pole:none")).toBeNull();
    expect(ringCounts("ring:1,2")).toBeNull(); // not RING_SLOTS numbers
    expect(RING_SLOTS).toBe(8);
  });
  it("splits the ring in proportion, clockwise from the top, skipping empty slots", () => {
    const segs = ringSegments([2, 0, 1, 0, 0, 0, 1, 0]);
    expect(segs.map((s) => s.colour)).toEqual([PALETTE[0], PALETTE[2], PALETTE[6]]);
    expect(segs[0].start).toBeCloseTo(-Math.PI / 2);
    expect(segs[0].end - segs[0].start).toBeCloseTo(Math.PI); // 2 of 4
    expect(segs[2].end).toBeCloseTo(-Math.PI / 2 + 2 * Math.PI);
    expect(ringSegments([0, 0, 0, 0, 0, 0, 0, 0])).toEqual([]);
  });
});
