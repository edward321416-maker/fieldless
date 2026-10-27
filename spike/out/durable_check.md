# Durable sidewalk defects check (2026-10-27)

Why: most Korean Mapillary photos are a year or more old (Myeongdong: 1,690 of 1,922 from 2025), so a scooter on the tactile paving in a photo is usually gone by now. This check asks whether the sidewalk layer can report defects that stay true: (A) broken, missing or interrupted tactile paving, and (B) spots blocked again and again across photos from different dates.

## A. Tactile paving condition from one still (Muse Spark)

- Input: 38 full-resolution stills (2048×1536) from `street_b` (Seongsu), one structured Spark call each. Prompt and schema: `spike/run_s7_probe.py` (taken unchanged from the plan-1 draft probe, written before the labels).
- Labels: `spike/s7_labels.json`, written by Claude from ground crops before any model output was seen. 4 structural defects, 7 worn (faded), 18 intact, 9 excluded as unclear. The sample is small; read the numbers as direction only.
- Cost: $0.52 for 38 calls; median $0.012 per call (4.3k input tokens, 1.6k output tokens of which 1.5k reasoning), median 9 s.

| Label | Photos | Flagged as structural (abrupt end, broken, missing) | Any flag (also covered, worn) |
|---|---|---|---|
| structural | 4 | 1 (64) | 3 (64; 82 "covered"; 45 "worn") |
| worn | 7 | 1 (73 "broken") | 3 (70 worn, 73 broken) |
| intact | 18 | 0 | 1 (83 "worn") |

Per photo:
- **64** (crossing pad with missing blocks): `abrupt` + `missing`. Correct.
- **63** (same crossing, farther): `normal`, `none`. Missed, but the spot is still found through 64.
- **82** (guide line cut by a manhole cover): `covered`, and the note says "interrupted by a manhole cover set directly into its path". Right finding in the wrong class. Fixed covers (manholes, plates) should count as structural.
- **45** (entrance branch with no main line in 44 or 46): `normal`. A single photo cannot know the main line is missing; this needs the neighbouring photos.
- **Worn**: 3 of 7 blurred photos came back `unclear` (71, 75, 76) and 72 came back `none`. Fading from one photo depends on light and blur and is not reliable.
- **Intact**: no structural false flags on 18. The one flag (83, "faded to tan") is plausible; the worn/intact line in the labels is itself subjective.
- **Excluded**: 94 and 95 (dot strip stopping short of the crossing, a crosswise bar block) and 58 were flagged `abrupt`. These may be real; they were too unclear to label.

Reading: Spark rarely raises false structural alarms on intact paving (0 of 18), and it finds 2 of the 3 structural spots if fixed covers count. It misses defects that need context from neighbouring photos. It is usable as **"possible defect, review"** candidates with the evidence photo, but not as an accuracy headline. Fading should not be an automatic class.

Cost in production: at $0.012 per photo, checking every tactile photo would add about $0.36 per 100 photos (about 30% of photos show tactile paving) on top of the plan-1 estimate of $0.57. Run it only on candidates (ends of SAM tactile runs, and a sample of the rest), or test a smaller image size first.

## B. Repeat spots: photos of the same spot from different dates

`spike/run_repeat_coverage.py`, metadata only (free). Flat (non-panorama) photos since 2020, 15 m grid cells.

| Area | Cells with photos | Cells with dates ≥180 days apart | Same, pedestrian (`on_foot`) photos only |
|---|---|---|---|
| Seongsu | 261 | 21 (8%), median gap 778 days | **0** |
| Myeongdong | 302 | 18 (6%), median gap 829 days | **0** |

Reading: almost every street has been photographed once. The few repeated spots come from vehicle-mounted photos, which rarely show the tactile paving well. **Repeat-obstacle spots are not feasible** with current Mapillary coverage in the demo areas. Plan 1's collect step can keep only the newest sequence per street.
