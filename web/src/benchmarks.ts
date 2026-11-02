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

// A published fact the overview cites: its big number, what it says and where it comes from.
export interface Fact {
  title: string;
  big: string;
  text: string;
  how?: string; // a small line under the text
  source?: string;
}

export const BENCHMARKS: Record<string, Benchmark> = {
  US: {
    usd_per_km: 106,
    people: "14 people walked 2,336 miles in 16 weeks",
    name: "Seattle's 2017 sidewalk survey",
    caveat: "The 2017 survey recorded more about each sidewalk, so the scope differs.",
    scope: "a $400,000 budget for 2,336 miles, the most it could have cost",
    source: "https://seattle.gov/documents/Departments/SDOT/About/SidewalkAssessExecSummary_4_6_2018R5.pdf",
  },
};

// Seattle's 2020 Status & Condition Report: pp. 39 (the budget) and 115 (the tree list) of the PDF.
const CONDITION_REPORT = "https://www.seattle.gov/documents/Departments/SDOT/About/DocumentLibrary/Reports/AMStatusCondRpt2020Final.pdf";

// What walking a city takes, and what a survey can lead to. The street lights are the builder's own job, so they have
// no source to link.
export const PROBLEM: Fact[] = [
  { title: "Seattle's sidewalks", big: "16 weeks", text: "14 people walked 2,336 miles in 2017, on a $400,000 budget.",
    source: BENCHMARKS.US.source },
  { title: "Seattle's street trees", big: "2016 to 2024", text: "The years the city expected to spend checking its tree list in the field.",
    source: CONDITION_REPORT },
  { title: "One district's street lights", big: "3 months",
    text: "In 2022 my district paid about twenty of us to walk its streets for three months. Where each street light stood wasn't marked exactly, so we kept looking around to find it." },
  { title: "What a survey leads to", big: "3×",
    text: "Seattle's sidewalk repair and mitigation budget after its 2017 survey. A survey is how a city learns what to fix.",
    source: CONDITION_REPORT },
];
