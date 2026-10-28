// The walking survey each standard's results are set beside (docs/ui-rules.md U1), from docs/survey-benchmarks.md:
// published numbers only, with the source and the caveat that the scope differs.
export interface Benchmark {
  short: string; // one line under the main numbers: "On foot · <short> · Source"
  scope: string; // the rest, on the source link's title: the whole cost, and that the scope differs
  source: string;
}

export const BENCHMARKS: Record<string, Benchmark> = {
  US: {
    short: "Seattle 2017: 14 people, 16 weeks, about $106 per km",
    scope: "$400,000 for 2,336 miles. It recorded more about each sidewalk, so the scope differs.",
    source: "https://seattle.gov/documents/Departments/SDOT/About/SidewalkAssessExecSummary_4_6_2018R5.pdf",
  },
};
