import { colourOf, itemName, type Finding } from "./items";
import { EvidencePhoto, PhotoCredit } from "./Photo";
import type { ItemMeta } from "./types";

interface Props {
  finding: Finding;
  items: ItemMeta[];
  onClose: () => void;
}

// The selected finding's photo over the map, so the map and the photo are seen together (docs/specs ui-redesign
// 3.3, after Mapillary's viewer): the boxed evidence photo and its credit. Stepping lives in the card alone
// (docs/specs/2026-10-29-cleanup-design.md 3).
export default function PhotoWindow({ finding, items, onClose }: Props) {
  const evidence = finding.picked.item.evidence;
  if (!evidence) return null;
  const target = items.find((it) => it.id === finding.item)?.target ?? finding.item;
  return (
    <section className="photo-window" aria-label="Photo of the selected finding">
      <div className="photo-window-head">
        <strong>{itemName(finding.item, items)}</strong>
        <button type="button" className="photo-close" onClick={onClose} aria-label="Close the photo">×</button>
      </div>
      <EvidencePhoto evidence={evidence} box={finding.box} boxLabel={`the ${target}`} large
        colour={colourOf(finding.item, items)}
        alt={`Street photo: ${finding.title}`} />
      <PhotoCredit evidence={evidence} />
    </section>
  );
}
