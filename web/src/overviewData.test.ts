import { describe, expect, it } from "vitest";
import { phraseJourney, secondOpinions } from "./overviewData";
import type { Issue } from "./types";

describe("the overview's helpers", () => {
  it("reads the SAM 3.1 phrases the item agent tried, in order, from its progress lines", () => {
    const progress = [
      "Muse Spark drafted the item: find \"fireplug\", ask \"A fire hydrant is ...\"",
      "[1] SAM 3.1 finds \"fireplug\": 3 of 4 photos that show it, 0 of 14 that do not",
      "[2] SAM 3.1 finds \"fire hydrant\": 4 of 4 photos that show it, 0 of 14 that do not",
      "[5] Muse Spark checks \"fire hydrant\" with \"A fire hydrant ...\": kept 4 of 4 real ones",
      "[3] SAM 3.1 finds \"red hydrant\": 0 of 4 photos that show it, 0 of 14 that do not",
    ];
    expect(phraseJourney(progress)).toEqual([
      { phrase: "fireplug", hit: 3, of: 4 }, { phrase: "fire hydrant", hit: 4, of: 4 }, { phrase: "red hydrant", hit: 0, of: 4 },
    ]);
    expect(phraseJourney([])).toEqual([]);
  });
  it("counts the findings another model answered too", () => {
    const issues = [{ second_opinion: { answer: "yes", agree: true, model: "Muse Spark Open" } }, { second_opinion: null }] as unknown as Issue[];
    expect(secondOpinions(issues)).toBe(1);
  });
});
