// A number measured outside the server, shown on the overview only for the survey it was measured on, with how.
export interface Claim {
  right: number;
  of: number;
  how: string;
}

// Survey 17 (Seattle, Capitol Hill): each finding the models kept, checked on its evidence photo (2026-10-29).
export const CHECKED_BY_EYE: Record<number, Claim> = {
  17: { right: 73, of: 86, how: "Judged on each evidence photo by the AI coding assistant, not by a person." },
};
