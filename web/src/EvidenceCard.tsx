import { useState } from "react";
import { STATIC_DEMO } from "./api";
import { labelText, photoMonth, reasonText } from "./format";
import type { Picked } from "./MapView";
import type { Evidence } from "./types";

// The official Mapillary logo (press kit v0.0.4, the light-background version, outer margin trimmed); the word if it fails to load.
export function MapillaryMark({ alt = "Mapillary" }: { alt?: string }) {
  const [missing, setMissing] = useState(false);
  if (missing) return alt ? <span className="mapillary-word">Mapillary</span> : null;
  return <img src="/mapillary-logo.png" alt={alt} className="mapillary-logo" onError={() => setMissing(true)} />;
}

export function PhotoCredit({ evidence }: { evidence: Evidence }) {
  return (
    <p className="credit">
      Photo by {evidence.creator ?? "a Mapillary contributor"}, {photoMonth(evidence.captured_at)} ·{" "}
      <a href={evidence.link} target="_blank" rel="noreferrer">
        <MapillaryMark alt="" /> View on Mapillary
      </a>{" "}
      · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noreferrer">{evidence.license}</a>
      {" "}· {evidence.blurred ? "Edited: people and plates blurred, resized" : "Edited: resized"}
    </p>
  );
}

interface Props {
  picked: Picked;
  onClose: () => void;
  onReview: () => void;
}

export default function EvidenceCard({ picked, onClose, onReview }: Props) {
  const item = picked.item;
  const title = picked.kind === "entrance" ? picked.item.name || "Unnamed entrance" : labelText(picked.item.label);
  const summary = picked.kind === "entrance"
    ? `${picked.item.name || "Entrance"}: ${picked.item.label.text}`
    : picked.item.label.text;
  return (
    <section className="card" aria-labelledby="card-title">
      <div className="card-head">
        <h2 id="card-title">{title}</h2>
        <button type="button" onClick={onClose} aria-label="Close">×</button>
      </div>
      {item.evidence ? (
        <>
          <img src={item.evidence.url} alt={`Street photo: ${summary}`} className="evidence" />
          <PhotoCredit evidence={item.evidence} />
        </>
      ) : (
        <p className="hint">No evidence photo for this item.</p>
      )}
      {item.old_photo && <p className="badge">Photo over 3 years old ({photoMonth(item.captured_at)})</p>}
      {picked.kind === "entrance" ? (
        <dl className="stats">
          <dt>Steps</dt>
          <dd>{labelText(picked.item.label)}</dd>
          <dt>Stairs outlined by SAM 3.1</dt>
          <dd>{picked.item.stairs_detected === null ? "not checked" : picked.item.stairs_detected ? "yes" : "no"}</dd>
        </dl>
      ) : (
        <p>
          {picked.item.durable
            ? "A defect of the paving itself, so it is likely still there today."
            : `Seen in the photo of ${photoMonth(picked.item.captured_at)}; movable objects may be gone now.`}{" "}
          {picked.item.frame_start !== null && picked.item.frame_end !== null
            ? `Photos ${picked.item.frame_start}–${picked.item.frame_end} of the walk.`
            : ""}
        </p>
      )}
      {item.review_reasons.length > 0 && (
        <>
          <h3>Why it needs a check</h3>
          <ul>
            {item.review_reasons.map((r) => <li key={r}>{reasonText(r)}</li>)}
          </ul>
        </>
      )}
      {item.review_status === "needs_review" && !STATIC_DEMO && (
        <button type="button" className="primary" onClick={onReview}>Review now</button>
      )}
      {item.review_status === "verified" && <p className="badge">Checked by a surveyor</p>}
    </section>
  );
}
