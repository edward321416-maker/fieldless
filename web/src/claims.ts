// A number measured outside the server, shown on the overview only for the survey it was measured on, with how.
export interface Claim {
  right: number;
  of: number;
  how: string;
}

// Surveys 30 (Seattle, Ballard, 2026 photos) and 17 (Seattle, Capitol Hill): each finding the models kept, checked on
// its evidence photo (2026-11-01, 2026-10-29).
export const CHECKED_BY_EYE: Record<number, Claim> = {
  30: { right: 43, of: 57, how: "Judged on each evidence photo by the AI coding assistant, not by a person." },
  17: { right: 73, of: 86, how: "Judged on each evidence photo by the AI coding assistant, not by a person." },
};
