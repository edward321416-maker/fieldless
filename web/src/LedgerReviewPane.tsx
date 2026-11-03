import { useContext, useReducer, type Dispatch } from "react";
import { LedgerReviewContext } from "./LedgerReviewContext";
import { colourOf } from "./items";
import {
  EMPTY_DRAFT, buildJournal, decisionFromDraft, draftProblems, editableNames, exportFiles, has, photoDay, proposalChoices, reviewSignature,
  reviewableRows, sourceFile, sourceOf, validateJournal, type Draft, type ExportFile, type LedgerCheck,
} from "./ledgerReview";
import { EvidencePhoto, PhotoCredit } from "./Photo";
import type { AssetRow, LedgerDecision, LedgerJournal, Scan } from "./types";

export interface ReviewState {
  reviewer: string;
  drafts: Record<string, Draft>; // by the source's review key
  decisions: Record<string, LedgerDecision>;
  index: number;
}

export type ReviewAction =
  | { type: "reviewer"; value: string }
  | { type: "draft"; key: string; patch: Partial<Draft> }
  | { type: "record"; row: AssetRow; now: string }
  | { type: "remove"; key: string }
  | { type: "go"; index: number; count: number };

// Nothing is chosen and nothing is recorded: a review begins empty.
export function initialReviewState(): ReviewState {
  return { reviewer: "", drafts: {}, decisions: {}, index: 0 };
}

export function reviewReducer(state: ReviewState, action: ReviewAction): ReviewState {
  switch (action.type) {
    case "reviewer":
      return { ...state, reviewer: action.value };
    case "draft": {
      // Any edit takes the row out of the journal until it is recorded again: what is shown is what would be downloaded.
      const { [action.key]: _dropped, ...decisions } = state.decisions;
      return { ...state, decisions, drafts: { ...state.drafts, [action.key]: { ...EMPTY_DRAFT, ...state.drafts[action.key], ...action.patch } } };
    }
    case "record": {
      const key = sourceOf(action.row)?.review_key;
      if (!key) return state;
      const made = decisionFromDraft(action.row, state.drafts[key] ?? EMPTY_DRAFT, state.reviewer, action.now);
      return "decision" in made ? { ...state, decisions: { ...state.decisions, [key]: made.decision } } : state;
    }
    case "remove": {
      const { [action.key]: _dropped, ...decisions } = state.decisions;
      return { ...state, decisions };
    }
    case "go":
      return { ...state, index: Math.max(0, Math.min(action.index, action.count - 1)) };
  }
}

// The decisions recorded for rows that are in this list now, in the list's order. A decision whose row has gone is not exported.
export function journalOf(rows: AssetRow[], scanId: number, state: ReviewState): LedgerJournal {
  const decisions: LedgerDecision[] = [];
  for (const r of reviewableRows(rows)) {
    const d = state.decisions[sourceOf(r)!.review_key];
    if (d) decisions.push(d);
  }
  return buildJournal(scanId, decisions);
}

export type DownloadKind = "journal" | "changes" | "observations" | "source";
const KINDS: DownloadKind[] = ["journal", "changes", "observations"];

// Builds one of the three files from what is recorded and hands it over. It throws when there is nothing recorded or the
// package does not pass the checks, so a file never carries something the backend would reject.
export function runDownload(kind: DownloadKind, check: LedgerCheck, scanId: number, state: ReviewState, save: (file: ExportFile) => void): void {
  if (kind === "source") return save(sourceFile(check, scanId));
  const rows = check.rows;
  const journal = journalOf(rows, scanId, state);
  if (journal.decisions.length === 0) throw new Error("There is nothing to download: no decision has been recorded.");
  save(exportFiles(rows, journal, scanId)[KINDS.indexOf(kind)]);
}

