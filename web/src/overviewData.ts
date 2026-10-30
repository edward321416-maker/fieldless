import type { Issue } from "./types";

export interface PhraseTry {
  phrase: string;
  hit: number; // photos showing the item where SAM 3.1 found the phrase
  of: number; // photos showing the item
}

// The SAM 3.1 phrases the item agent tried, in the order it tried them, read from its progress lines
// ('[2] SAM 3.1 finds "fire hydrant": 4 of 4 photos that show it, 0 of 14 that do not').
const TRY = /^\[\d+\] SAM 3\.1 finds "(.+?)": (\d+) of (\d+) photos that show it/;
export function phraseJourney(progress: string[]): PhraseTry[] {
  return progress.flatMap((line) => {
    const m = line.match(TRY);
    return m ? [{ phrase: m[1], hit: Number(m[2]), of: Number(m[3]) }] : [];
  });
}

// Findings another model answered as well: Muse Spark Open on a possible defect, or Muse Spark auditing Glimmer.
export const secondOpinions = (issues: Issue[]) => issues.filter((i) => i.second_opinion).length;
