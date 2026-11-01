import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidencePhoto, zoomScale } from "./Photo";

const evidence = {
  url: "/api/evidence/scan1_issue1.jpg", creator: "West9876", captured_at: "2023-02-08T23:11:57Z",
  link: "https://www.mapillary.com/app/?pKey=1154200575281762", license: "CC BY-SA 4.0", blurred: true,
};

describe("zoomScale", () => {
  it("is 1 for a box as wide as the photo", () => expect(zoomScale([0, 300, 1000, 900])).toBe(1));
  it("enlarges a small box, at most 4 times", () => expect(zoomScale([400, 400, 450, 450])).toBe(4));
});

const photo = (box: number[] | null) => renderToStaticMarkup(
  <EvidencePhoto evidence={evidence} box={box} boxLabel="the pole" colour="#2563eb" alt="Street photo" />);

describe("Zoom to box", () => {
  it("is offered for a box that is small in the photo", () => {
    expect(photo([400, 400, 450, 450])).toContain("Zoom to box");
    expect(photo([0, 0, 600, 300])).toContain("Zoom to box"); // 0.8 / 0.6 = 1.33 times
  });

  it("is not shown when it would enlarge the photo by less than 1.3 times", () => {
    expect(photo([0, 0, 700, 300])).not.toContain("Zoom to box"); // 1.14 times
    expect(photo([0, 300, 1000, 900])).not.toContain("Zoom to box"); // as wide as the photo
    expect(photo([0, 300, 1000, 900])).toContain("Open full size");
  });

  it("is replaced by the hint when the model marked no box", () => {
    const html = photo(null);
    expect(html).not.toContain("Zoom to box");
    expect(html).toContain("The model did not mark where it is.");
  });
});
