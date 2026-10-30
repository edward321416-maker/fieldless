import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LensSwitch from "./LensSwitch";

describe("the map's lens switch", () => {
  it("offers the findings and the city's records, and says which is on", () => {
    const html = renderToStaticMarkup(<LensSwitch lens="records" onChange={() => undefined} />);
    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="What the map shows"');
    expect(html).toMatch(/aria-pressed="false"[^>]*>Findings</);
    expect(html).toMatch(/aria-pressed="true"[^>]*>City records</);
  });
});
