import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LensSwitch from "./LensSwitch";

const render = (lens: "findings" | "records" | "update", update?: { noVisit: number; visit: number }) =>
  renderToStaticMarkup(<LensSwitch lens={lens} onChange={() => undefined} update={update} />);

describe("the map's views", () => {
  it("offers the update list only for a survey that has one", () => {
    expect(render("findings")).not.toContain("Site visits");
    expect(render("findings", { noVisit: 43, visit: 51 })).toContain(">Site visits</button>");
  });
  it("keys the update list's dots with the list's own numbers", () => {
    const html = render("update", { noVisit: 43, visit: 51 });
    expect(html).toContain("51 visit on site");
    expect(html).toContain("43 no site visit");
    expect(html).not.toContain("Matched");
  });
  it("keeps the other two views as they were", () => {
    expect(render("findings", { noVisit: 43, visit: 51 })).toContain("1 dot = 1 photo detection");
    expect(render("records", { noVisit: 43, visit: 51 })).toContain("Matched");
  });
});
