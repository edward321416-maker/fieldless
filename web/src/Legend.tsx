import { useMemo, useState } from "react";
import { BADGE_TEXT, itemName, type Badge } from "./items";
import { badgeCanvas, itemArt, ringCanvas } from "./markerArt";
import type { ItemMeta } from "./types";

interface Props {
  items: ItemMeta[];
  counts: Map<string, number>;
  badges: Set<Badge>;
  hidden: Set<string>;
  onToggle: (item: string) => void;
  onShowAll: () => void;
}

const BADGES: Badge[] = ["review", "site", "defect"];

// The legend is the filter (docs/ui-rules.md U4): each item's marker exactly as the map draws it, its count, and a
// switch. Only what is on the map is listed.
export default function Legend({ items, counts, badges, hidden, onToggle, onShowAll }: Props) {
  // On a phone the legend would cover the map: it starts folded there.
  const [open, setOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 800);
  const art = useMemo(() => itemArt(items), [items]);
  const badgeArt = useMemo(() => Object.fromEntries(BADGES.map((b) => [b, badgeCanvas(b).toDataURL()])), []);
  const ringArt = useMemo(() => ringCanvas([1, 1, 0, 0, 0, 0, 0, 0]).toDataURL(), []);
  const present = items.filter((it) => (counts.get(it.id) ?? 0) > 0);
  return (
    <section className={`legend${open ? "" : " legend-folded"}`} aria-label="Map legend and filter">
      <button type="button" className="legend-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "Hide legend" : "Legend"}
      </button>
      {open && (<>
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
      {hidden.size > 0 && (
        <p className="legend-hidden">
          {hidden.size} hidden · <button type="button" className="link-button inline" onClick={onShowAll}>Show all</button>
        </p>
      )}
      {BADGES.some((b) => badges.has(b)) && (
        <ul className="legend-badges">
          {BADGES.filter((b) => badges.has(b)).map((b) => (
            <li key={b}><img src={badgeArt[b]} alt="" width={18} height={18} /> {BADGE_TEXT[b]}</li>
          ))}
        </ul>
      )}
      <ul className="legend-badges">
        <li><img src={ringArt} alt="" width={18} height={18} /> Findings close together; the ring shows their items</li>
        <li><span className="legend-walk" aria-hidden="true" /> Walk the photos were taken on</li>
      </ul>
      </>)}
    </section>
  );
}
