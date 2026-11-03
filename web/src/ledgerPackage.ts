// The portable review package: one UTF-8 JSON file with the source list, the journal and, for a person who opens it, the two CSV files.
// docs/specs/2026-11-04-ledger-improvements-design.md section C; eval/ledger_package.py reads and writes the same format.
//
// The source and the journal are the only inputs. The manifest and the CSV strings are checked against what is rebuilt from them and
// a package that differs is refused, never repaired and never trusted as instructions. A package is not signed: it proves nothing about
// who made it, and the reviewer names in it are self-asserted. Nothing here sends anything anywhere.
import { changesCsv, exportFiles, has, observationsCsv, validateJournal, type ExportFile, type LedgerCheck } from "./ledgerReview";
import type { LedgerJournal } from "./types";

export const PACKAGE_KIND = "fieldless-ledger-review";
export const PACKAGE_MANIFEST = {
  notice: "Prepared only; not applied to an official ledger.",
  reviewer_assertion: "Self-asserted; not authenticated municipal approval.",
} as const;
// The public guide in the code repository: what the package is, how to check it against the current GeoJSON and who approves it.
export const PACKAGE_GUIDE_URL = "https://github.com/edward321416-maker/fieldless/blob/main/docs/ledger-workflow.md";

const REQUIRED = ["schema_version", "kind", "scan_id", "source", "journal"];
const OPTIONAL = ["manifest", "files"];
const FILE_KEYS = ["changes_csv", "observations_csv"];
const MAX_DEPTH = 64;
const MAX_TEXT = 32 * 1024 * 1024;

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

function sameKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const own = Object.keys(value);
  return own.length === keys.length && keys.every((k) => has(value, k));
}

// A key that would reach Object.prototype if anything assigned through it, or a nesting that could overflow a recursive reader.
// An explicit stack, so the check itself cannot overflow.
function structureProblem(value: unknown): string | null {
  const stack: [unknown, number][] = [[value, 0]];
  while (stack.length > 0) {
    const [node, depth] = stack.pop()!;
    if (typeof node !== "object" || node === null) continue;
    if (depth > MAX_DEPTH) return "package is nested too deeply";
    if (Array.isArray(node)) {
      for (const item of node) stack.push([item, depth + 1]);
      continue;
    }
    for (const key of Object.keys(node)) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") return `package has a ${JSON.stringify(key)} key, which no package field is`;
      stack.push([(node as Record<string, unknown>)[key], depth + 1]);
    }
  }
  return null;
}

function rebuilt(rows: LedgerCheck["rows"], journal: LedgerJournal, scanId: number): Record<string, string> {
  return { changes_csv: changesCsv(rows, journal, scanId), observations_csv: observationsCsv(rows, journal, scanId) };
}

// The one file to download. The journal must pass the same rules as the three separate downloads, so a package is never made from
// work that the backend would refuse.
export function buildReviewPackage(check: LedgerCheck, journal: LedgerJournal, scanId: number): ExportFile {
  if (check.scan_id !== scanId) throw new Error(`The list is for scan ${String(check.scan_id)}, not scan ${scanId}.`);
  const journalFile = exportFiles(check.rows, journal, scanId)[0]; // validates, and keeps only the contract's keys of each decision
  const files = rebuilt(check.rows, journal, scanId);
  const body = {
    schema_version: 1, kind: PACKAGE_KIND, scan_id: scanId, source: check, journal: JSON.parse(journalFile.text),
    manifest: { ...PACKAGE_MANIFEST }, files: { changes_csv: files.changes_csv, observations_csv: files.observations_csv },
  };
  return { name: `ledger-review-scan${scanId}-package.json`, mime: "application/json", text: `${JSON.stringify(body, null, 2)}\n` };
}

