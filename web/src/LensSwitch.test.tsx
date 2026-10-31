import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LensSwitch from "./LensSwitch";

describe("the map's lens switch", () => {
  it("offers the findings and the city's records, and says which is on with an inline HUD legend", () => {
    const recordsHtml = renderToStaticMarkup(<LensSwitch lens="records" onChange={() => undefined} />);
    expect(recordsHtml).toContain('role="group"');
    expect(recordsHtml).toContain('aria-label="What the map shows"');
    expect(recordsHtml).toMatch(/aria-pressed="false"[^>]*>Findings</);
    expect(recordsHtml).toMatch(/aria-pressed="true"[^>]*>City records</);
    expect(recordsHtml).toContain("Matched");

    const findingsHtml = renderToStaticMarkup(<LensSwitch lens="findings" onChange={() => undefined} />);
    expect(findingsHtml).toContain("1 dot = 1 photo detection");
    expect(findingsHtml).toContain("Click any dot to open photo");
  });
});
