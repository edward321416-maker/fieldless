import { describe, expect, it } from "vitest";
import { compassWord, destination, distanceM, distanceToLineM, targetBearing, viewWedge } from "./geo";
import { fallbackLetter, hasIcon } from "./icons";
import { badgeOf, colourOf, inkOn, isLight, PALETTE } from "./items";
import { namedRoads, streetText } from "./streets";
import type { ItemMeta } from "./types";

const item = (id: string): ItemMeta => ({ id, target: id, map_icon: "", kind: "check", inventory: true });

describe("where a photo looks", () => {
  it("turns the box centre into a bearing the way eval/city.py row_direction does", () => {
    // row_direction: (compass + ((x1 + x2) / 2000 - 0.5) * 70) % 360
    expect(targetBearing(90, [500, 0, 500, 1000])).toBe(90);
    expect(targetBearing(90, [900, 0, 1000, 1000])).toBeCloseTo(90 + 0.45 * 70);
    expect(targetBearing(10, [0, 0, 100, 1000])).toBeCloseTo(((10 - 0.45 * 70) % 360 + 360) % 360);
    expect(targetBearing(null, [0, 0, 1, 1])).toBeNull();
    expect(targetBearing(90, null)).toBeNull();
    expect(targetBearing(90, [])).toBeNull();
  });

  it("walks a distance along a bearing and draws the photo's view", () => {
    const [lng, lat] = destination(47.6, -122.3, 0, 10);
    expect(lng).toBeCloseTo(-122.3, 6);
    expect(distanceM([-122.3, 47.6], [lng, lat])).toBeCloseTo(10, 1);
    const wedge = viewWedge(47.6, -122.3, 90, 25);
    expect(wedge[0]).toEqual([-122.3, 47.6]);
    expect(wedge[wedge.length - 1]).toEqual([-122.3, 47.6]);
    expect(distanceM([-122.3, 47.6], wedge[1])).toBeCloseTo(25, 0);
    expect(compassWord(2)).toBe("north");
    expect(compassWord(271)).toBe("west");
  });

  it("measures the distance to a polyline", () => {
    const line: [number, number][] = [[-122.3, 47.6], [-122.3, 47.601]];
    expect(distanceToLineM([-122.3001, 47.6005], line)).toBeCloseTo(7.5, 0);
    expect(distanceToLineM([-122.3, 47.602], line)).toBeCloseTo(111, -1);
  });
});

describe("what a marker says", () => {
  it("colours items by their order, the same order the same colours", () => {
    const items = [item("a"), item("b"), item("c")];
    expect(colourOf("a", items)).toBe(PALETTE[0]);
    expect(colourOf("c", items)).toBe(PALETTE[2]);
    expect(colourOf("c", items)).toBe(colourOf("c", [item("a"), item("b"), item("c")]));
    expect(PALETTE).not.toContain("#B91C1C");
    expect(isLight("#E6C800")).toBe(true);
    expect(isLight("#0072B2")).toBe(false);
  });

  it("puts at most one badge: a site visit, else a possible defect, else a check to do", () => {
    expect(badgeOf("field_check", true)).toBe("site");
    expect(badgeOf("needs_review", true)).toBe("defect");
    expect(badgeOf("needs_review", false)).toBe("review");
    expect(badgeOf("auto", false)).toBeNull();
    expect(badgeOf("verified", false)).toBeNull();
  });

  it("falls back to the first letter when the icon set has no such shape", () => {
    expect(hasIcon("tree")).toBe(true);
    expect(hasIcon("fire-hydrant")).toBe(false);
    expect(fallbackLetter("fire hydrant")).toBe("F");
    expect(fallbackLetter("  ")).toBe("?");
  });
});

describe("street names", () => {
  const roads = namedRoads([
    { properties: { name: "11th Ave" }, geometry: { type: "LineString", coordinates: [[-122.32, 47.61], [-122.32, 47.62]] } },
    { properties: { name: "E Pike St" }, geometry: { type: "MultiLineString", coordinates: [[[-122.33, 47.6143], [-122.31, 47.6143]]] } },
    { properties: {}, geometry: { type: "LineString", coordinates: [[-122.32, 47.61], [-122.31, 47.61]] } },
  ]);

  it("names the road a photo was taken on and the nearest other road", () => {
    expect(streetText([-122.3201, 47.6140], roads)).toBe("11th Ave, near E Pike St");
    expect(streetText([-122.3201, 47.618], roads)).toBe("11th Ave");
    expect(streetText([-122.25, 47.618], roads)).toBeNull();
  });
});

describe("text on an item's colour", () => {
  it("takes dark or white, whichever reads at 4.5:1 or better", () => {
    expect(inkOn("#009E73")).toBe("#111827"); // white on this green is 3.4:1
    expect(inkOn("#0072B2")).toBe("#ffffff");
    expect(inkOn("#E6C800")).toBe("#111827");
    expect(inkOn("#1B7837")).toBe("#ffffff");
  });
});
