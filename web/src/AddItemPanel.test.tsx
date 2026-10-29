import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import AddItemPanel from "./AddItemPanel";
import type { ItemRun } from "./types";

const base: ItemRun = {
  id: 4, scan_id: 17, target: "public bench", status: "running", photos: [], item: null, issues: [], cost_usd: 0.08,
  error: null, test: null, created_at: "2026-10-30T00:00:00Z",
  progress: ['Muse Spark drafted the item: find "bench", ask "Is there a public bench?"'],
};
const noop = async () => undefined;
const render = (over: Partial<Parameters<typeof AddItemPanel>[0]> = {}) => renderToStaticMarkup(
  <AddItemPanel canMake={false} run={null} examples={[]} defaultOpen onStart={noop} onOpen={() => undefined}
    onClose={() => undefined} {...over} />);

describe("adding a survey item", () => {
  it("without an invite code on this server, offers the finished examples", () => {
    const html = render({ examples: [{ id: 4, target: "bike rack", status: "done", created_at: "" }] });
    expect(html).toContain("See the example: bike rack");
    expect(html).toContain("needs an invite code");
    expect(html).not.toContain("Write and test the item");
  });
  it("with one, asks what the survey should record", () => {
    const html = render({ canMake: true });
    expect(html).toContain("What should the survey record?");
    expect(html).toContain("Write and test the item");
  });
  it("shows the agent's steps as they come", () => {
    expect(render({ run: base })).toContain("Muse Spark drafted the item: find &quot;bench&quot;");
  });
  it("says why a run failed", () => {
    const html = render({ run: { ...base, status: "failed", error: "Too few photos show a public bench here" } });
    expect(html).toContain("Too few photos show a public bench here");
    expect(html).toContain("Try another item");
  });
  it("sums up a finished run: what was added, its scores, the city's list and the cost", () => {
    const run: ItemRun = { ...base, target: "bike rack", status: "done", cost_usd: 0.4612,
      test: { labels: "model", judge: "Muse Spark Open", layer: "SDOT bike racks", phrase: "bike rack", ask: "?", calls: 4,
        found: [11, 12],
        kept: [11, 12], false: [0, 6], city: { layer: "SDOT bike racks", found: 14, covered: 22 } } };
    const html = render({ run });
    expect(html).toContain("Muse Spark added “bike rack”");
    expect(html).toContain("$0.46");
    expect(html).toContain("Kept 11 of 12 photos showing it and 0 of 6 without it");
    expect(html).toContain("on Muse Spark Open&#x27;s answers");
    expect(html).not.toContain("Does this photo show");
    expect(html).toContain("14 of 22 in SDOT bike racks");
    expect(html).not.toContain("so far");
    expect(html).toContain("Share this survey with its new item");
  });
});
