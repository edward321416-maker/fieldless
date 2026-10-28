import { describe, expect, it } from "vitest";
import {
  dollars, isFinished, issueQuestion, issueTone, km, labelText, median, minutes, photoMonth, reasonText, stageState, yearsSpan,
} from "./format";
import type { Scan } from "./types";

const scan = (stage_status: Record<string, string>, status = "running", standard: "KR" | "US" = "KR"): Scan => ({
  id: 1, status, stage_status, bbox: [0, 0, 1, 1], image_count: 0, captured_from: null, captured_to: null,
  cost_usd: 0, processing_seconds: 0, review_seconds: 0, km: null, usd_per_km: null, standard, checks: [], error: null,
  counts: { defects: 0, obstructions: 0, entrances: 0, entrances_with_steps: 0, needs_review: 0, field_check: 0 },
});

describe("format", () => {
  it("writes labels as icon and text", () => {
    expect(labelText({ icon: "◼", text: "Tactile line cut by a cover" })).toBe("◼ Tactile line cut by a cover");
  });
  it("asks the reviewer a question that fits the item", () => {
    const base = { id: 1, lat: 0, lng: 0, durable: true, defect: true, object_label: null, frame_start: 1, frame_end: 1,
      review_status: "needs_review", review_reasons: [], evidence: null, captured_at: null, old_photo: false, check: null, second_opinion: null };
    expect(issueQuestion({ ...base, issue_type: "missing_blocks", label: { icon: "✖", text: "" } })).toBe("Are tactile blocks missing here?");
    expect(issueQuestion({ ...base, issue_type: "obstruction", label: { icon: "⛔", text: "" } })).toBe("Is the tactile paving blocked here?");
    const check = { id: "curb_ramp_warning", find: "curb cut", target: "curb ramp", question: "Does it have domes?",
      answer: "no" as const, report_when: "no" as const, label_yes: "Curb ramp with detectable warning", label_no: "Curb ramp without detectable warning",
      inventory: false, model: "Muse Spark" };
    const ramp = { ...base, issue_type: "curb_ramp_warning", label: { icon: "✖", text: "Curb ramp without detectable warning" }, check };
    expect(issueQuestion(ramp)).toBe("Does it have domes?");
    expect(issueQuestion({ ...ramp, check: { ...check, answer: "yes" } })).toBe("Does it have domes?");
    expect(issueQuestion({ ...ramp, check: { ...check, answer: "unclear" } })).toBe("Does it have domes?");
  });
  it("colours a check row by whether it is a defect, not by its answer", () => {
    const check = { id: "ramp", find: "curb cut", target: "curb ramp", question: "?", answer: "no" as const,
      report_when: "no" as const, label_yes: "Y", label_no: "N", inventory: false, model: "Muse Spark" };
    const row = { id: 1, lat: 0, lng: 0, durable: true, defect: true, object_label: "no", frame_start: 1, frame_end: 1,
      review_status: "needs_review", review_reasons: [], evidence: null, captured_at: null, old_photo: false,
      second_opinion: null, issue_type: "ramp", label: { icon: "✖", text: "N" }, check };
    expect(issueTone(row)).toBe("check-defect");
    expect(issueTone({ ...row, defect: false, check: { ...check, answer: "yes" } })).toBe("check-ok");
    expect(issueTone({ ...row, defect: false, check: { ...check, answer: "unclear" } })).toBe("check-unclear");
    // when "yes" is the defect, a "no" answer is fine
    expect(issueTone({ ...row, defect: false, check: { ...check, report_when: "yes" } })).toBe("check-ok");
    expect(issueTone({ ...row, issue_type: "cover_cut", check: null })).toBe("cover_cut");
    // an inventory row is an asset, unless it is still unclear
    const asset = { ...check, answer: "yes" as const, report_when: "" as const, inventory: true };
    expect(issueTone({ ...row, defect: false, check: asset })).toBe("check-asset");
    expect(issueTone({ ...row, defect: false, check: { ...asset, answer: "unclear" } })).toBe("check-unclear");
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
  it("names the stages of a US scan by what they survey", () => {
    const us = stageState(scan({}, "running", "US"));
    expect(us.find((s) => s.name === "sidewalk")?.label).toBe("Track the walking path and objects on it (SAM 3.1)");
    expect(us.find((s) => s.name === "defects")?.label).toBe("Check the survey items: SAM 3.1 finds, Muse Spark answers");
    expect(stageState(scan({})).find((s) => s.name === "defects")?.label).toContain("tactile");
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
