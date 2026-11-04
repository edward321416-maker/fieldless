import { useContext, useEffect, useReducer, useRef, useState, type Dispatch } from "react";
import LedgerHandoff from "./LedgerHandoff";
import { LedgerReviewContext } from "./LedgerReviewContext";
import { colourOf } from "./items";
import { IMPORT_MAX_CHARS, applyImport, planImport, type ImportPlan } from "./ledgerImport";
import { readReviewPackage } from "./ledgerPackage";
import { DEFAULT_QUEUE, FILTERS, FILTER_LABELS, QUERY_MAX, filterCounts, itemsOf, isFilter, queueRows, type QueueSettings } from "./ledgerQuery";
import { emptyState, hasMeaningfulWork, recoveryStore, type RecoverableState } from "./ledgerRecovery";
import {
  EMPTY_DRAFT, buildJournal, decisionFromDraft, draftProblems, editableNames, exportFiles, has, photoDay, proposalChoices, reviewSignature,
  reviewableRows, sourceFile, sourceOf, validateJournal, type Draft, type ExportFile, type LedgerCheck,
} from "./ledgerReview";
import { clearSaved, needsLeaveWarning, startSession, syncRecovery, type Notice, type StorageProblem } from "./ledgerSession";
import { EvidencePhoto, PhotoCredit } from "./Photo";
import { actionLabel } from "./reviewLabels";
import type { AssetRow, LedgerDecision, LedgerJournal, Scan } from "./types";

// What a review is made of: the reviewer's words and choices, and where they are in the list. All of it can be saved and restored.
export type ReviewState = RecoverableState;

export type ReviewAction =
  | { type: "reviewer"; value: string }
  | { type: "draft"; key: string; patch: Partial<Draft> }
  | { type: "record"; row: AssetRow; now: string }
  | { type: "remove"; key: string }
  | { type: "select"; key: string }
  | { type: "queue"; patch: Partial<QueueSettings> }
  | { type: "replace"; state: ReviewState };

