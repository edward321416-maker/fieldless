import { describe, expect, it } from "vitest";
import { staticPath } from "./api";

describe("static demo", () => {
  it("reads each answer from a .json file next to the API path", () => {
    expect(staticPath("/api/scans/3/issues")).toBe("/api/scans/3/issues.json");
    expect(staticPath("/api/config")).toBe("/api/config.json");
  });
});
