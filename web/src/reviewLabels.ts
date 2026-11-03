// Preserve stored action keys while saying what the reviewer can actually do.
export function actionLabel(action: string): string {
  return action === "Update the date" ? "Review newer photo evidence" : action;
}