// Nothing is chosen and nothing is recorded: a review begins empty.
export function initialReviewState(): ReviewState {
  return emptyState();
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
    case "select":
      return { ...state, selected: action.key };
    case "queue": {
      // A new search or filter starts at the best match: the place held before is not kept.
      const patch = action.patch.query === undefined ? action.patch : { ...action.patch, query: action.patch.query.slice(0, QUERY_MAX) };
      return { ...state, selected: "", queue: { ...state.queue, ...patch } };
    }
    case "replace":
      return action.state;
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

// What the page needs from its session: the notices, the browser's storage problem, a pending import and the actions on them.
export interface SessionUi {
  notices: Notice[];
  problem: StorageProblem;
  plan: ImportPlan | null;
  askingClear: boolean;
  onFile: (file: File) => void;
  onConfirmImport: () => void;
  onCancelImport: () => void;
  onAskClear: () => void;
  onKeepSaved: () => void;
  onClear: (everything: boolean) => void;
}

const NOOP = () => {};
const NO_UI: SessionUi = { notices: [], problem: "", plan: null, askingClear: false, onFile: NOOP, onConfirmImport: NOOP, onCancelImport: NOOP,
  onAskClear: NOOP, onKeepSaved: NOOP, onClear: NOOP };
const PREVIEW_LINES = 30;

interface ViewProps {
  scan: Scan;
  city: string;
  check: LedgerCheck; // the whole list, as given
  state: ReviewState;
  dispatch: Dispatch<ReviewAction>;
  showFor?: (row: AssetRow) => (() => void) | undefined;
  save: (file: ExportFile) => void;
  ui?: SessionUi;
}

function Notices({ ui }: { ui: SessionUi }) {
  return (
    <>
      {ui.notices.map((n) => <p key={n.id} role="status" className={`ledger-notice ledger-notice-${n.tone}`}>{n.text}</p>)}
      {ui.problem && (
        <p role="alert" className="ledger-alert">
          {`Your work could not be saved in this browser (${ui.problem === "full" ? "its storage is full" : "storage is unavailable or blocked"}). It lasts only while this page is open. Download the files to keep it.`}
        </p>
      )}
    </>
  );
}

function SavedWork({ ui }: { ui: SessionUi }) {
  return (
    <div className="ledger-saved">
      <p className="hint small">
        Your work is saved in this browser while you review: the reviewer name, your notes, drafts and recorded decisions. Notes and names are not sent to a
        server, and anyone who can open this browser profile can read them. Download the files to keep a copy or to move to another browser.
      </p>
      {ui.askingClear ? (
        <div className="ledger-clear" role="group" aria-label="Clear saved work">
          <p>This removes the saved reviewer name, drafts and decisions from this browser and empties the review on screen. Download the files first if you need them.</p>
          <div className="ledger-actions">
            <button type="button" className="ledger-download" onClick={() => ui.onClear(false)}>Clear this scan</button>
            <button type="button" className="ledger-download" onClick={() => ui.onClear(true)}>Clear all scans</button>
            <button type="button" className="ledger-download" onClick={ui.onKeepSaved}>Keep my work</button>
          </div>
        </div>
      ) : (
        <button type="button" className="link-button" onClick={ui.onAskClear}>Clear saved work…</button>
      )}
    </div>
  );
}

function ImportBox({ ui }: { ui: SessionUi }) {
  const { plan } = ui;
  return (
    <div className="ledger-import">
      <label htmlFor="ledger-import">Import a journal or review package (.json)</label>
      <input id="ledger-import" type="file" accept=".json,application/json"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) ui.onFile(file);
        }} />
      <p className="hint small">
        A file is checked against this exact list before anything changes, and you see a preview first. A journal holds recorded decisions only, never drafts.
      </p>
      {plan && !plan.ok && (
        <div className="ledger-preview" role="alert">
          <h4>Import rejected</h4>
          <ul>{plan.errors.slice(0, 10).map((e) => <li key={e}>{e}</li>)}</ul>
          <p>Nothing was imported and your current work is unchanged.</p>
          <button type="button" className="ledger-download" onClick={ui.onCancelImport}>Dismiss</button>
        </div>
      )}
      {plan && plan.ok && (
        <section className="ledger-preview" aria-labelledby="ledger-preview-title">
          <h4 id="ledger-preview-title">Import preview</h4>
          <p>
            {`${plan.kind === "package" ? "Review package" : "Journal"} for this scan: ${plan.counts.changes} changes, ${plan.counts.observations} observations, ${plan.counts.holds} holds.`}
          </p>
          <ul>
            {plan.lines.slice(0, PREVIEW_LINES).map((l) => <li key={l.key}>{l.text}</li>)}
            {plan.lines.length > PREVIEW_LINES && <li>{`and ${plan.lines.length - PREVIEW_LINES} more`}</li>}
          </ul>
          {plan.replaces.decisions + plan.replaces.drafts > 0 && (
            <p role="alert">
              {`Replacing discards what you have now: ${plan.replaces.decisions} recorded ${plan.replaces.decisions === 1 ? "decision" : "decisions"} and ${plan.replaces.drafts} ${plan.replaces.drafts === 1 ? "draft" : "drafts"} that no decision stands for. Cancel keeps it.`}
            </p>
          )}
          {plan.notes.map((n) => <p key={n} className="hint small">{n}</p>)}
          <div className="ledger-actions">
            <button type="button" className="action-export-btn" onClick={ui.onConfirmImport}>
              {plan.replaces.decisions + plan.replaces.drafts > 0 ? "Replace my work with this file" : "Import this file"}
            </button>
            <button type="button" className="ledger-download" onClick={ui.onCancelImport}>Cancel</button>
          </div>
        </section>
      )}
    </div>
  );
}

