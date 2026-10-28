import { fallbackLetter, hasIcon, ICONS } from "./icons";
import { DEFECT_RED, isLight, SITE_ORANGE, type Badge } from "./items";

// One marker = the item's colour circle with its icon (what it is) and at most one badge at the top right (whether
// a person has something to do): docs/ui-rules.md U3. The legend draws the same canvases, so the two always match.
export const MARKER_PX = 32;
export const CANVAS_PX = 44; // the circle centred, with room for the badge
export const RATIO = 2;
const BADGE_R = 8;

export function drawBadge(g: CanvasRenderingContext2D, x: number, y: number, badge: Badge): void {
  g.beginPath();
  g.arc(x, y, BADGE_R, 0, 2 * Math.PI);
  g.fillStyle = badge === "review" ? "#ffffff" : badge === "site" ? SITE_ORANGE : DEFECT_RED;
  g.fill();
  g.lineWidth = 1.5;
  g.strokeStyle = badge === "review" ? "#374151" : "#ffffff";
  g.stroke();
  g.fillStyle = badge === "review" ? "#111827" : "#ffffff";
  if (badge === "site") { // a flag on a pole
    g.fillRect(x - 3, y - 4.5, 1.5, 9);
    g.beginPath();
    g.moveTo(x - 1.5, y - 4.5);
    g.lineTo(x + 4.5, y - 2.5);
    g.lineTo(x - 1.5, y - 0.5);
    g.closePath();
    g.fill();
  } else {
    g.font = "bold 11px system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(badge === "review" ? "?" : "!", x, y + 0.5);
  }
}

export function markerCanvas(colour: string, icon: string, target: string, badge: Badge | null): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = c.height = CANVAS_PX * RATIO;
  const g = c.getContext("2d")!;
  g.scale(RATIO, RATIO);
  const mid = CANVAS_PX / 2;
  const r = MARKER_PX / 2;
  g.shadowColor = "rgba(0, 0, 0, 0.35)";
  g.shadowBlur = 3;
  g.shadowOffsetY = 1;
  g.beginPath();
  g.arc(mid, mid, r, 0, 2 * Math.PI);
  g.fillStyle = "#ffffff";
  g.fill();
  g.shadowColor = "transparent";
  g.beginPath();
  g.arc(mid, mid, r - 2, 0, 2 * Math.PI);
  g.fillStyle = colour;
  g.fill();
  const ink = isLight(colour) ? "#111827" : "#ffffff";
  g.fillStyle = ink;
  if (hasIcon(icon)) {
    g.save();
    g.translate(mid - 9, mid - 9);
    g.scale(18 / 24, 18 / 24);
    g.fill(new Path2D(ICONS[icon]), "evenodd");
    g.restore();
  } else {
    g.font = "bold 15px system-ui, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(fallbackLetter(target), mid, mid + 1);
  }
  if (badge) drawBadge(g, CANVAS_PX - BADGE_R, BADGE_R, badge);
  return c;
}

export function badgeCanvas(badge: Badge): HTMLCanvasElement {
  const size = BADGE_R * 2 + 2;
  const c = document.createElement("canvas");
  c.width = c.height = size * RATIO;
  const g = c.getContext("2d")!;
  g.scale(RATIO, RATIO);
  drawBadge(g, size / 2, size / 2, badge);
  return c;
}

export const markerName = (item: string, badge: Badge | null) => `m:${item}:${badge ?? "none"}`;
