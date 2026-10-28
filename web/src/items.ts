import type { Entrance, Issue, ItemMeta, Picked } from "./types";

// Item colours come from the order of the scan's items (the survey standard's checks, then the stages' own), so the
// web app names no survey item (docs/ui-rules.md U2, U3). Colour-blind safe (Okabe-Ito and neighbours); no red or
// orange, which mean "possible defect" and "check on site".
export const PALETTE = ["#0072B2", "#009E73", "#CC79A7", "#56B4E9", "#E6C800", "#6A3D9A", "#1B7837", "#4D4D4D"];
export const DEFECT_RED = "#B91C1C";
export const SITE_ORANGE = "#C2410C";

export function colourOf(itemId: string, items: ItemMeta[]): string {
  const i = items.findIndex((it) => it.id === itemId);
  return PALETTE[(i < 0 ? items.length : i) % PALETTE.length];
}

// A light fill takes a dark icon, so the icon keeps its contrast.
export function isLight(hex: string): boolean {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.35;
}

export const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function itemName(itemId: string, items: ItemMeta[]): string {
  return capital(items.find((it) => it.id === itemId)?.target ?? itemId.replace(/_/g, " "));
}

// What a marker's corner says: is there something for a person to do? Three kinds only (U3).
export type Badge = "review" | "site" | "defect";

export function badgeOf(status: string, defect: boolean): Badge | null {
  if (status === "field_check") return "site";
  if (defect) return "defect";
  if (status === "needs_review") return "review";
  return null;
}

export const BADGE_TEXT: Record<Badge, string> = {
  review: "Waiting for a person to check",
  site: "A person could not tell: check on site",
  defect: "Possible defect",
};

export const STATUS_SENTENCE: Record<string, string> = {
  auto: "Found by the models · not checked by a person",
  needs_review: "Found by the models · waiting for a person to check",
  verified: "Confirmed by a person",
  field_check: "A person could not tell from the photo · check on site",
  rejected: "Rejected by a person",
};

// One thing on the map: a sidewalk row or an entrance, with what the map, the list and the card need.
export interface Finding {
  key: string;
  item: string;
  lat: number;
  lng: number;
  heading: number | null;
  box: number[] | null;
  status: string;
  badge: Badge | null;
  title: string;
  picked: Picked;
}

export function findingsOf(issues: Issue[], entrances: Entrance[]): Finding[] {
  const out: Finding[] = issues.map((i) => ({
    key: `issue:${i.id}`, item: i.issue_type, lat: i.lat, lng: i.lng, heading: i.heading ?? null, box: i.box ?? null,
    status: i.review_status, badge: badgeOf(i.review_status, i.defect), title: i.label.text,
    picked: { kind: "issue", item: i },
  }));
  for (const e of entrances) {
    out.push({
      key: `entrance:${e.id}`, item: "entrance", lat: e.lat, lng: e.lng, heading: e.heading ?? null, box: null,
      status: e.review_status, badge: badgeOf(e.review_status, false),
      title: `${e.name || "Unnamed entrance"}: ${e.label.text}`, picked: { kind: "entrance", item: e },
    });
  }
  return out;
}

export const keyOf = (p: Picked) => `${p.kind}:${p.item.id}`;
