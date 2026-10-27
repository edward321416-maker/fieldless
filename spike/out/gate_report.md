# D1 Gate Report

Decision: **NO-GO: apply fallbacks from spec section 20 and re-test**

Demo area: **Korea**

| Criterion | Value | Threshold | Result | Sample size |
|---|---|---|---|---|
| kr_recent_pedestrian_photos | 444 | 300 | PASS | seoul_myeongdong, not capped |
| spark_entrance_recall | 0.769 | 0.8 | FAIL | 13 scored |
| sam_stairs_rate | 0.625 | 0.7 | FAIL | 8 scored / 0 failed |
| step_count_accuracy | 0.538 | 0.7 | FAIL | 13 scored / 0 failed |
| tactile_coverage | 0.649 | 0.8 | FAIL | 37 scored / 0 failed |
| obstacle_detection_rate | 0.882 | 0.7 | PASS | 17 scored / 0 failed |
| cost_per_100_photos_usd | 0.247 | 0.6 | PASS | 291 photos |
| seconds_per_100_photos | 224.080 | 300.0 | PASS | 291 photos |

## Fallbacks (spec section 20)

- spark_entrance_recall: Spark reads only; entrance candidates come from SAM `door` detection
- sam_stairs_rate: switch to box prompts (Spark boxes → SAM) and re-test before demoting
- step_count_accuracy: height estimate simplified to standard riser height × step count; uncertain counts shown as ⚪
- tactile_coverage: switch to box prompts (Spark boxes → SAM) and re-test before demoting

Spike total cost including phrase exploration: $1.11
Failed API attempts (possibly billed): 0 (SAM 0, Spark 0, 0 s); their upper-bound cost $0.00 is included in the spike total
calls.jsonl: 108 entries read, 0 unreadable lines skipped
Kilometers of street in the test clips: 0.67; production cost per km: $1.07
`cost_per_100_photos_usd` is a production estimate: Spark, the chosen stair phrase, all three tactile phrases (tactile_coverage is their union) and the obstacle labels. `seconds_per_100_photos` is sequential API time, an upper bound.

Spark chunk errors: 0; entrances reported outside their chunk: 0
Chosen stair phrase: stairs
Chosen tactile phrase per clip: street_b.mp4: yellow tactile paving
D1 finding: Muse Spark samples uploaded video once every 2 s; Spark clips hold each photo 2 s (config.SPARK_HOLD)

## Extra measurements (not gate criteria)

- Step-count accuracy on stepped entrances (truth step_count >= 1): 0.750 (n=8)
- Stair detection rate on entrances with 2+ steps (phrase stairs): 1.000 (n=5)
- Obstacle detection rate on events whose label Spark also listed for the clip: 1.000 (n=14)

## Notes

Written by hand after the run (2026-10-27). Labels in `spike/clips/truth.json` were made by Claude from photo-by-photo inspection, not by a human surveyor, and were frozen before any paid run. Samples are small (8 stepped entrances, 13 entrances, 37 tactile photos), so one sample moves a rate by 8–13 points; read every rate as a direction, not a precise value.

- **Entrance recall (10/13).** Misses: a restaurant door at street_a 64, a glass lobby at street_b 83, and MAISON MINED at street_b 92 (three photos from Opening Project at 95, which took the only prediction near both). One more hit passes. The 14 false positives are mostly real doors the labels skipped on purpose (too far, too oblique or unclear threshold, e.g. street_a 39, 47, 99, 157), so precision is understated.
- **Stairs (5/8, phrase `stairs`).** All 5 entrances with 2+ risers were found (c 0.71–0.94, best frame within 2 photos of the label), and none of the 5 flat entrances produced a stair mask. All 3 misses are single-riser sills (emart24, Rainbow, the milling shop). The failure is specific to low sills, not to stairs. `steps` and `staircase` tied with `stairs`; `entrance steps` found 1/8.
- **Step count (7/13 exact, 13/13 within ±1).** On stepped entrances it is 6/8. Four of the five flat entrances were read as 1 step: two have diamond-plate ramp plates over the threshold (WM fashion store, Fruit of the Loom), one is a frameless glass door on a mat (Calvin Klein), one a glass lobby. The labels count a ramp-plated threshold as 0 steps with a ramp; the plan's rule "a lip of a few cm counts as 1" could also be read as 1 step plus a ramp. Under that reading accuracy would be 9/13 (0.69), still short. Spark leans toward reporting a step at any visible threshold.
- **Tactile (any phrase 0.649; `yellow tactile paving` 0.622, `tactile paving` 0.243, `braille block` 0.0).** Misses cluster on faded, ochre-brown blocks (street_b 69–76) and on bright strips that run along the frame edge at an oblique angle (83–88). Detections also appeared at 50, 55, 90 and 91, outside the labeled ranges; 90 and 91 were left out of the labels as too faint. `braille block` is useless as a phrase.
- **Obstacles (15/17).** Both misses are `car` events (a white sedan at 42–44, a van at 84–88). Every label Spark also listed for the clip was detected (14/14). No obstacle stood on the tactile line in these photos, so `on_tactile` was false for all events and the overlap check was not exercised.
- **API behaviour.** 108 calls, 0 failed attempts, 0 uploaded files left on the server. SAM returns an empty string when nothing is found. Frame indices stayed inside each window (52 stair files and the probe checked). The Task 7 probe on one entrance (street_b 27) ran first and was logged separately (`calls_d1_probe7.jsonl`).
- **Decision by spec section 20.** Both core metrics failed, so neither layer is demoted yet: the spec's next step for both is box prompts (Spark gives boxes, SAM segments inside them), re-tested on the same labels. Obstacle detection, cost and speed already pass. Human labeling time was not measured, because Claude labeled; the human baseline for the speed comparison is still owed (D4).
