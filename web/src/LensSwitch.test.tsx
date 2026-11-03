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
    expect(html).toContain("43 desk review candidates");
    expect(html).not.toContain("no site visit");
    expect(html).not.toContain("Matched");
  });
  it("names the records no photo reached, in every view", () => {
    const reach = { noVisit: 43, visit: 51, unreached: 3198 };
    expect(render("update", reach)).toContain("3,198 not reached");
    expect(render("findings", reach)).toContain("3,198 not reached by photos");
    expect(render("records", reach)).toContain("3,198 not reached by photos");
    expect(render("findings", { noVisit: 43, visit: 51 })).not.toContain("not reached");
  });
  it("keeps the other two views as they were", () => {
    expect(render("findings", { noVisit: 43, visit: 51 })).toContain("1 dot = 1 photo detection");
    expect(render("records", { noVisit: 43, visit: 51 })).toContain("Matched");
  });
});
