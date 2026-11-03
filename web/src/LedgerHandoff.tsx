// The handoff of a review: a read-only preview of what the prepared package proposes, and the one file to take away.
// Nothing is sent anywhere. The preview comes from the journal and the list the reviewer worked on, so it is the proposal, not a check
// of the city's current files (that is the local command's job: docs/ledger-workflow.md).
import { useState } from "react";
import "./LedgerHandoff.css";
import { buildReviewPackage, previewOf, PACKAGE_GUIDE_URL } from "./ledgerPackage";
import type { ExportFile, LedgerCheck } from "./ledgerReview";
import type { LedgerJournal } from "./types";

const COMMAND = "python -m eval.ledger_apply --package PACKAGE.json --input-dir CURRENT_LAYERS --output-dir NEW_COPY --city CITY_NAME --dry-run";

// A file made in this browser and saved by the browser: it is not uploaded.
function saveInBrowser(file: ExportFile): void {
  const url = URL.createObjectURL(new Blob([file.text], { type: `${file.mime};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Throws, and saves nothing, when no decision is recorded or the journal does not pass the review rules.
export function downloadPackage(check: LedgerCheck, journal: LedgerJournal, scanId: number, save: (file: ExportFile) => void = saveInBrowser): void {
  if (journal.decisions.length === 0) throw new Error("There is nothing to put in a package: no decision has been recorded.");
  save(buildReviewPackage(check, journal, scanId));
}

interface Props {
  check: LedgerCheck;
  journal: LedgerJournal;
  scanId: number;
  save?: (file: ExportFile) => void;
}

export default function LedgerHandoff({ check, journal, scanId, save }: Props) {
  const [failure, setFailure] = useState("");
  const preview = previewOf(check, journal, scanId);
  const empty = preview.ok && preview.decisions === 0;
  const onDownload = () => {
    try {
      downloadPackage(check, journal, scanId, save);
      setFailure("");
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <section className="handoff" aria-labelledby="handoff-title">
      <h3 id="handoff-title">Handoff package</h3>
      <p>
        One file with the list you reviewed, your journal and both CSV files. It is a prepared proposal for the city's records keeper, not an
        approval. Reviewer names in it are self-asserted, not authenticated municipal approvals, and the file is not signed.
      </p>
      {!preview.ok && (
        <ul className="handoff-problems" role="alert">
          {preview.errors.map((e, n) => <li key={n}>{e}</li>)}
        </ul>
      )}
      {preview.ok && (
        <>
          <dl className="handoff-counts" aria-label="What the package holds">
            <div><dt>Recorded decisions</dt><dd>{preview.decisions}</dd></div>
            <div><dt>Proposed changes</dt><dd>{preview.changes.length}</dd></div>
            <div><dt>Observations</dt><dd>{preview.observations}</dd></div>
            <div><dt>On hold</dt><dd>{preview.holds}</dd></div>
          </dl>
          {empty && <p>No decision has been recorded yet, so there is nothing to put in a package.</p>}
          {preview.changes.length > 0 && (
            <>
              <h4>Proposed changes: before and after</h4>
              <ul className="handoff-changes">
                {preview.changes.map((c) => (
                  <li key={`${c.layer}:${c.record_id}:${c.field}`} className="handoff-change">
                    <p className="handoff-change-head">{c.record_id} ({c.item}), field {c.field}</p>
                    <dl className="handoff-pair">
                      <div><dt>Before</dt><dd>{c.before}</dd></div>
                      <div className="handoff-after"><dt>After</dt><dd>{c.after}</dd></div>
                    </dl>
                    <p className="handoff-change-meta">Photo of {c.photo_observed_on}. Reviewer (self-asserted): {c.reviewer}</p>
                  </li>
                ))}
              </ul>
            </>
          )}
          {preview.observations + preview.holds > 0 && (
            <p>
              {preview.observations} observation{preview.observations === 1 ? "" : "s"} record what a dated photo showed and change no value;{" "}
              {preview.holds} hold{preview.holds === 1 ? "" : "s"} stay open for later. Neither is applied to a record.
            </p>
          )}
        </>
      )}
      <div className="handoff-note">
        <p>
          This is a preview of the proposal prepared in this browser. It does not check the city's current GeoJSON, which may have changed
          since the scan. To check the package against your current files, run the local command first. A dry run writes nothing:
        </p>
        <pre className="handoff-code">{COMMAND}</pre>
      </div>
      {failure && <p className="handoff-problems" role="alert">{failure}</p>}
      <div className="handoff-actions">
        <button type="button" className="handoff-download" disabled={!preview.ok || empty} onClick={onDownload}>Download review package</button>
        <a className="handoff-guide" href={PACKAGE_GUIDE_URL} target="_blank" rel="noopener noreferrer">How to apply it: the public guide</a>
      </div>
    </section>
  );
}
