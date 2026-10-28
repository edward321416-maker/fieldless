import { useState } from "react";
import { photoMonth } from "./format";
import { isLight } from "./items";
import type { Evidence } from "./types";

// The official Mapillary logo (press kit v0.0.4, the light-background version, outer margin trimmed); the word if it fails to load.
export function MapillaryMark({ alt = "Mapillary" }: { alt?: string }) {
  const [missing, setMissing] = useState(false);
  if (missing) return alt ? <span className="mapillary-word">Mapillary</span> : null;
  return <img src="/mapillary-logo.png" alt={alt} className="mapillary-logo" onError={() => setMissing(true)} />;
}

// Creator, date, Mapillary logo and link, licence and what was changed: under every photo (Mapillary terms).
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

// How far to enlarge and move the photo so the box fills about 80% of the frame (at most 4 times), as a CSS
// transform with origin 0 0: a point p lands at s * p + t.
export function zoomTransform(box: number[]): string {
  const [x1, y1, x2, y2] = box.map((v) => v / 1000);
  const s = Math.min(4, Math.max(1, 0.8 / Math.max(x2 - x1, y2 - y1, 0.01)));
  const cx = (x1 + x2) / 2;
  const cy = (y1 + y2) / 2;
  const tx = Math.min(0, Math.max(1 - s, 0.5 - s * cx)) * 100; // keep the photo covering the frame
  const ty = Math.min(0, Math.max(1 - s, 0.5 - s * cy)) * 100;
  return `translate(${tx}%, ${ty}%) scale(${s})`;
}

interface Props {
  evidence: Evidence;
  box: number[] | null;
  boxLabel: string; // "the pole"
  colour: string; // the item's colour, as on the map
  alt: string;
  large?: boolean;
}

// The tag goes above the box, or inside it at the top when the box touches the photo's top edge, and it ends at
// the box's right edge when the box is in the right part of the photo, so it is never cut off.
export function tagPlace(box: number[]): { inside: boolean; right: boolean } {
  return { inside: box[1] < 60, right: box[0] > 600 };
}

// The evidence photo with the model's box drawn on it, and "Zoom to box" for an object photographed from afar
// (docs/ui-rules.md U5).
export function EvidencePhoto({ evidence, box, boxLabel, colour, alt, large = false }: Props) {
  const [zoom, setZoom] = useState(false);
  const zoomed = zoom && box !== null;
  return (
    <figure className={`photo${large ? " photo-large" : ""}`}>
      <div className="photo-frame">
        <div className="photo-inner" style={zoomed ? { transform: zoomTransform(box!) } : undefined}>
          <img src={evidence.url} alt={alt} />
          {box && (
            <div className="photo-box" style={{ left: `${box[0] / 10}%`, top: `${box[1] / 10}%`,
              width: `${(box[2] - box[0]) / 10}%`, height: `${(box[3] - box[1]) / 10}%`, borderColor: colour }}>
              {!zoomed && (
                <span className={`photo-box-label${tagPlace(box).inside ? " tag-inside" : ""}${tagPlace(box).right ? " tag-right" : ""}`}
                  style={{ background: colour, color: isLight(colour) ? "#111827" : "#ffffff" }}>
                  {boxLabel}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="photo-tools">
        {box ? (
          <button type="button" aria-pressed={zoom} onClick={() => setZoom((z) => !z)}>
            {zoom ? "Show the whole photo" : "Zoom to box"}
          </button>
        ) : (
          <span className="hint small">The model did not mark where it is.</span>
        )}
        <a href={evidence.url} target="_blank" rel="noreferrer">Open full size</a>
      </div>
    </figure>
  );
}
