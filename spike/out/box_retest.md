# Box re-test (spec section 20 fallbacks)

Same frozen labels as the D1 gate. Hit rate is on positives (stepped entrances, labeled tactile photos); false-positive rate is on negatives (flat entrances, other photos).

| Test | Method | Hit rate | Positives | Threshold | Result | False-positive rate | Negatives |
|---|---|---|---|---|---|---|---|
| stairs | concept | 0.375 | 8 | 0.7 | FAIL | 0.000 | 5 |
| stairs | box | 1.000 | 8 | 0.7 | PASS | 0.800 | 5 |
| stairs | either | 1.000 | 8 | 0.7 | PASS | 0.800 | 5 |
| tactile | concept | 0.892 | 37 | 0.8 | PASS | 0.050 | 40 |
| tactile | box | 0.973 | 37 | 0.8 | PASS | 0.154 | 39 |
| tactile | either | 0.973 | 37 | 0.8 | PASS | 0.154 | 39 |

Cost: $1.19 (Spark $0.64, SAM $0.56); failed attempts: 0

## Notes

Written by hand after the run (2026-10-27). Labels are the D1 gate's frozen `truth.json` (made by Claude from the photos, see `gate_report.md`). Stills are the clip photos at full resolution (2048×1536); the gate's SAM saw 1280-wide video. Spark boxes use a 0–1000 scale; SAM box prompts follow dev.meta.ai/docs/sam/segmenting.

- **Tactile paving passes once SAM sees full-resolution stills.** `yellow tactile paving` alone hits 32/37 (0.865) with 2 negatives fired; adding `tactile paving` gives 33/37 (0.892) for the same 2. The gate's video run got 0.649. The two fired negatives (80, 91) were left out of the labels as faint or distant glimpses, so they are likely real paving. Concept misses: 8, 69, 70, 75 (faded ochre blocks and a strip at the frame edge). 9 of the 10 labeled stretches are hit by the single phrase; the miss is the one-photo stretch at 49.
- **Spark box → SAM box on tactile** hits 36/37 (0.973) but fires on 6/39 negatives: 80 and 91 as above, 27 (the clinic landing in that photo carries a tactile strip the labels did not cover), and 46, 48, 55 (46 was noted as unclear while labeling). Photo 61 has no box result: Spark returned text that was not JSON, and the run recorded it as an error rather than a miss. Box prompting costs a Spark call per photo for at most +2 hits over the concept phrase.
- **Stairs: the box method finds every stepped entrance (8/8), but Spark says "step" at 4 of the 5 entrances labeled flat.** Its notes for those four: "low concrete sill", "low stone sill/threshold strip … slightly raised", and twice a diamond-plate ramp/sill. These are exactly the 0-vs-1 cases the gate report flagged as ambiguous in the labels (a ramp plate usually covers a lip). Photos cannot settle whether a few-cm sill is present, so precision at the 0/1 boundary is unmeasured, not 0.2.
- **SAM `stairs` on a single still is weaker than on the video window** (3/8 vs 5/8). The video window gives SAM five photos around the entrance; two of the 2-riser entrances (92, 95) were found in video but not in the single still. The still keeps a 0.0 false-positive rate.
- **What this means for spec section 20.** Sidewalk layer: tactile now passes, and obstacles already passed (0.882), so it passes. Entrance layer: entrances with 2+ risers are detected reliably by SAM on video (5/5 in the gate, no false stairs); a single low sill is only flagged by Spark and cannot be confirmed from photos. Suggested pipeline: SAM video `stairs` for flights of steps; the Spark box check reported as "possible sill, needs review" (⚪, as spec section 20 already allows for uncertain counts); tactile from SAM `yellow tactile paving` on full-resolution stills.
- **Production cost with that pipeline**: tactile on stills adds about $0.25 per 100 photos ($2.50 per 1k images) in place of three video phrases ($0.06), and the sill check adds about $0.11 per 100 photos (it runs on every entrance Spark reports: 24 in these 291 photos, about 8 per 100, at $0.014 each). Starting from the gate's $0.247, the total is about $0.55 per 100 photos, under the $0.6 limit but close to it. Sampling every second photo for tactile would halve the tactile part, because tactile paving runs continuously.
- Re-test spend: stairs $0.19, tactile $1.19; 0 failed API attempts. The box-prompt probe before it (3 photos) cost about $0.05 and is logged separately (`threshold/spike/out/calls_box_probe.jsonl`).