// The review for the ledger: one record at a time, with its photo, and the files to keep. It changes nothing but this
// page's own memory and this browser's saved copy: no request is made and no city record is touched.
export function LedgerReviewView({ scan, city, check: list, state, dispatch, showFor, save, ui = NO_UI }: ViewProps) {
  const rows = list.rows;
  const queue = reviewableRows(rows);
  const without = rows.filter((r) => r.city_record && !queue.includes(r)).length;
  const journal = journalOf(rows, scan.id, state);
  const check = validateJournal(rows, journal, scan.id);
  const keyOf = (r: AssetRow) => sourceOf(r)!.review_key;
  const reviewed = queue.filter((r) => state.decisions[keyOf(r)]).length;
  const q = state.queue;
  const visible = queueRows(queue, state.decisions, q);
  const current = visible.find((r) => keyOf(r) === state.selected) ?? visible[0];
  const at = current ? visible.indexOf(current) : -1;
  const items = itemsOf(queue);
  const counts = filterCounts(queue, state.decisions);
  const search = q.query.trim() !== "";
  const go = (r: AssetRow | undefined) => r && dispatch({ type: "select", key: keyOf(r) });
  const nextOpen = visible.find((r, i) => i > at && !state.decisions[keyOf(r)]);
  const files = (kind: DownloadKind, enabled: boolean, label: string) => (
    <button key={kind} type="button" className="ledger-download" disabled={!enabled}
      onClick={() => runDownload(kind, list, scan.id, state, save)}>{label}</button>
  );
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
      <Notices ui={ui} />
      <SavedWork ui={ui} />
      <p className="hint small">
        The reviewer name is typed by you. It is not a verified municipal approval.
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
          <div className="ledger-queue">
            <div className="ledger-field">
              <label htmlFor="ledger-search">Find a record (ID or place)</label>
              <input id="ledger-search" type="search" autoComplete="off" maxLength={QUERY_MAX} value={q.query}
                onChange={(e) => dispatch({ type: "queue", patch: { query: e.target.value } })} />
            </div>
            {items.length > 1 && (
              <div className="ledger-field">
                <label htmlFor="ledger-item">Item</label>
                <select id="ledger-item" value={q.item} onChange={(e) => dispatch({ type: "queue", patch: { item: e.target.value } })}>
                  <option value="">All items</option>
                  {items.map((i) => <option key={i.item} value={i.item}>{i.asset}</option>)}
                </select>
              </div>
            )}
            <div className="ledger-field">
              <label htmlFor="ledger-state">Show</label>
              <select id="ledger-state" value={q.filter}
                onChange={(e) => isFilter(e.target.value) && dispatch({ type: "queue", patch: { filter: e.target.value } })}>
                {FILTERS.map((f) => <option key={f} value={f}>{`${FILTER_LABELS[f]} (${counts[f]})`}</option>)}
              </select>
            </div>
          </div>
          {q.action && (
            <p className="ledger-scope">
              {search ? "Searching the whole list. " : `Showing only the “${actionLabel(q.action)}” rows. `}
              <button type="button" className="link-button" onClick={() => dispatch({ type: "queue", patch: { action: "" } })}>Show the whole list</button>
            </p>
          )}
          <p className="hint small">Rows that correct the record come first, then identity or attribute doubt, then other candidates.</p>
          {current ? (
            <>
              <div className="ledger-nav">
                <button type="button" disabled={at <= 0} onClick={() => go(visible[at - 1])}>Previous</button>
                <span>{`Row ${at + 1} of ${visible.length}${visible.length < queue.length ? ` (${queue.length} in the whole list)` : ""}`}</span>
                <button type="button" disabled={at >= visible.length - 1} onClick={() => go(visible[at + 1])}>Next</button>
                <button type="button" disabled={!nextOpen} onClick={() => go(nextOpen)}>Next not reviewed</button>
              </div>
              <RecordCard key={keyOf(current)} row={current} uid="ledger-card" scan={scan} state={state} dispatch={dispatch}
                onShow={showFor?.(current)} />
            </>
          ) : (
            <div className="ledger-empty" role="status">
              <p>No record matches. Decisions you recorded stay in your files even when a filter hides them.</p>
              <button type="button" className="ledger-download" onClick={() => dispatch({ type: "queue", patch: { ...DEFAULT_QUEUE } })}>Clear search and filters</button>
            </div>
          )}
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
            <ImportBox ui={ui} />
          </div>
          {check.ok && <LedgerHandoff check={list} journal={journal} scanId={scan.id} />}
        </>
      )}
    </section>
  );
}

