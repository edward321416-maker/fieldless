import { Fragment, useEffect } from "react";
import { NearbyPhotoLink } from "./AssetCheckPane";
import type { MissedRecord } from "./cityMatch";
import { colourOf, itemName } from "./items";
import { recordLine } from "./story";
import type { AssetRow, ItemMeta } from "./types";

interface Props {
  record: MissedRecord;
  items: ItemMeta[];
  row?: AssetRow; // the update list's row for the record, when there is one
  unreached?: boolean; // a record in the box no photo reached
  credit?: string; // the words the city's data asks to be shown with its records
  onClose: () => void;
}

// A city record in a photo's view that no finding was paired with: what it is, the city's list and the ids the city
// keeps it by, so a surveyor can look it up in the city's own system before going out. Its dot is the map's (a faint
// dot in the item's colour).
const UNREACHED = "In the city's list, inside the survey's box. No survey photo reached it, so the survey says nothing about it.";

export default function CityRecordCard({ record, items, row, unreached, credit, onClose }: Props) {
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
        <span className="dot record-dot" style={{ background: colourOf(record.item, items) }} aria-hidden="true" />
        <span className="item-title-name">{itemName(record.item, items)}</span>
      </h2>
      <p className="sub">{record.layer}</p>
      <dl className="record-ids">
        {record.fields.map(([name, value]) => (
          <Fragment key={name}><dt>{name}</dt><dd>{value}</dd></Fragment>
        ))}
      </dl>
      <p>{unreached ? UNREACHED : recordLine(row)}</p>
      {row?.nearby_photo && <NearbyPhotoLink photo={row.nearby_photo} />}
      {credit && <p className="hint small">{credit}</p>}
      <button type="button" onClick={onClose}>Close</button>
    </section>
  );
}