// The source and journal of a package (a parsed value or the file's text), or an Error naming every problem. The package cannot
// choose a city or a source for anyone: the caller checks that this source is the one open before using its journal.
export function readReviewPackage(value: unknown): { source: LedgerCheck; journal: LedgerJournal } {
  let pkg = value;
  if (typeof pkg === "string") {
    if (pkg.length > MAX_TEXT) throw new Error("The package was rejected: package is too large.");
    try {
      pkg = JSON.parse(pkg.charCodeAt(0) === 0xfeff ? pkg.slice(1) : pkg);
    } catch {
      throw new Error("The package was rejected: package is not valid JSON.");
    }
  }
  const reject = (problems: string[]): never => {
    throw new Error(`The package was rejected: ${problems.join(" ")}`);
  };
  if (!isRecord(pkg)) return reject(["package is not an object."]);
  const shape = structureProblem(pkg);
  if (shape) return reject([`${shape}.`]);

  const problems: string[] = [];
  for (const key of Object.keys(pkg)) if (!REQUIRED.includes(key) && !OPTIONAL.includes(key)) problems.push(`package: unknown key ${key}.`);
  for (const key of REQUIRED) if (!has(pkg, key)) problems.push(`package: missing key ${key}.`);
  if (has(pkg, "kind") && pkg.kind !== PACKAGE_KIND) problems.push(`package: kind must be ${PACKAGE_KIND}.`);
  if (has(pkg, "schema_version") && pkg.schema_version !== 1) problems.push("package: schema_version must be 1.");
  const source = pkg.source;
  const journal = pkg.journal;
  if (has(pkg, "source") && (!isRecord(source) || !Array.isArray(source.rows) || !source.rows.every(isRecord))) {
    problems.push("package: source is not an Asset Check with rows.");
  }
  if (has(pkg, "journal") && !isRecord(journal)) problems.push("package: journal is not an object.");
  const scan = pkg.scan_id;
  if (has(pkg, "scan_id")) {
    if (typeof scan !== "number" || !Number.isInteger(scan)) problems.push("package: scan_id must be a whole number.");
    else {
      for (const [name, part] of [["source", source], ["journal", journal]] as const) {
        if (isRecord(part) && part.scan_id !== scan) problems.push(`package: scan_id ${scan} is not the ${name}'s scan_id ${String(part.scan_id)}.`);
      }
    }
  }
  if (has(pkg, "manifest") && !(isRecord(pkg.manifest) && sameKeys(pkg.manifest, Object.keys(PACKAGE_MANIFEST))
    && Object.entries(PACKAGE_MANIFEST).every(([k, v]) => pkg.manifest !== null && (pkg.manifest as Record<string, unknown>)[k] === v))) {
    problems.push("package: manifest is not the one this format states (a manifest cannot say the review is approved or applied).");
  }
  const files = pkg.files;
  if (has(pkg, "files")) {
    if (!isRecord(files)) problems.push("package: files must be an object with changes_csv and observations_csv.");
    else {
      for (const key of Object.keys(files)) if (!FILE_KEYS.includes(key)) problems.push(`package: files has unknown key ${key}.`);
      for (const key of FILE_KEYS) if (typeof files[key] !== "string") problems.push(`package: files.${key} must be text.`);
    }
  }
  if (problems.length > 0) return reject(problems);

  const check = source as unknown as LedgerCheck;
  const checked = validateJournal(check.rows, journal as unknown as LedgerJournal, scan as number);
  if (!checked.ok) return reject(checked.errors);
  if (isRecord(files)) {
    const want = rebuilt(check.rows, journal as unknown as LedgerJournal, scan as number);
    for (const key of FILE_KEYS) {
      if (files[key] !== want[key]) problems.push(`package: files.${key} is not the CSV rebuilt from the source and journal (it was edited or made elsewhere); the file is not trusted.`);
    }
    if (problems.length > 0) return reject(problems);
  }
  return { source: check, journal: journal as unknown as LedgerJournal };
}

export interface PreviewChange {
  record_id: string;
  item: string;
  layer: string;
  field: string;
  before: string;
  after: string;
  photo_observed_on: string;
  reviewer: string;
}

export interface ReviewPreview {
  ok: boolean;
  errors: string[];
  decisions: number;
  changes: PreviewChange[];
  observations: number;
  holds: number;
}

// What the package would propose, read from the journal by the same rules the downloads use. A preview of the prepared proposal: it
// says nothing about the city's current files.
export function previewOf(check: LedgerCheck, journal: LedgerJournal, scanId: number): ReviewPreview {
  const none = (errors: string[]): ReviewPreview => ({ ok: false, errors, decisions: 0, changes: [], observations: 0, holds: 0 });
  if (check.scan_id !== scanId) return none([`The list is for scan ${String(check.scan_id)}, not scan ${scanId}.`]);
  const result = validateJournal(check.rows, journal, scanId);
  if (!result.ok) return none(result.errors);
  return {
    ok: true, errors: [], decisions: journal.decisions.length, observations: result.observations.length, holds: result.holds.length,
    changes: result.changes.map((c) => ({ record_id: c.record_id, item: c.item, layer: c.layer, field: c.field, before: c.before, after: c.after,
      photo_observed_on: c.photo_observed_on, reviewer: c.reviewer })),
  };
}
