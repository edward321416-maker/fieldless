import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { stageRows, type CallStats } from "./callStats";
import HowMade from "./HowMade";

// Survey 17's call record, cut down: two stages with a free upload row, the open model without a cost, the report.
const stats: CallStats = {
  scan_id: 17,
  note: "seconds adds up each call's own duration.",
  stages: [
    { stage: "items", label: "Survey items", model: "SAM 3.1", calls: 282, seconds: 586.4, cost_usd: 0.7805 },
    { stage: "items", label: "Survey items", model: "Muse Spark", calls: 144, seconds: 1406.6, cost_usd: 1.6991 },
    { stage: "items", label: "Survey items", model: "File upload (for SAM 3.1 video)", calls: 4, seconds: 47.5, cost_usd: 0 },
    { stage: "second_opinion", label: "Second opinion on possible defects", model: "Muse Spark Open", calls: 1, seconds: 15.8, cost_usd: null },
    { stage: "report", label: "Report text", model: "Muse Spark", calls: 2, seconds: 60.1, cost_usd: 0.081 },
  ],
  total: { calls: 433, seconds: 2116.4, cost_usd: 2.5606, scan_cost_usd: 2.5606, processing_seconds: 420.5 },
};

describe("how a survey was made", () => {
  it("groups the calls by stage in their order, models within, leaving the free file uploads out", () => {
    const rows = stageRows(stats);
    expect(rows.map((r) => r.stage)).toEqual(["items", "second_opinion", "report"]);
    expect(rows[0]).toEqual({ stage: "items", label: "Survey items", cost: 2.4796, share: 2.4796 / 2.5606,
      models: [{ model: "SAM 3.1", calls: 282, cost: 0.7805 }, { model: "Muse Spark", calls: 144, cost: 1.6991 }] });
    expect(rows[1]).toEqual({ stage: "second_opinion", label: "Second opinion on possible defects", cost: null, share: 0,
      models: [{ model: "Muse Spark Open", calls: 1, cost: null }] });
  });
  it("tucks the breakdown into a collapsible drawer while showing the summary and stage details", () => {
    const html = renderToStaticMarkup(<HowMade stats={stats} />);
    expect(html).toContain("how-made-drawer");
    expect(html).toContain("How this survey was made");
    expect(html).toContain("429 model calls · $2.56 in all · 7.0 min of processing");
    expect(html).toContain("Survey items");
    expect(html).toContain("SAM 3.1 × 282");
    expect(html).toContain("Muse Spark × 144");
    expect(html).toContain("$2.48");
    expect(html).toContain("Muse Spark Open × 1");
    expect(html).toContain("cost not logged");
    expect(html).not.toContain("File upload (for SAM 3.1 video) ×");
    expect(html).toContain("Calls run side by side");
  });
});