// What the list says about who this photo shows and how clear it is. "Not flagged" and "not reviewed" mean no one looked or nothing was found:
// they are never shown as a check that passed.
export function QualityNotes({ row }: { row: AssetRow }) {
  const others = row.competing_record_ids ?? [];
  return (
    <>
      {row.identity_state === "unresolved" && (
        <div className="ledger-warning" role="note">
          <strong>Identity not settled</strong>
          <p>{row.identity_reason || "The list could not tell which record this photo shows."}</p>
          {others.length > 0 && <p>Other records it could be: {others.join(", ")}.</p>}
          <p className="hint small">A desk identity check or a new photo may settle it.</p>
        </div>
      )}
      {row.identity_state !== "unresolved" && others.length > 0 && <p className="hint small">Other records it could be: {others.join(", ")}.</p>}
      {row.identity_state === "not_flagged" && <p className="hint small">No identity concern was flagged. That does not verify the identity.</p>}
      {row.evidence_quality === "unclear" && (
        <div className="ledger-warning" role="note">
          <strong>Photo clarity: not clear</strong>
          {row.evidence_quality_reason && <p>{row.evidence_quality_reason}</p>}
        </div>
      )}
      {row.evidence_quality === "not_reviewed" && <p className="hint small">The clarity of this photo has not been reviewed. That is not the same as clear.</p>}
    </>
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
        <div><dt>Suggested action</dt><dd>{actionLabel(row.action)}</dd></div>
        <div><dt>City says</dt><dd>{row.city_says}</dd></div>
        <div><dt>Photo shows</dt><dd>{row.photo_shows}</dd></div>
        <div><dt>City date</dt><dd>{row.city_date || "none"}</dd></div>
        <div><dt>Photo day</dt><dd>{day || "unknown"}</dd></div>
        {Object.entries(source.editable_fields).filter(([f]) => editable.includes(f)).map(([f, v]) => (
          <div key={f}><dt>{f}</dt><dd>now {String(v.before)}</dd></div>
        ))}
      </dl>
      <p className="hint small">The photo day is when the picture was taken. It is not an inspection date, and nothing here changes the city's dates.</p>
      <QualityNotes row={row} />
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
  // The action the list was showing when the review was opened. The whole list is one button away.
  initialAction?: string;
}

// A review belongs to one scan and one set of source snapshots: when either changes it starts again, from what was saved for that version.
export default function LedgerReviewPane(props: PaneProps) {
  return <Session key={reviewSignature(props.scan.id, props.check.rows)} {...props} />;
}

function Session(props: PaneProps) {
  const ledger = useContext(LedgerReviewContext);
  const { scan, check } = props;
  const signature = reviewSignature(scan.id, check.rows);
  const [begin] = useState(() => startSession(recoveryStore, scan.id, signature, check.rows, props.initialAction ?? "",
    ledger?.session?.signature === signature ? ledger.session.state : null));
  const [localState, localDispatch] = useReducer(reviewReducer, begin.state);
  const state = ledger?.session?.signature === signature ? ledger.session.state : localState;
  const dispatch: Dispatch<ReviewAction> = ledger ? (action) => {
    ledger.setSession((previous) => ({ scanId: scan.id, signature, open: true,
      state: reviewReducer(previous?.signature === signature ? previous.state : begin.state, action) }));
  } : localDispatch;

  const owns = useRef(begin.owns);
  const [stored, setStored] = useState<StorageProblem>(begin.problem);
  const [notices, setNotices] = useState<Notice[]>(begin.notices);
  const [plan, setPlan] = useState<ImportPlan | null>(null);
  const [askingClear, setAskingClear] = useState(false);
  const say = (notice: Notice) => setNotices((all) => [...all.filter((n) => n.id !== notice.id), notice]);

  // The page's memory outlives opening a map inspector: put this review there on arrival.
  useEffect(() => {
    ledger?.setSession((previous) => previous?.signature === signature ? { ...previous, open: true }
      : { scanId: scan.id, signature, open: true, state: begin.state });
  }, []);

  // Keep the work as it changes. A failure is shown, never thrown.
  const meaningful = hasMeaningfulWork(state);
  useEffect(() => {
    const synced = syncRecovery(recoveryStore, scan.id, signature, state, owns.current);
    owns.current = synced.owns;
    setStored(meaningful ? synced.problem : begin.problem);
    if (synced.evicted.length > 0) {
      setNotices((all) => [...all.filter((n) => n.id !== "evicted"), { id: "evicted", tone: "warn",
        text: `Saved work for ${synced.evicted.length === 1 ? "scan" : "scans"} ${synced.evicted.join(", ")} was removed to make room: this browser keeps four scans.` }]);
    }
  }, [state]);

  // A prompt before leaving only for work the browser could not keep.
  const problem = meaningful ? stored : "";
  useEffect(() => {
    if (!needsLeaveWarning(state, problem)) return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [meaningful, problem]);

  // The list was opened on one action; choosing another one in the list moves the review there too.
  const firstAction = useRef(props.initialAction);
  useEffect(() => {
    if (props.initialAction === firstAction.current) return;
    firstAction.current = props.initialAction;
    dispatch({ type: "queue", patch: { action: props.initialAction ?? "" } });
  }, [props.initialAction]);

  const ui: SessionUi = {
    notices, problem, plan, askingClear,
    onFile: (file) => {
      if (file.size > IMPORT_MAX_CHARS) return setPlan({ ok: false, errors: ["The file is far larger than a review."] });
      file.text().then(
        (text) => setPlan(planImport(text, { scanId: scan.id, rows: check.rows, current: state, readPackage: readReviewPackage })),
        () => setPlan({ ok: false, errors: ["The file could not be read."] }),
      );
    },
    onConfirmImport: () => {
      if (!plan || !plan.ok) return;
      dispatch({ type: "replace", state: applyImport(plan, state, check.rows) });
      setPlan(null);
      say({ id: "imported", tone: "info", text: `Imported ${plan.journal.decisions.length} ${plan.journal.decisions.length === 1 ? "decision" : "decisions"}. They are the reviewer's own word, not a municipal approval.` });
    },
    onCancelImport: () => setPlan(null),
    onAskClear: () => setAskingClear(true),
    onKeepSaved: () => setAskingClear(false),
    onClear: (everything) => {
      const done = clearSaved(recoveryStore, scan.id, everything);
      owns.current = true;
      setAskingClear(false);
      setNotices((all) => all.filter((n) => n.id === "unavailable")); // what they said was about the saved work that is gone now
      dispatch({ type: "replace", state: { ...emptyState(), queue: { ...state.queue } } });
      say({ id: "cleared", tone: done.ok ? "info" : "warn",
        text: done.ok ? `Saved work for ${everything ? "all scans" : "this scan"} was cleared from this browser, and the review on screen is empty.`
          : "The browser would not remove the saved work. Clear this site's data in the browser settings." });
    },
  };
  return <LedgerReviewView {...props} state={state} dispatch={dispatch} save={saveFile} ui={ui} />;
}
