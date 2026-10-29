import type { Issue, ItemMeta, ItemRun } from "./types";

// An item the agent wrote in the app (docs/specs/2026-10-29-agent-items-design.md 2): its run opens from the address
// next to its survey ("#scan=17&item=4"), and once done its item and rows join the survey's. The survey's own data
// never changes; the run is looked up again on every visit.
export function itemFromHash(hash: string): number | null {
  const match = hash.match(/item=(\d+)/);
  return match ? Number(match[1]) : null;
}

export const isActive = (run: ItemRun | null) => !!run && (run.status === "queued" || run.status === "running");

// The survey's items and rows with a finished run's added: its item last, so it takes the palette's next colour.
export function withRun(items: ItemMeta[], issues: Issue[], run: ItemRun | null): { items: ItemMeta[]; issues: Issue[] } {
  if (!run || run.status !== "done" || !run.item) return { items, issues };
  const item = run.item;
  return {
    items: items.some((it) => it.id === item.id) ? items : [...items, item],
    issues: [...issues.filter((i) => i.item_run !== run.id), ...run.issues],
  };
}
