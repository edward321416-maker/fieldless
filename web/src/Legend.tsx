import { useMemo } from "react";
import { BADGE_TEXT, colourOf, itemName, type Badge } from "./items";
import { badgeCanvas, markerCanvas } from "./markerArt";
import type { ItemMeta } from "./types";

interface Props {
  items: ItemMeta[];
  counts: Map<string, number>;
  badges: Set<Badge>;
  hidden: Set<string>;
  onToggle: (item: string) => void;
}

const BADGES: Badge[] = ["review", "site", "defect"];

// The legend is the filter (docs/ui-rules.md U4): each item's marker exactly as the map draws it, its count, and a
// switch. Only what is on the map is listed.
export default function Legend({ items, counts, badges, hidden, onToggle }: Props) {
  const art = useMemo(() => Object.fromEntries(items.map((it) => [it.id,
    markerCanvas(colourOf(it.id, items), it.map_icon, it.target, null).toDataURL()])), [items]);
  const badgeArt = useMemo(() => Object.fromEntries(BADGES.map((b) => [b, badgeCanvas(b).toDataURL()])), []);
  const present = items.filter((it) => (counts.get(it.id) ?? 0) > 0);
  return (
    <section className="legend" aria-label="Map legend and filter">
      <ul>
        {present.map((it) => {
          const on = !hidden.has(it.id);
          return (
            <li key={it.id}>
              <button type="button" className={`legend-item${on ? "" : " legend-off"}`} aria-pressed={on}
                onClick={() => onToggle(it.id)} title={on ? "Hide on the map" : "Show on the map"}>
                <img src={art[it.id]} alt="" width={44} height={44} />
                <span className="legend-name">{itemName(it.id, items)}</span>
                <span className="legend-count">{counts.get(it.id)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {BADGES.some((b) => badges.has(b)) && (
        <ul className="legend-badges">
          {BADGES.filter((b) => badges.has(b)).map((b) => (
            <li key={b}><img src={badgeArt[b]} alt="" width={18} height={18} /> {BADGE_TEXT[b]}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
