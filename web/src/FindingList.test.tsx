import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import FindingList, { listCredit } from "./FindingList";
import type { Finding } from "./items";
import type { Scan } from "./types";

const finding = (key: string, creator: string | null, evidence = true) => ({
  key, item: "street_tree", title: "Street tree", status: "auto", badge: null, box: null, lat: 0, lng: 0, heading: null,
  picked: { kind: "issue", item: { id: key, captured_at: "2026-07-01T00:00:00Z", old_photo: false,
    evidence: evidence ? { url: `/api/evidence/${key}.jpg`, creator, captured_at: "2026-07-01T00:00:00Z",
      link: `https://www.mapillary.com/app/?pKey=${key}`, license: "CC BY-SA 4.0", blurred: true } : null } },
}) as unknown as Finding;
const scan = { id: 30, items: [{ id: "street_tree", target: "street tree", map_icon: "tree", kind: "check", inventory: true }] } as unknown as Scan;

describe("the item list's photo credit", () => {
  it("names the creators of the photos shown, a few by name and the rest counted", () => {
    expect(listCredit([finding("a", "tannewt"), finding("b", "tannewt"), finding("c", "jlin4529")]))
      .toEqual({ names: "tannewt, jlin4529", more: 0, unnamed: false });
    expect(listCredit(["a", "b", "c", "d", "e"].map((k) => finding(k, `p${k}`))).more).toBe(2);
    expect(listCredit([finding("a", null)]).unnamed).toBe(true);
    expect(listCredit([finding("a", "x", false)])).toEqual({ names: "", more: 0, unnamed: false });
  });
  it("links the Mapillary logo and the licence", () => {
    const html = renderToStaticMarkup(<FindingList scan={scan} item="street_tree" findings={[finding("a", "tannewt"), finding("b", "jlin4529")]}
      streets={{}} selectedKey={null} onPick={() => undefined} onBack={() => undefined} />);
    expect(html).toContain("Photos by tannewt, jlin4529 ·");
    expect(html).toContain('href="https://www.mapillary.com"');
    expect(html).toContain('class="mapillary-logo"');
    expect(html).toContain('href="https://creativecommons.org/licenses/by-sa/4.0/"');
    expect(html).not.toContain("contributors, CC BY-SA 4.0. Open a row for its creator and link.");
  });
});
