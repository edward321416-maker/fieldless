// The icon set: shapes on a 24 x 24 grid, named as survey standards name them (map_icon = "tree"). The set holds
// shapes, not survey items (docs/ui-rules.md U3): an item the set has no shape for gets its first letter.
// Drawn with fill-rule evenodd, so a shape inside a shape is a hole.
export const ICONS: Record<string, string> = {
  "curb-ramp": "M2 20v-3h6l7-5h7v8z M15 15.5a1 1 0 1 0 0.01 0z M18 15.5a1 1 0 1 0 0.01 0z M15 18a1 1 0 1 0 0.01 0z M18 18a1 1 0 1 0 0.01 0z",
  "street-light": "M11 21V6a3 3 0 0 1 3-3h4v2h-4a1 1 0 0 0-1 1v15z M15 5h6l-1 3h-4z M8 20h8v2H8z",
  tree: "M12 2a6 6 0 0 0-6 6c0 2.6 1.7 4.8 4 5.6V20H8v2h8v-2h-2v-6.4c2.3-.8 4-3 4-5.6a6 6 0 0 0-6-6z",
  "litter-bin": "M9 2h6v2h5v2H4V4h5z M5 8h14l-1.5 14h-11z M9 10v10h1.5V10z M13.5 10v10H15V10z",
  "parking-kiosk": "M6 2h12v14H6z M9 5v8h2v-3h2a2.5 2.5 0 0 0 0-5z M11 7h2a.5.5 0 0 1 0 1h-2z M11 16h2v6h-2z",
  tiles: "M3 3h5v5H3z M10 3h5v5h-5z M17 3h4v5h-4z M3 10h5v5H3z M17 10h4v5h-4z M3 17h5v4H3z M10 17h5v4h-5z M17 17h4v4h-4z",
  cover: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 6a6 6 0 1 1 0 12a6 6 0 1 1 0-12z M7 11h10v2H7z",
  block: "M9 5a3 3 0 0 1 6 0v15H9z M7 20h10v2H7z",
  cone: "M10 3h4l5 16H5z M3 19h18v2H3z",
  door: "M6 2h12v20H6z M14.5 11a1.2 1.2 0 1 0 0.01 0z",
};

export function hasIcon(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(ICONS, name);
}

// The letter drawn when the item names no shape the set has.
export function fallbackLetter(target: string): string {
  const letter = target.trim().charAt(0).toUpperCase();
  return letter || "?";
}
