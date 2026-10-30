// Which model did what in a survey, from the server's call record (call_stats_scan<N>.json, demo surveys only): per
// stage of the run and per model, the calls, their own time added up and their cost.
export interface CallStage {
  stage: string; // scene | sidewalk | items | second_opinion | entrances | evidence | report, in run order
  label: string; // the stage in words
  model: string; // "SAM 3.1", "Muse Spark", "Muse Spark Open", "Muse Glimmer", or a free file upload
  calls: number;
  seconds: number; // each call's own duration added up: calls run side by side
  cost_usd: number | null; // null when the provider's cost is not logged (Muse Spark Open)
}

export interface CallStats {
  scan_id: number;
  note: string;
  stages: CallStage[];
  total: { calls: number; seconds: number; cost_usd: number; scan_cost_usd: number; processing_seconds: number };
}

export interface StageRow {
  stage: string;
  label: string;
  cost: number | null; // null when no model of the stage has a logged cost
  share: number; // of the survey's cost, 0-1, for the bar
  models: { model: string; calls: number; cost: number | null }[];
}

// File uploads for SAM 3.1 video cost nothing and are not a model's work, so the list leaves them out.
const isUpload = (s: CallStage) => s.model.startsWith("File upload");
const round = (usd: number) => Math.round(usd * 1e4) / 1e4;

// One row per stage, in the order the record gives (the run's order), with its models in their order.
export function stageRows(stats: CallStats): StageRow[] {
  const rows: StageRow[] = [];
  for (const s of stats.stages.filter((x) => !isUpload(x))) {
    let row = rows.find((r) => r.stage === s.stage);
    if (!row) {
      row = { stage: s.stage, label: s.label, cost: null, share: 0, models: [] };
      rows.push(row);
    }
    row.models.push({ model: s.model, calls: s.calls, cost: s.cost_usd });
    if (s.cost_usd !== null) row.cost = round((row.cost ?? 0) + s.cost_usd);
  }
  for (const r of rows) r.share = r.cost !== null && stats.total.cost_usd > 0 ? r.cost / stats.total.cost_usd : 0;
  return rows;
}

// The calls a model answered, uploads left out.
export const modelCalls = (stats: CallStats) =>
  stats.stages.filter((s) => !isUpload(s)).reduce((n, s) => n + s.calls, 0);
