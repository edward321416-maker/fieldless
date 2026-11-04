// The life of one review in the page: what comes back when it opens, what is kept as it changes, and what the reviewer is told.
// Pure over a store, so every case can be tested without a browser.
import { DEFAULT_QUEUE } from "./ledgerQuery";
import { emptyState, hasMeaningfulWork, type RecoverableState, type recoveryStore } from "./ledgerRecovery";
import type { AssetRow } from "./types";

export type RecoveryStoreLike = Pick<typeof recoveryStore, "load" | "save" | "clear" | "clearAll" | "entries" | "raw">;
export type StorageProblem = "" | "full" | "unavailable" | "conflict";

export interface Notice {
  id: string;
  tone: "info" | "warn";
  text: string;
  // The notice is about a saved file this version could not use: it can be downloaded before new work replaces it.
  older?: boolean;
}

export interface Start {
  state: RecoverableState;
  notices: Notice[];
  problem: StorageProblem;
  // Whether this session may replace or remove the saved file: not while it holds work this version could not restore.
  owns: boolean;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const when = (iso: string) => `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;

export function startSession(store: RecoveryStoreLike, scanId: number, signature: string, rows: AssetRow[], initialAction: string,
  existing: RecoverableState | null): Start {
  const fresh = (): RecoverableState => ({ ...emptyState(), queue: { ...DEFAULT_QUEUE, action: initialAction } });
  const loaded = store.load(scanId, signature, rows);
  const problem: StorageProblem = loaded.status === "unavailable" ? "unavailable" : "";
  const start = begin(loaded, fresh, problem);
  // Work already in the page stays as it is. A restore is not announced twice, but a file that still cannot be used still is.
  if (existing) return { ...start, state: existing, notices: start.notices.filter((n) => n.id !== "restored"), owns: start.owns };
  return start;
}

function begin(loaded: ReturnType<RecoveryStoreLike["load"]>, fresh: () => RecoverableState, problem: StorageProblem): Start {
  switch (loaded.status) {
    case "restored":
      return { state: loaded.state, problem, owns: true, notices: [{ id: "restored", tone: "info",
        text: `Restored your saved work from ${when(loaded.savedAt)}: ${plural(loaded.decisions, "recorded decision", "recorded decisions")} and ${plural(loaded.drafts, "draft", "drafts")}. `
          + "Restored decisions are still the reviewer's own word, not a municipal approval." }] };
    case "stale":
      return { state: fresh(), problem, owns: false, notices: [{ id: "stale", tone: "warn",
        text: `Work saved in this browser on ${when(loaded.savedAt)} was made on an older version of this list, so it is unavailable for this version and was not restored. `
          + "New work here will replace it, so download it first if you want to keep it. If you downloaded a journal or a package then, use Import to bring its decisions back where they still apply.", older: true }] };
    case "corrupt":
      return { state: fresh(), problem, owns: false, notices: [{ id: "corrupt", tone: "warn",
        text: `Work saved in this browser for this scan could not be read (${loaded.detail}), so nothing was restored. New work here will replace it, so download it first if you want to keep it.`, older: true }] };
    case "unavailable":
      return { state: fresh(), problem, owns: true, notices: [{ id: "unavailable", tone: "warn",
        text: "This browser cannot keep saved work (storage is unavailable or blocked). Your work lasts only while this page is open. Download the files to keep it." }] };
    case "none":
      return { state: fresh(), problem, owns: true, notices: [] };
  }
}

export interface Synced {
  owns: boolean;
  problem: StorageProblem;
  evicted: number[];
}

// Called after every change. Never throws: a store that fails is a result the page shows.
export function syncRecovery(store: RecoveryStoreLike, scanId: number, signature: string, state: RecoverableState, owns: boolean,
  force = false): Synced {
  const meaningful = hasMeaningfulWork(state);
  if (!meaningful && !owns) return { owns, problem: "", evicted: [] };
  const saved = store.save(scanId, signature, state, { force });
  if (!meaningful) return { owns, problem: "", evicted: [] };
  return { owns: true, problem: saved.ok ? "" : saved.reason, evicted: saved.evicted };
}

// A prompt before leaving, only for work the browser could not keep. Safe navigation is never interrupted.
export function needsLeaveWarning(state: RecoverableState, problem: StorageProblem): boolean {
  return problem !== "" && hasMeaningfulWork(state);
}

export function clearSaved(store: RecoveryStoreLike, scanId: number, everything: boolean): { ok: boolean } {
  return everything ? store.clearAll() : store.clear(scanId);
}
