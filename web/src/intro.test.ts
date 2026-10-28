import { describe, expect, it } from "vitest";
import { startsWithIntro } from "./intro";

describe("the first screen", () => {
  it("opens on the introduction unless the address names a survey", () => {
    expect(startsWithIntro("")).toBe(true);
    expect(startsWithIntro("#")).toBe(true);
    expect(startsWithIntro("#scan=4")).toBe(false);
    expect(startsWithIntro("#scan=12&x=1")).toBe(false);
  });
});
