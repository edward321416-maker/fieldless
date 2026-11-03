import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ReviewCosts, { reviewCosts } from "./ReviewCosts";

const scan = { cost_usd: 3.33, km: .924, asset_check: { trust: { second_looks_usd: 2.30 } } };
describe("cost of the prepared review list", () => {
  it("separates the survey and known follow-ups and uses surveyed distance", () => {
    expect(reviewCosts(scan)).toEqual({ survey: 3.33, followUps: 2.30, combined: 5.63, combinedPerKm: 5.63 / .924 });
    const html = renderToStaticMarkup(<ReviewCosts scan={scan} />);
    expect(html).toContain("Survey");
    expect(html).toContain("$3.33");
    expect(html).toContain("$2.30");
    expect(html).toContain("$5.63");
    expect(html).toContain("Human review and official import costs have not been measured.");
  });
  it("does not silently turn missing follow-up accounting into zero", () => {
    const result = reviewCosts({ cost_usd: 3.33, km: .924 });
    expect(result.followUps).toBeNull();
    expect(result.combined).toBeNull();
    expect(result.combinedPerKm).toBeNull();
    const html = renderToStaticMarkup(<ReviewCosts scan={{ cost_usd: 3.33, km: .924 }} />);
    expect(html).toContain("not recorded");
    expect(html).not.toContain("$0.00");
  });
  it("accepts an explicitly recorded zero but not negative, nonfinite or boolean costs", () => {
    expect(reviewCosts({ ...scan, asset_check: { trust: { second_looks_usd: 0 } } }).combined).toBe(3.33);
    for (const invalid of [-1, NaN, Infinity, true]) {
      expect(reviewCosts({ ...scan, asset_check: { trust: { second_looks_usd: invalid } } }).combined).toBeNull();
    }
  });
  it("does not divide by missing/zero distance or display a nonfinite total", () => {
    for (const km of [0, null, NaN, Infinity]) expect(reviewCosts({ ...scan, km }).combinedPerKm).toBeNull();
    expect(reviewCosts({ ...scan, cost_usd: Infinity }).combined).toBeNull();
  });
});
