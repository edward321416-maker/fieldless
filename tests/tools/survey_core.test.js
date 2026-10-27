const test = require("node:test");
const assert = require("node:assert/strict");
const core = require("../../tools/survey_core.js");

const manifest = { run: "r1", scan_id: 3, frames: [
  { idx: 1, image_id: "b", captured_at: "2023-02-08", creator: "u" },
  { idx: 0, image_id: "a", captured_at: "2023-02-08", creator: "u" },
] };
const A = { idx: 0, image_id: "a" };

test("empty truth carries the scan and the format", () => {
  const t = core.emptyTruth(manifest);
  assert.deepEqual(t, { format: core.FORMAT, run: "r1", scan_id: 3, labeled_by: "human", labeling_seconds: 0, photos: {} });
});

test("toggling a defect adds it, toggling again removes the photo entry", () => {
  const t = core.emptyTruth(manifest);
  core.toggleDefect(t, A, "missing_blocks");
  assert.deepEqual(t.photos.a, { idx: 0, defects: [{ type: "missing_blocks" }], obstructions: [], entrances: [] });
  core.toggleDefect(t, A, "missing_blocks");
  assert.equal(t.photos.a, undefined);
});

test("fixed objects are told apart by label", () => {
  const t = core.emptyTruth(manifest);
  core.toggleDefect(t, A, "fixed_obstruction", "pole");
  core.toggleDefect(t, A, "fixed_obstruction", "bollard");
  assert.deepEqual(t.photos.a.defects.map((d) => d.label), ["pole", "bollard"]);
  core.toggleDefect(t, A, "fixed_obstruction", "pole");
  assert.deepEqual(t.photos.a.defects, [{ type: "fixed_obstruction", label: "bollard" }]);
});

test("unknown defect types are refused", () => {
  assert.throws(() => core.toggleDefect(core.emptyTruth(manifest), A, "worn"), /unknown defect/);
});

test("obstructions toggle by label and entrances add and remove", () => {
  const t = core.emptyTruth(manifest);
  core.toggleObstruction(t, A, "electric scooter");
  core.addEntrance(t, A, 2, false);
  core.addEntrance(t, A, -1, true);
  assert.deepEqual(t.photos.a.obstructions, [{ label: "electric scooter" }]);
  assert.deepEqual(t.photos.a.entrances, [{ steps: 2, sill_unsure: false }, { steps: 0, sill_unsure: true }]);
  core.removeEntrance(t, A, 0);
  core.removeEntrance(t, A, 0);
  core.toggleObstruction(t, A, "electric scooter");
  assert.equal(t.photos.a, undefined);
});

test("normalize keeps good entries, drops bad ones and refuses other formats", () => {
  const t = core.normalizeTruth({ format: core.FORMAT, run: "r1", scan_id: 3, labeling_seconds: 7, photos: {
    a: { idx: 0, defects: [{ type: "cover_cut" }, { type: "worn" }], obstructions: [{}], entrances: [{ steps: 1.5 }] },
    b: { defects: [] },
  } });
  assert.equal(t.labeling_seconds, 7);
  assert.deepEqual(t.photos.a, { idx: 0, defects: [{ type: "cover_cut" }], obstructions: [{ label: "obstacle" }],
                                 entrances: [{ steps: 0, sill_unsure: false }] });
  assert.equal(t.photos.b, undefined);
  assert.throws(() => core.normalizeTruth({ clips: {} }), /not a survey truth file/);
});

test("photos are matched to files by image id and sorted by idx", () => {
  const out = core.matchPhotos(manifest, ["a.jpg", "zzz.jpg"]);
  assert.deepEqual(out.frames.map((f) => f.image_id), ["a", "b"]);
  assert.deepEqual(out.missing, ["b"]);
});

test("a second counts only when loaded, visible and recently active", () => {
  assert.equal(core.shouldCount(true, true, 100000, 50000), true);
  assert.equal(core.shouldCount(true, true, 100000, 40000), false);
  assert.equal(core.shouldCount(false, true, 100000, 99000), false);
  assert.equal(core.shouldCount(true, false, 100000, 99000), false);
});
