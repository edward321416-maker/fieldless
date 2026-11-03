// The CSV bytes the app writes are the ones Python writes: tests/fixtures/ledger_interop holds the app's JSON form of a check, a
// journal and the CSV bytes tests/eval/test_ledger_interop.py also reproduces. A Blob made from the string adds no second BOM.
import { beforeAll, describe, expect, it } from "vitest";
import { changesCsv, exportFiles, observationsCsv, validateJournal } from "./ledgerReview";
import type { AssetRow, LedgerJournal } from "./types";

const bytes = (text: string) => Array.from(new TextEncoder().encode(text));

describe("the CSV bytes match the ones Python writes", () => {
  let rows: AssetRow[] = [];
  let journal: LedgerJournal;
  let scan = 0;
  let changes: number[] = [];
  let observations: number[] = [];
  beforeAll(async () => {
    // @ts-expect-error node:fs has no types here
    const { readFileSync } = await import("node:fs");
    const read = (name: string) => readFileSync(new URL(`../../tests/fixtures/ledger_interop/${name}`, import.meta.url));
    const check = JSON.parse(read("check.json").toString("utf8"));
    rows = check.rows;
    scan = check.scan_id;
    journal = JSON.parse(read("journal.json").toString("utf8"));
    changes = Array.from(read("changes.csv") as Uint8Array);
    observations = Array.from(read("observations.csv") as Uint8Array);
  });

  it("accepts the journal the fixture holds", () => {
    expect(validateJournal(rows, journal, scan).errors).toEqual([]);
  });

  it("writes changes.csv byte for byte", () => {
    expect(bytes(changesCsv(rows, journal, scan))).toEqual(changes);
  });

  it("writes observations.csv byte for byte", () => {
    expect(bytes(observationsCsv(rows, journal, scan))).toEqual(observations);
  });

  it("saves the same bytes: a Blob made from the string, as saveFile does, adds no second BOM", async () => {
    const [, change, observation] = exportFiles(rows, journal, scan);
    for (const [file, expected] of [[change, changes], [observation, observations]] as const) {
      const blob = new Blob([file.text], { type: `${file.mime};charset=utf-8` });
      expect(Array.from(new Uint8Array(await blob.arrayBuffer()))).toEqual(expected);
    }
  });

  it("starts each CSV with one BOM and the journal file with none", () => {
    const [json, change, observation] = exportFiles(rows, journal, scan);
    for (const file of [change, observation]) {
      expect(file.text.startsWith("﻿")).toBe(true);
      expect(file.text.slice(1).includes("﻿")).toBe(false);
    }
    expect(json.text.startsWith("﻿")).toBe(false);
  });
});
