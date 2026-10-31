import { describe, expect, it } from "vitest";
import { bboxArea, bboxAroundPoint, bboxFromCorners, bboxPolygon, bboxTooLarge, bboxTooSmall, clampBboxToMax, contentBounds, viewToBbox } from "./bbox";

describe("bbox", () => {
  it("bounds the photographed walks, or nothing when there are none", () => {
    expect(contentBounds([{ clip: 0, points: [[-122.32, 47.613], [-122.318, 47.615]] },
      { clip: 1, points: [[-122.321, 47.614]] }])).toEqual([-122.321, 47.613, -122.318, 47.615]);
    expect(contentBounds([])).toBeNull();
  });
  it("orders corners whatever the drag direction", () => {
    expect(bboxFromCorners([126.99, 37.56], [126.98, 37.57])).toEqual([126.98, 37.56, 126.99, 37.57]);
  });
  it("measures and limits the area", () => {
    const b = bboxFromCorners([126.98, 37.56], [126.99, 37.57]);
    expect(bboxArea(b)).toBeCloseTo(0.0001, 8);
    expect(bboxTooLarge(b, 0.0001)).toBe(false);
    expect(bboxTooLarge([126.9, 37.5, 127.0, 37.6], 0.0001)).toBe(true);
    expect(bboxTooSmall([126.98, 37.56, 126.980001, 37.57])).toBe(true);
    expect(bboxTooSmall(b)).toBe(false);
  });
  it("creates a surveyable bbox around a searched point and clamps oversized views", () => {
    const around = bboxAroundPoint(-122.32, 47.614);
    expect(bboxTooLarge(around, 0.0001)).toBe(false);
    expect(bboxTooSmall(around)).toBe(false);
    const wide = clampBboxToMax([-122.4, 47.5, -122.2, 47.7], 0.0001);
    expect(bboxTooLarge(wide, 0.0001)).toBe(false);
    expect((wide[0] + wide[2]) / 2).toBeCloseTo(-122.3, 5);
  });
  it("draws a closed polygon", () => {
    const ring = bboxPolygon([1, 2, 3, 4]).geometry.coordinates[0];
    expect(ring).toEqual([[1, 2], [3, 2], [3, 4], [1, 4], [1, 2]]);
  });
  it("turns a map view into a bbox", () => {
    expect(viewToBbox(1, 2, 3, 4)).toEqual([1, 2, 3, 4]);
  });
});
