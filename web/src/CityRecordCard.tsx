import { Fragment, useEffect } from "react";
import type { MissedRecord } from "./cityMatch";
import { colourOf, itemName } from "./items";
import type { ItemMeta } from "./types";

interface Props {
  record: MissedRecord;
  items: ItemMeta[];
  onClose: () => void;
}

// A city record in a photo's view that no finding was paired with: what it is, the city's list and the ids the city
// keeps it by, so a surveyor can look it up in the city's own system before going out. Its dot is the map's (light
// grey, ringed in the item's colour).
export default function CityRecordCard({ record, items, onClose }: Props) {
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <section className="panel-section record-card" aria-labelledby="record-title">
      <h2 id="record-title" className="item-title">
        <span className="dot record-dot" style={{ borderColor: colourOf(record.item, items) }} aria-hidden="true" />
        <span className="item-title-name">{itemName(record.item, items)}</span>
      </h2>
      <p className="sub">{record.layer}</p>
      <dl className="record-ids">
        {record.fields.map(([name, value]) => (
          <Fragment key={name}><dt>{name}</dt><dd>{value}</dd></Fragment>
        ))}
      </dl>
      <p>In the city's list and in a photo's view; not found by this survey. Check on site or in the photos.</p>
      <button type="button" onClick={onClose}>Close</button>
    </section>
  );
}
