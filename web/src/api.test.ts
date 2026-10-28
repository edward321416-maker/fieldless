import { describe, expect, it } from "vitest";
import { reportUrl, staticPath } from "./api";

describe("static demo", () => {
  it("reads each answer from a .json file next to the API path", () => {
    expect(staticPath("/api/scans/3/issues")).toBe("/api/scans/3/issues.json");
    expect(staticPath("/api/config")).toBe("/api/config.json");
  });
  it("opens a survey's report from the server, or from its file on the static demo", () => {
    expect(reportUrl(7, false)).toBe("/api/scans/7/report");
    expect(reportUrl(7, true)).toBe("/api/scans/7/report.html");
  });
});
