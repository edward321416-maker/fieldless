import { dollars } from "./format";

interface CostInput {
  cost_usd: unknown;
  km: unknown;
  asset_check?: { trust?: { second_looks_usd?: unknown } } | null;
}
const cost = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;

export function reviewCosts(scan: CostInput) {
  const survey = cost(scan.cost_usd);
  const followUps = cost(scan.asset_check?.trust?.second_looks_usd);
  const combined = survey !== null && followUps !== null ? cost(survey + followUps) : null;
  const km = typeof scan.km === "number" && Number.isFinite(scan.km) && scan.km > 0 ? scan.km : null;
  const combinedPerKm = combined !== null && km !== null ? cost(combined / km) : null;
  return { survey, followUps, combined, combinedPerKm };
}

export default function ReviewCosts({ scan }: { scan: CostInput }) {
  const costs = reviewCosts(scan);
  return <div role="group" aria-label="Cost of the review list">
    <p className="hint small">Survey: {costs.survey === null ? "not recorded" : dollars(costs.survey)} · Follow-ups: {costs.followUps === null ? "not recorded" : dollars(costs.followUps)}</p>
    <p className="small"><strong>Combined: {costs.combined === null ? "not determined" : dollars(costs.combined)}
      {costs.combinedPerKm !== null && ` · ${dollars(costs.combinedPerKm)} per surveyed km`}</strong></p>
    <p className="hint small">Human review and official import costs have not been measured.</p>
  </div>;
}
