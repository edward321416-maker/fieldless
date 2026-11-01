import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LandingNav from "./LandingNav";

describe("the first screen's links", () => {
  it("links the example surveys, as Examples, when there are some", () => {
    const html = renderToStaticMarkup(<LandingNav hasExamples />);
    expect(html).toContain('<a href="#overview-cities">Examples</a>');
    expect(html).not.toContain(">Cities<");
  });
  it("leaves out the link to the example surveys when there are none, so no link goes nowhere", () => {
    const html = renderToStaticMarkup(<LandingNav hasExamples={false} />);
    expect(html).not.toContain("overview-cities");
    expect(html).not.toContain("Examples");
  });
  it("always links the other three sections", () => {
    const html = renderToStaticMarkup(<LandingNav hasExamples={false} />);
    for (const id of ["overview-items", "overview-flow", "overview-checks"]) expect(html).toContain(`href="#${id}"`);
  });
});
