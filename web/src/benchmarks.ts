// The walking survey each standard's cost is set beside on the first screen (docs/ui-rules.md U1, U7), from
// docs/survey-benchmarks.md: published numbers only, per km like Fieldless's own, with the source and the caveat
// that the scope differs.
export interface Benchmark {
  usd_per_km: number;
  people: string; // who walked how far, how long
  name: string; // the survey
  caveat: string; // why the two numbers are not the same job
  scope: string; // on the source link's title: the whole cost
  source: string;
}

export const BENCHMARKS: Record<string, Benchmark> = {
  US: {
    usd_per_km: 106,
    people: "14 people walked 2,336 miles in 16 weeks",
    name: "Seattle's 2017 sidewalk survey",
    caveat: "The 2017 survey recorded more about each sidewalk, so the scope differs.",
    scope: "$400,000 for 2,336 miles",
    source: "https://seattle.gov/documents/Departments/SDOT/About/SidewalkAssessExecSummary_4_6_2018R5.pdf",
  },
};
