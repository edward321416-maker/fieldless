/* Pure helpers for survey_labeler.html (plan 3). A classic script, so it loads from file:// in Chrome
   (window.SurveyCore) and from node --test (module.exports). */
(function (root) {
  const FORMAT = "threshold-survey-truth/1";
  const DEFECTS = ["missing_blocks", "cover_cut", "fixed_obstruction"];
  const IDLE_LIMIT_MS = 60000;

  const isObj = (x) => x !== null && typeof x === "object" && !Array.isArray(x);

  function emptyTruth(manifest) {
    return { format: FORMAT, run: manifest.run, scan_id: manifest.scan_id, labeled_by: "human",
             labeling_seconds: 0, photos: {} };
  }

  // Validates an imported or restored file and fills in defaults. Throws on a bad shape.
  function normalizeTruth(obj) {
    if (!isObj(obj) || obj.format !== FORMAT) throw new Error("not a survey truth file");
    if (typeof obj.run !== "string" || !Number.isInteger(obj.scan_id)) throw new Error("run and scan_id are required");
    const out = emptyTruth(obj);
    if (typeof obj.labeled_by === "string") out.labeled_by = obj.labeled_by;
    if (Number.isInteger(obj.labeling_seconds) && obj.labeling_seconds >= 0) out.labeling_seconds = obj.labeling_seconds;
    for (const [id, p] of Object.entries(isObj(obj.photos) ? obj.photos : {})) {
      if (!isObj(p) || !Number.isInteger(p.idx)) continue;
      const defects = (Array.isArray(p.defects) ? p.defects : [])
        .filter((d) => isObj(d) && DEFECTS.includes(d.type))
        .map((d) => (d.type === "fixed_obstruction" ? { type: d.type, label: String(d.label || "pole") } : { type: d.type }));
      const obstructions = (Array.isArray(p.obstructions) ? p.obstructions : [])
        .filter(isObj).map((o) => ({ label: String(o.label || "obstacle") }));
      const entrances = (Array.isArray(p.entrances) ? p.entrances : []).filter(isObj).map((e) => ({
        steps: Number.isInteger(e.steps) && e.steps >= 0 ? e.steps : 0, sill_unsure: e.sill_unsure === true }));
      if (defects.length || obstructions.length || entrances.length) out.photos[id] = { idx: p.idx, defects, obstructions, entrances };
    }
    return out;
  }

  function entry(truth, frame) {
    return (truth.photos[frame.image_id] ||= { idx: frame.idx, defects: [], obstructions: [], entrances: [] });
  }

  // A photo with no labels left is removed, so the file only lists labelled photos.
  function prune(truth, frame) {
    const p = truth.photos[frame.image_id];
    if (p && !p.defects.length && !p.obstructions.length && !p.entrances.length) delete truth.photos[frame.image_id];
  }

  function toggleDefect(truth, frame, type, label) {
    if (!DEFECTS.includes(type)) throw new Error(`unknown defect ${type}`);
    const p = entry(truth, frame);
    const name = label || "pole";
    const i = p.defects.findIndex((d) => d.type === type && (type !== "fixed_obstruction" || d.label === name));
    if (i >= 0) p.defects.splice(i, 1);
    else p.defects.push(type === "fixed_obstruction" ? { type, label: name } : { type });
    prune(truth, frame);
  }

  function toggleObstruction(truth, frame, label) {
    const p = entry(truth, frame);
    const name = label || "obstacle";
    const i = p.obstructions.findIndex((o) => o.label === name);
    if (i >= 0) p.obstructions.splice(i, 1);
    else p.obstructions.push({ label: name });
    prune(truth, frame);
  }

  function addEntrance(truth, frame, steps, sillUnsure) {
    entry(truth, frame).entrances.push({ steps: Number.isInteger(steps) && steps >= 0 ? steps : 0, sill_unsure: sillUnsure === true });
  }

  function removeEntrance(truth, frame, index) {
    const p = truth.photos[frame.image_id];
    if (!p) return;
    p.entrances.splice(index, 1);
    prune(truth, frame);
  }

  // Pairs manifest frames with picked files named "<image_id>.jpg". Frames come back in idx order.
  function matchPhotos(manifest, fileNames) {
    const names = new Set(fileNames);
    const frames = [...manifest.frames].sort((a, b) => a.idx - b.idx);
    return { frames, missing: frames.filter((f) => !names.has(`${f.image_id}.jpg`)).map((f) => f.image_id) };
  }

  // The D1 rule: count a second only while photos are loaded, the tab is visible and the last activity is recent.
  function shouldCount(loaded, visible, nowMs, lastActivityMs) {
    return loaded && visible && nowMs - lastActivityMs < IDLE_LIMIT_MS;
  }

  const api = { FORMAT, DEFECTS, IDLE_LIMIT_MS, emptyTruth, normalizeTruth, toggleDefect, toggleObstruction,
                addEntrance, removeEntrance, matchPhotos, shouldCount };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SurveyCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