// A file made in this browser and saved by the browser: it is not uploaded.
export function saveFile(file: ExportFile): void {
  const url = URL.createObjectURL(new Blob([file.text], { type: `${file.mime};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const IDENTITY_CHOICES: [string, string][] = [["confirmed", "Yes, the same facility"], ["uncertain", "Not sure"], ["different", "No, a different object"]];
const EVIDENCE_CHOICES: [string, string][] = [["clear", "Clear enough to rely on"], ["unclear", "Not clear enough"]];
const DECISION_CHOICES: [string, string][] = [["hold", "Hold: decide later"], ["observe", "Observe: record that it was seen in this photo"],
  ["change", "Change: propose a new value (not applied)"]];

function Choice({ name, value, label, checked, disabled, onPick }: { name: string; value: string; label: string; checked: boolean;
  disabled?: boolean; onPick: () => void }) {
  return (
    <label className="ledger-choice">
      <input type="radio" name={name} value={value} checked={checked} disabled={disabled} onChange={onPick} />
      <span>{label}</span>
    </label>
  );
}

interface ViewProps {
  scan: Scan;
  city: string;
  check: LedgerCheck; // the whole list, as given
  state: ReviewState;
  dispatch: Dispatch<ReviewAction>;
  showFor?: (row: AssetRow) => (() => void) | undefined;
  save: (file: ExportFile) => void;
}

// The review for the ledger: one record at a time, with its photo, and the files to keep. It changes nothing but this
// page's own memory: no request is made and no city record is touched.
export function LedgerReviewView({ scan, city, check: list, state, dispatch, showFor, save }: ViewProps) {
  const rows = list.rows;
  const queue = reviewableRows(rows);
  const without = rows.filter((r) => r.city_record && !queue.includes(r)).length;
  const journal = journalOf(rows, scan.id, state);
  const check = validateJournal(rows, journal, scan.id);
  const keyOf = (r: AssetRow) => sourceOf(r)!.review_key;
  const reviewed = queue.filter((r) => state.decisions[keyOf(r)]).length;
  const index = Math.min(state.index, Math.max(0, queue.length - 1));
  const current = queue[index];
  const files = (kind: DownloadKind, enabled: boolean, label: string) => (
    <button key={kind} type="button" className="ledger-download" disabled={!enabled}
      onClick={() => runDownload(kind, list, scan.id, state, save)}>{label}</button>
  );
  const nextOpen = queue.findIndex((r, i) => i > index && !state.decisions[keyOf(r)]);
  const status = (
    <p className="ledger-status">
      <strong>Prepared changes · not applied.</strong> Nothing is sent to a server and no {city} record is changed. The files you download
      are what a city office could check against its own list.
    </p>
  );
  return (
    <section className="ledger-review" aria-labelledby="ledger-review-title">
      <h3 id="ledger-review-title">Review for ledger</h3>
      {status}
      <p className="hint small">
        Your review is kept only on this page. Download the files to keep it: reloading the page or opening another scan clears it. The
        reviewer name is typed by you. It is not a verified municipal approval.
      </p>
      {without > 0 && (
        <p className="hint small">{without} {without === 1 ? "record has" : "records have"} no source snapshot (an older list), so {without === 1 ? "it" : "they"} cannot be reviewed for the ledger.</p>
      )}
      {queue.length === 0 ? (
        <p className="hint">This list has no source snapshot of the city's records, so nothing here can be reviewed for the ledger. Open the survey again to get a current list.</p>
      ) : (
        <>
          <dl className="ledger-counts" aria-live="polite">
            <div><dt>Changes</dt><dd>{check.changes.length}</dd></div>
            <div><dt>Observations</dt><dd>{check.observations.length}</dd></div>
            <div><dt>Holds</dt><dd>{check.holds.length}</dd></div>
            <div><dt>Not reviewed</dt><dd>{queue.length - reviewed}</dd></div>
          </dl>
          <div className="ledger-field">
            <label htmlFor="ledger-reviewer">Reviewer name (typed by you, not verified)</label>
            <input id="ledger-reviewer" type="text" autoComplete="off" maxLength={200} value={state.reviewer}
              onChange={(e) => dispatch({ type: "reviewer", value: e.target.value })} />
          </div>
          <div className="ledger-nav">
            <button type="button" disabled={index === 0} onClick={() => dispatch({ type: "go", index: index - 1, count: queue.length })}>Previous</button>
            <span>Row {index + 1} of {queue.length}</span>
            <button type="button" disabled={index >= queue.length - 1} onClick={() => dispatch({ type: "go", index: index + 1, count: queue.length })}>Next</button>
            <button type="button" disabled={nextOpen < 0} onClick={() => dispatch({ type: "go", index: nextOpen, count: queue.length })}>Next not reviewed</button>
          </div>
          <RecordCard key={keyOf(current)} row={current} uid={`ledger-${index}`} scan={scan} state={state} dispatch={dispatch}
            onShow={showFor?.(current)} />
          <div className="ledger-export">
            <h4>Files to keep</h4>
            {!check.ok && <p className="hint small" role="alert">The journal is not valid: {check.errors.join(" ")}</p>}
            <div className="ledger-downloads">
              {files("journal", journal.decisions.length > 0, "Download journal (.json)")}
              {files("changes", check.changes.length > 0, "Download changes (.csv)")}
              {files("observations", check.observations.length > 0, "Download observations (.csv)")}
              {files("source", true, "Download source list (.json)")}
            </div>
            <p className="hint small">
              The journal is checked against this exact list: download both and keep them together. The journal holds every decision, the holds too. The changes file holds only recorded changes, the observations file only
              photo observations. A hold appears in neither.
            </p>
          </div>
        </>
      )}
    </section>
  );
}

function RecordCard({ row, uid, scan, state, dispatch, onShow }: { row: AssetRow; uid: string; scan: Scan; state: ReviewState;
  dispatch: Dispatch<ReviewAction>; onShow?: () => void }) {
  const source = sourceOf(row)!;
  const key = source.review_key;
  const draft = { ...EMPTY_DRAFT, ...state.drafts[key] };
  const recorded = state.decisions[key];
  const problems = draftProblems(row, draft, state.reviewer);
  const patch = (p: Partial<Draft>) => dispatch({ type: "draft", key, patch: p });
  const editable = editableNames(source);
  const choices = draft.field ? proposalChoices(source, draft.field) : [];
  const asked = draft.field && has(source.editable_fields, draft.field) ? source.editable_fields[draft.field] : null;
  const day = photoDay(row);
  const needsNeighbours = source.identity_warnings.length > 0;
  return (
    <article className="ledger-card">
      {row.evidence && (
        <EvidencePhoto evidence={row.evidence} box={row.box} boxLabel={row.asset.toLowerCase()}
          colour={colourOf(row.item, scan.items)} alt={`Street photo: ${row.asset}`} />
      )}
      {row.evidence && <PhotoCredit evidence={row.evidence} />}
      {!row.evidence && <p className="hint small">This row has no photo of its own, so it can be held but not observed or changed.</p>}
      <header className="ledger-record">
        <h4>{source.record_id}</h4>
        <p>{row.asset} · <code>{source.layer}</code></p>
        <p>{row.city_location || row.city_record}</p>
        <p>Position {row.lat.toFixed(5)}, {row.lng.toFixed(5)} ({row.position_accuracy})</p>
        {onShow && <button type="button" className="link-button" onClick={onShow}>Show on the map</button>}
      </header>
      <dl className="ledger-facts">
        <div><dt>City says</dt><dd>{row.city_says}</dd></div>
        <div><dt>Photo shows</dt><dd>{row.photo_shows}</dd></div>
        <div><dt>City date</dt><dd>{row.city_date || "none"}</dd></div>
        <div><dt>Photo day</dt><dd>{day || "unknown"}</dd></div>
        {Object.entries(source.editable_fields).filter(([f]) => editable.includes(f)).map(([f, v]) => (
          <div key={f}><dt>{f}</dt><dd>now {String(v.before)}</dd></div>
        ))}
      </dl>
      <p className="hint small">The photo day is when the picture was taken. It is not an inspection date, and nothing here changes the city's dates.</p>
      <p className="ledger-state" aria-live="polite">
        {recorded
          ? <>Recorded: {recorded.decision}{recorded.field ? ` ${recorded.field} ${String(source.editable_fields[recorded.field].before)} → ${recorded.proposed_value}` : ""} (by {recorded.reviewer})</>
          : "Not reviewed"}
      </p>
      {needsNeighbours && (
        <div className="ledger-warning" role="note">
          <strong>Neighboring records</strong>
          <ul>{source.identity_warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          <label className="ledger-choice">
            <input type="checkbox" checked={draft.neighbors} onChange={(e) => patch({ neighbors: e.target.checked })} />
            <span>I checked the neighboring records, and this photo shows this record's facility.</span>
          </label>
        </div>
      )}
      <fieldset className="ledger-group">
        <legend>Is the photo showing the same facility as this record?</legend>
        {IDENTITY_CHOICES.map(([v, label]) => (
          <Choice key={v} name={`${uid}-identity`} value={v} label={label} checked={draft.identity === v} onPick={() => patch({ identity: v as Draft["identity"] })} />
        ))}
      </fieldset>
      <fieldset className="ledger-group">
        <legend>Can you rely on the photo?</legend>
        {EVIDENCE_CHOICES.map(([v, label]) => (
          <Choice key={v} name={`${uid}-evidence`} value={v} label={label} checked={draft.evidence === v} onPick={() => patch({ evidence: v as Draft["evidence"] })} />
        ))}
      </fieldset>
      <fieldset className="ledger-group">
        <legend>Decision</legend>
        {DECISION_CHOICES.map(([v, label]) => (
          <Choice key={v} name={`${uid}-decision`} value={v} label={label} checked={draft.decision === v}
            disabled={v === "change" && editable.length === 0}
            onPick={() => patch({ decision: v as Draft["decision"], ...(v === "change" ? {} : { field: "", proposed: "" }) })} />
        ))}
        {editable.length === 0 && <p className="hint small">This record has no attribute a photo can change. You can observe it or hold it.</p>}
      </fieldset>
      {draft.decision === "change" && editable.length > 0 && (
        <fieldset className="ledger-group">
          <legend>Proposed change</legend>
          <div className="ledger-field">
            <label htmlFor={`${uid}-field`}>Field</label>
            <select id={`${uid}-field`} value={draft.field} onChange={(e) => patch({ field: e.target.value, proposed: "" })}>
              <option value="">Choose a field</option>
              {editable.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </div>
          {asked && (
            <>
              <p className="ledger-question">{asked.question}</p>
              {asked.suggested && <p className="hint small">A model suggested "{asked.suggested}". That is a suggestion; choose for yourself.</p>}
              {choices.map((v) => (
                <Choice key={v} name={`${uid}-proposed`} value={v} label={`${v} (the record says ${String(asked.before)})`}
                  checked={draft.proposed === v} onPick={() => patch({ proposed: v })} />
              ))}
            </>
          )}
        </fieldset>
      )}
      <div className="ledger-field">
        <label htmlFor={`${uid}-note`}>Reason (required)</label>
        <textarea id={`${uid}-note`} rows={3} maxLength={2000} value={draft.note} onChange={(e) => patch({ note: e.target.value })} />
      </div>
      {problems.length > 0 && !recorded && (
        <ul className="ledger-problems" aria-live="polite">
          {problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      <div className="ledger-actions">
        <button type="button" className="action-export-btn" disabled={problems.length > 0}
          onClick={() => dispatch({ type: "record", row, now: new Date().toISOString() })}>Record decision</button>
        {recorded && <button type="button" className="link-button" onClick={() => dispatch({ type: "remove", key })}>Remove this decision</button>}
      </div>
    </article>
  );
}

interface PaneProps {
  scan: Scan;
  city: string;
  check: LedgerCheck;
  showFor?: (row: AssetRow) => (() => void) | undefined;
}

// A review belongs to one scan and one set of source snapshots: when either changes it starts again, empty.
export default function LedgerReviewPane({ scan, city, check, showFor }: PaneProps) {
  return <Session key={reviewSignature(scan.id, check.rows)} scan={scan} city={city} check={check} showFor={showFor} />;
}

function Session(props: PaneProps) {
  const ledger = useContext(LedgerReviewContext);
  const [localState, localDispatch] = useReducer(reviewReducer, undefined, initialReviewState);
  const signature = reviewSignature(props.scan.id, props.check.rows);
  const state = ledger?.session?.signature === signature ? ledger.session.state : localState;
  const dispatch: Dispatch<ReviewAction> = ledger ? (action) => {
    ledger.setSession((previous) => ({ scanId: props.scan.id, signature, open: true,
      state: reviewReducer(previous?.signature === signature ? previous.state : initialReviewState(), action) }));
  } : localDispatch;
  return <LedgerReviewView {...props} state={state} dispatch={dispatch} save={saveFile} />;
}
