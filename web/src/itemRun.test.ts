import { describe, expect, it } from "vitest";
import { isActive, itemFromHash, withRun } from "./itemRun";
import type { AgentItem, Issue, ItemMeta, ItemRun } from "./types";

const pole: ItemMeta = { id: "utility_pole", target: "utility or streetlight pole", map_icon: "", kind: "check", inventory: true };
const rack = { id: "agent_bike_rack", target: "bike rack", map_icon: "", kind: "check", inventory: true,
  added_by: "agent" } as AgentItem;
const issue = (id: number | string, type: string, run?: number) =>
  ({ id, issue_type: type, ...(run ? { item_run: run } : {}) }) as unknown as Issue;
const run = (status: ItemRun["status"]): ItemRun => ({
  id: 4, scan_id: 17, target: "bike rack", status, progress: [], photos: [], item: status === "done" ? rack : null,
  issues: status === "done" ? [issue("item4-1", "agent_bike_rack", 4)] : [], cost_usd: 0.4, error: null, test: null,
  created_at: "2026-10-30T00:00:00Z",
});

describe("an item the agent wrote", () => {
  it("opens from the address next to its survey", () => {
    expect(itemFromHash("#scan=17&item=4")).toBe(4);
    expect(itemFromHash("#scan=17")).toBeNull();
    expect(itemFromHash("")).toBeNull();
  });
  it("is followed while it is queued or running, and no longer once it waits, ends or fails", () => {
    expect(isActive(run("queued"))).toBe(true);
    expect(isActive(run("running"))).toBe(true);
    expect(isActive(run("labels_needed"))).toBe(false);
    expect(isActive(run("done"))).toBe(false);
    expect(isActive(null)).toBe(false);
  });
  it("joins the survey only when done: its item takes the next colour slot and its rows join the findings", () => {
    const base = withRun([pole], [issue(1, "utility_pole")], run("running"));
    expect(base.items).toEqual([pole]);
    expect(base.issues.map((i) => i.id)).toEqual([1]);
    const done = withRun([pole], [issue(1, "utility_pole")], run("done"));
    expect(done.items.map((i) => i.id)).toEqual(["utility_pole", "agent_bike_rack"]);
    expect(done.issues.map((i) => i.id)).toEqual([1, "item4-1"]);
    const twice = withRun(done.items, done.issues, run("done"));
    expect(twice.items).toHaveLength(2);
    expect(twice.issues).toHaveLength(2);
  });
});
