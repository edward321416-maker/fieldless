import { useEffect, useRef, useState } from "react";
import { photoMonth } from "./format";
import { colourOf, itemName, STATUS_SENTENCE, type Finding } from "./items";
import { cityLine, itemNote, thumbPosition } from "./summary";
import type { Scan } from "./types";

interface Props {
  scan: Scan;
  item: string;
  findings: Finding[]; // this item's, in panel order
  streets: Record<string, string>; // "11th Avenue, near East Pike Street" by finding key, from the map's road names
  selectedKey: string | null;
  onPick: (f: Finding) => void;
  onBack: () => void;
}

const STATUS_SHORT: Record<string, string> = {
  needs_review: "To review", field_check: "Check on site", verified: "Confirmed", auto: "",
};
const FIRST = 10; // shown before "Show n more": a phone would scroll for metres

// One item's findings (docs/specs/2026-10-29-cleanup-design.md 4): each with its street, its photo's month, what a
// person may have to do, and a thumbnail centred on the model's box.
export default function FindingList(props: Props) {
  const { scan, findings } = props;
  const items = scan.items;
  const [all, setAll] = useState(false);
  const selectedRow = useRef<HTMLButtonElement>(null);
  const at = findings.findIndex((f) => f.key === props.selectedKey);
  const shown = all || at >= FIRST ? findings : findings.slice(0, FIRST);
  const note = itemNote({
    city: cityLine(scan.city_check?.items[props.item], !!scan.city_check)?.text ?? null,
    confirmed: findings.filter((f) => f.badge === "defect" && f.status === "verified").length,
    possible: findings.filter((f) => f.badge === "defect" && f.status !== "verified").length,
    site: findings.filter((f) => f.badge === "site").length,
  });

  useEffect(() => {
    selectedRow.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [props.selectedKey]);

  return (
    <section className="panel-section finding-panel" aria-labelledby="item-title">
      <button type="button" className="link-button back-intro" onClick={props.onBack}>← All findings</button>
      <h2 id="item-title" className="item-title">
        <span className="dot" style={{ background: colourOf(props.item, items) }} aria-hidden="true" />
        <span className="item-title-name">{itemName(props.item, items)}</span>
        <span className="item-count">{findings.length}</span>
      </h2>
      {note && <p className="sub">{note}</p>}
      <ul className="finding-list">
        {shown.map((f) => {
          const evidence = f.picked.item.evidence;
          const street = props.streets[f.key];
          const status = STATUS_SHORT[f.status] ?? "";
          const selected = f.key === props.selectedKey;
          // the finding's own words only when they say more than the item's name ("Curb ramp without detectable warning")
          const title = f.title.toLowerCase() === itemName(props.item, items).toLowerCase() ? "" : f.title;
          return (
            <li key={f.key}>
              <button type="button" ref={selected ? selectedRow : undefined}
                className={`finding${selected ? " finding-selected" : ""}`} aria-current={selected || undefined}
                onClick={() => props.onPick(f)}
                aria-label={`${f.title}${street ? `, ${street}` : ""}. ${STATUS_SENTENCE[f.status] ?? ""}`}>
                <span className="finding-text">
                  <span className="finding-title">{street ?? (title || "Position from the photo")}</span>
                  <span className="finding-sub">Photo {photoMonth(f.picked.item.captured_at)}{street && title ? ` · ${title}` : ""}</span>
                  {status && <span className={`chip chip-${f.badge ?? "none"}`}>{status}</span>}
                </span>
                {evidence ? (
                  <img className="thumb" src={evidence.url} alt="" loading="lazy"
                    style={{ objectPosition: thumbPosition(f.box) }} />
                ) : <span className="thumb" />}
              </button>
            </li>
          );
        })}
      </ul>
      {shown.length < findings.length && (
        <button type="button" className="link-button" onClick={() => setAll(true)}>
          Show {findings.length - shown.length} more
        </button>
      )}
      <p className="hint small">Photos: Mapillary contributors, CC BY-SA 4.0</p>
    </section>
  );
}
