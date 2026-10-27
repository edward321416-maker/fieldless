import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Attribution from "./Attribution";
import { PhotoCredit } from "./EvidenceCard";

const evidence = {
  url: "/api/evidence/scan1_issue1.jpg", creator: "West9876", captured_at: "2023-02-08T23:11:57Z",
  link: "https://www.mapillary.com/app/?pKey=1154200575281762", license: "CC BY-SA 4.0",
};

describe("attribution", () => {
  it("credits the photo's creator, month, Mapillary page and licence", () => {
    const html = renderToStaticMarkup(<PhotoCredit evidence={evidence} />);
    expect(html).toContain("West9876");
    expect(html).toContain("2023-02");
    expect(html).toContain('href="https://www.mapillary.com/app/?pKey=1154200575281762"');
    expect(html).toContain("CC BY-SA 4.0");
  });
  it("falls back when the creator is unknown", () => {
    const html = renderToStaticMarkup(<PhotoCredit evidence={{ ...evidence, creator: null }} />);
    expect(html).toContain("a Mapillary contributor");
  });
  it("credits Mapillary, OpenStreetMap and OpenFreeMap on the map view", () => {
    const html = renderToStaticMarkup(<Attribution />);
    expect(html).toContain("https://www.mapillary.com");
    expect(html).toContain("OpenStreetMap");
    expect(html).toContain("OpenFreeMap");
  });
});
