import type { EntranceReview, IssueReview, Picked } from "./types";

// The answers a person can give on a card, each with its key (docs/ui-rules.md U5): the question first, then
// buttons whose labels come from the survey standard (label_yes, label_no, the target), never from the web app.
export type Send = { kind: "issue"; body: Omit<IssueReview, "seconds"> } | { kind: "entrance"; body: Omit<EntranceReview, "seconds"> };

export interface Choice {
  key: string; // the keyboard shortcut, shown on the button
  label: string;
  send: Send;
  main?: boolean; // the model's own answer
}

const FIELD = "Can't tell from the photo: check on site";

export function choicesFor(p: Picked): { question: string; choices: Choice[] } {
  const cantTell = (): Choice => ({ key: "C", label: FIELD, send: p.kind === "issue"
    ? { kind: "issue", body: { action: "field_check" } } : { kind: "entrance", body: { action: "field_check" } } });
  if (p.kind === "entrance") {
    const e = p.item;
    if (e.review_reasons.includes("possible_sill")) {
      const step = (n: number): Send => ({ kind: "entrance", body: n === e.step_count
        ? { action: "confirm" } : { action: "correct", step_count: n } });
      return { question: "Is there a step at this entrance?", choices: [
        { key: "Y", label: "Yes, one step", send: step(1) },
        { key: "N", label: "No step", send: step(0) },
        cantTell(),
      ] };
    }
    return { question: "Is the model right?", choices: [
      { key: "Y", label: `Correct as shown: ${e.label.text}`, send: { kind: "entrance", body: { action: "confirm" } }, main: true },
      cantTell(),
    ] };
  }
  const i = p.item;
  const check = i.check;
  if (check?.inventory) {
    return { question: "Is the model right?", choices: [
      { key: "Y", label: `Yes, a ${check.target}`, send: { kind: "issue", body: { action: "answer", answer: "yes" } }, main: true },
      { key: "N", label: `Not a ${check.target}`, send: { kind: "issue", body: { action: "reject" } } },
      cantTell(),
    ] };
  }
  if (check) {
    return { question: "What do you see?", choices: [
      { key: "1", label: check.label_yes, send: { kind: "issue", body: { action: "answer", answer: "yes" } }, main: check.answer === "yes" },
      { key: "2", label: check.label_no, send: { kind: "issue", body: { action: "answer", answer: "no" } }, main: check.answer === "no" },
      { key: "N", label: `Not a ${check.target}`, send: { kind: "issue", body: { action: "reject" } } },
      cantTell(),
    ] };
  }
  return { question: "Is the model right?", choices: [
    { key: "Y", label: "Yes", send: { kind: "issue", body: { action: "confirm" } }, main: true },
    { key: "N", label: "No", send: { kind: "issue", body: { action: "reject" } } },
    cantTell(),
  ] };
}

export function choiceForKey(choices: Choice[], key: string): Choice | undefined {
  return choices.find((c) => c.key === key.toUpperCase());
}
