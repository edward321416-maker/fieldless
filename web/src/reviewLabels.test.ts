import { describe, expect, it } from "vitest";
import { actionLabel } from "./reviewLabels";

describe("action display names", () => {
  it("does not suggest writing an inspection date from a photo", () => {
    expect(actionLabel("Update the date")).toBe("Review newer photo evidence");
    expect(actionLabel("Correct the record")).toBe("Correct the record");
    expect(actionLabel("Visit on site")).toBe("Visit on site");
  });
});
