// The walking survey each standard's results are set beside (docs/ui-rules.md U1), from docs/survey-benchmarks.md:
// published numbers only, with the source and the caveat that the scope differs.
export interface Benchmark {
  text: string;
  source: string;
}

export const BENCHMARKS: Record<string, Benchmark> = {
  US: {
    text: "For scale: Seattle's 2017 walking survey took 14 people 16 weeks, about $106 per km ($400,000 for 2,336 miles). It recorded more about each sidewalk, so the scope differs.",
    source: "https://seattle.gov/documents/Departments/SDOT/About/SidewalkAssessExecSummary_4_6_2018R5.pdf",
  },
};
