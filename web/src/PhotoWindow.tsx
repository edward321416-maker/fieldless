import { itemName, type Finding } from "./items";
import { EvidencePhoto, PhotoCredit } from "./Photo";
import type { ItemMeta } from "./types";

interface Props {
  finding: Finding;
  list: Finding[];
  items: ItemMeta[];
  onStep: (f: Finding) => void;
  onClose: () => void;
}

// The selected finding's photo over the map, so the map and the photo are seen together (docs/specs ui-redesign
// 3.3, after Mapillary's viewer): the boxed evidence photo, the next and previous findings, and the credit.
export default function PhotoWindow({ finding, list, items, onStep, onClose }: Props) {
  const evidence = finding.picked.item.evidence;
  if (!evidence) return null;
  const i = list.findIndex((f) => f.key === finding.key);
  const target = items.find((it) => it.id === finding.item)?.target ?? finding.item;
  return (
    <section className="photo-window" aria-label="Photo of the selected finding">
      <div className="photo-window-head">
        <strong>{itemName(finding.item, items)}</strong>
        <span className="hint">{i >= 0 ? `${i + 1} of ${list.length}` : ""}</span>
        <span className="card-arrows">
          <button type="button" disabled={i <= 0} onClick={() => onStep(list[i - 1])} aria-label="Previous photo">‹</button>
          <button type="button" disabled={i < 0 || i >= list.length - 1} onClick={() => onStep(list[i + 1])}
            aria-label="Next photo">›</button>
          <button type="button" onClick={onClose} aria-label="Close the photo">×</button>
        </span>
      </div>
      <EvidencePhoto evidence={evidence} box={finding.box} boxLabel={`the ${target}`} large
        alt={`Street photo: ${finding.title}`} />
      <PhotoCredit evidence={evidence} />
    </section>
  );
}
