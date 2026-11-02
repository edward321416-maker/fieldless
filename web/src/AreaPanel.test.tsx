import { describe, expect, it } from "vitest";
import { countKey } from "./AreaPanel";
import type { Bbox, Scan } from "./types";

const box: Bbox = [-122.3755, 47.6655, -122.3695, 47.6695];

describe("when the new-survey panel counts the photos", () => {
  it("counts a box once: the same box in a fresh array is the same count", () => {
    expect(countKey(box, null)).not.toBeNull();
    expect(countKey([...box] as Bbox, null)).toBe(countKey(box, null));
    expect(countKey([box[0], box[1], box[2], 47.67] as Bbox, null)).not.toBe(countKey(box, null));
  });
  it("counts nothing without a box", () => {
    expect(countKey(null, null)).toBeNull();
  });
  it("counts nothing once a survey is under way or done (the app re-reads the survey's box every second)", () => {
    const scan = { id: 32, status: "running", bbox: [...box] } as unknown as Scan;
    expect(countKey(box, scan)).toBeNull();
    expect(countKey(scan.bbox, { ...scan, status: "done" } as Scan)).toBeNull();
  });
});
