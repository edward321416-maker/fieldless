import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Attribution from "./Attribution";
import { PhotoCredit, tagOverflows, tagPlace, zoomTransform } from "./Photo";
import { choiceForKey, choicesFor } from "./review";
import type { Issue } from "./types";

const evidence = {
  url: "/api/evidence/scan1_issue1.jpg", creator: "West9876", captured_at: "2023-02-08T23:11:57Z",
  link: "https://www.mapillary.com/app/?pKey=1154200575281762", license: "CC BY-SA 4.0", blurred: true,
};

describe("attribution", () => {
  it("credits the photo's creator, month, Mapillary page and licence", () => {
    const html = renderToStaticMarkup(<PhotoCredit evidence={evidence} />);
    expect(html).toContain("West9876");
    expect(html).toContain("2023-02");
    expect(html).toContain('href="https://www.mapillary.com/app/?pKey=1154200575281762"');
    expect(html).toContain("CC BY-SA 4.0");
    expect(html).toContain("people and plates blurred");
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

describe("the card", () => {
  it("zooms so the box fills the frame, never past the photo's edge", () => {
    expect(zoomTransform([450, 450, 550, 550])).toBe("translate(-150%, -150%) scale(4)");
    expect(zoomTransform([0, 0, 1000, 1000])).toBe("translate(0%, 0%) scale(1)");
    expect(zoomTransform([900, 0, 1000, 100])).toBe("translate(-300%, 0%) scale(4)");
  });

  const issue = (check: Partial<NonNullable<Issue["check"]>> | null): Issue => ({
    id: 7, lat: 0, lng: 0, heading: 90, box: [1, 2, 3, 4], issue_type: "curb_ramp_warning", durable: true, defect: false,
    check: check && { id: "curb_ramp_warning", find: "curb cut", target: "curb ramp", question: "q", note: null,
      answer: "unclear", report_when: "no", label_yes: "Has a warning surface", label_no: "No warning surface",
      inventory: false, model: "Muse Spark", ...check },
    second_opinion: null, label: { icon: "?", text: "t" }, object_label: null, frame_start: 1, frame_end: 1,
    review_status: "needs_review", review_reasons: [], evidence: null, captured_at: null, old_photo: false,
  });

  it("gives a defect check four answers with keys, in the standard's words", () => {
    const { question, choices } = choicesFor({ kind: "issue", item: issue({}) });
    expect(question).toBe("What do you see?");
    expect(choices.map((c) => [c.key, c.label])).toEqual([
      ["1", "Has a warning surface"], ["2", "No warning surface"], ["N", "Not a curb ramp"],
      ["C", "Can't tell from the photo: check on site"]]);
    expect(choiceForKey(choices, "n")?.send).toEqual({ kind: "issue", body: { action: "reject" } });
    expect(choiceForKey(choices, "2")?.send).toEqual({ kind: "issue", body: { action: "answer", answer: "no" } });
    expect(choiceForKey(choices, "x")).toBeUndefined();
  });

  it("gives an inventory item yes, not, or can't tell", () => {
    const { choices } = choicesFor({ kind: "issue", item: issue({ inventory: true, target: "street tree" }) });
    expect(choices.map((c) => c.key)).toEqual(["Y", "N", "C"]);
    expect(choices[0]).toMatchObject({ label: "Yes, a street tree", send: { body: { action: "answer", answer: "yes" } } });
    expect(choiceForKey(choices, "c")?.send.body).toEqual({ action: "field_check" });
  });
});

describe("the box's tag", () => {
  it("stays inside the photo: under the top edge, and ending at the box when it would run past the right", () => {
    expect(tagPlace([100, 300, 200, 500])).toEqual({ inside: false });
    expect(tagPlace([100, 0, 200, 900])).toEqual({ inside: true });
    expect(tagOverflows(420, 400)).toBe(true); // issue 136 on a phone: x1 = 577, a long tag
    expect(tagOverflows(400.3, 400)).toBe(false);
  });
});
