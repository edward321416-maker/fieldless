import { describe, expect, it } from "vitest";
import {
  dollars, isFinished, issueQuestion, km, labelText, median, minutes, photoMonth, reasonText, stageState, yearsSpan,
} from "./format";
import type { Scan } from "./types";

const scan = (stage_status: Record<string, string>, status = "running"): Scan => ({
  id: 1, status, stage_status, bbox: [0, 0, 1, 1], image_count: 0, captured_from: null, captured_to: null,
  cost_usd: 0, processing_seconds: 0, review_seconds: 0, km: null, usd_per_km: null, error: null,
  counts: { defects: 0, obstructions: 0, entrances: 0, entrances_with_steps: 0, needs_review: 0 },
});

describe("format", () => {
  it("writes labels as icon and text", () => {
    expect(labelText({ icon: "◼", text: "Tactile line cut by a cover" })).toBe("◼ Tactile line cut by a cover");
  });
  it("asks the reviewer a question that fits the issue type", () => {
    expect(issueQuestion("missing_blocks")).toBe("Are tactile blocks missing here?");
    expect(issueQuestion("cover_cut")).toBe("Does a cover or plate cut the tactile line here?");
    expect(issueQuestion("fixed_obstruction")).toBe("Does a fixed object stand on the tactile paving here?");
    expect(issueQuestion("obstruction")).toBe("Is the tactile paving blocked here?");
  });
  it("explains review reasons in words", () => {
    expect(reasonText("possible_sill")).toContain("cannot be confirmed from photos");
    expect(reasonText("something_new")).toBe("something new");
  });
  it("formats dates, years, time and money", () => {
    expect(photoMonth("2025-09-24T07:21:37Z")).toBe("2025-09");
    expect(yearsSpan("2024-05-01T00:00:00Z", "2025-09-24T07:21:37Z")).toBe("2024–2025");
    expect(yearsSpan("2025-01-01T00:00:00Z", "2025-09-24T07:21:37Z")).toBe("2025");
    expect(yearsSpan(null, null)).toBe("no photos");
    expect(minutes(222)).toBe("3.7 min");
    expect(dollars(0.5)).toBe("$0.50");
  });
  it("formats the street length", () => {
    expect(km(0.4234)).toBe("0.42 km");
  });
  it("lists every stage with its state", () => {
    const states = stageState(scan({ collect: "done", chunks: "running", scene: "failed: SamStreamError" }));
    expect(states.map((s) => s.state)).toEqual(["done", "running", "failed", "pending", "pending", "pending", "pending", "pending"]);
    expect(states.map((s) => s.name)).toEqual(["collect", "chunks", "scene", "sidewalk", "defects", "sidewalk_evidence", "entrances", "evidence"]);
    expect(states[0].label).toContain("Mapillary");
  });
  it("knows when a scan has finished", () => {
    expect(isFinished(scan({}, "running"))).toBe(false);
    expect(isFinished(scan({}, "stopped_cost_cap"))).toBe(true);
    expect(isFinished(null)).toBe(false);
  });
  it("takes the median of review times", () => {
    expect(median([4, 1, 10])).toBe(4);
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(median([])).toBe(0);
  });
});
