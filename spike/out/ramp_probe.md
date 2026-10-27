# Ramp probe (plan 4 Task 0)

Photos: 17, ramp visible: 12. Cost: $0.21.

| phrase | found on visible | found on not visible |
|---|---|---|
| curb cut | 10/12 | 1/5 |

Spark on visible ramps whose domes were judged yes/no by eye: 9/11 right, 1 unclear; agrees with the city record on 7/11.

| photo | ramp | city DW | seen | Spark | conf | note |
|---|---|---|---|---|---|---|
| 421513123303632 | CBR-5508 | N | no | no | 0.90 | The foreground curb edge is plain concrete with no yellow truncated-dome panel visible. |
| 1859712874536378 | CBR-8956 | N | no | no | 0.78 | The far curb where the crosswalk lands is plain gray concrete with no yellow truncated-dome pad visible. |
| 1388622938294798 | - | N | no | no | 0.90 | The curb where the rainbow crosswalk meets the sidewalk is plain concrete with no yellow truncated-dome panel visible. |
| 443029557885136 | - | N | no | no | 0.85 | The foreground curb where sidewalk meets the rainbow crosswalk is visible as plain concrete with no yellow truncated-dome panel. |
| 1135494587384233 | CBR-28731 | N | yes | yes | 0.95 | A yellow tactile panel with truncated domes is clearly visible at the base of the curb ramp by the crosswalk. |
| 5362983597112956 | CBR-9965 | N | yes | yes | 0.95 | A yellow detectable warning pad is clearly visible at the curb where the rainbow crosswalk meets the sidewalk. |
| 1026866854691216 | CBR-27047 | N | yes | unclear | 0.50 | The far corner curb ramp is visible but is too distant to confirm raised truncated domes, only a faint light patch at the edge. |
| 3490854744471068 | CBR-39793 | Y | yes | yes | 0.95 | A yellow truncated-dome pad sits at the curb edge just before the rainbow crosswalk. |
| 481649606865551 | CBR-11564 | Y | yes | no | 0.88 | The forward curb ramp where the sidewalk meets the crosswalk shows plain concrete with no yellow truncated-dome pad, the only yellow pad is on the side ramp to the right. |
| 1064667781086560 | CBR-14019 | Y | yes | yes | 0.92 | A yellow truncated-dome tactile pad is visible at the base of the curb ramp aligned with the crosswalk. |
| 620695656318142 | - | Y | yes | yes | 0.95 | A yellow detectable warning panel is clearly visible at the bottom edge of the central curb ramp. |
| 1938591246337969 | CBR-5290 | N | unclear | no | 0.85 | The foreground curb transition at the near end of the crosswalk is plain concrete with no yellow truncated-dome panel visible. |
| 3124848857805811 | CBR-22942 | N | not visible | no | 0.68 | The far crosswalk-to-sidewalk transition is visible and shows paint meeting concrete with no yellow truncated-dome panel. |
| 390830589688145 | CBR-8705 | N | not visible | unclear | 0.90 | No distinct perpendicular curb ramp is visible, only the continuous curb alongside the sidewalk and parked cars. |
| 371994845128583 | CBR-5362 | N | not visible | unclear | 0.95 | The photo shows a mid-block sidewalk with a continuous curb and parked cars but no curb ramp or intersection in view. |
| 1137016157203072 | CBR-14479 | N | not visible | unclear | 0.40 | The sidewalk-to-street transition is visible far ahead but is too distant to confirm presence of truncated domes. |
| 857040605681079 | CBR-27047 | N | not visible | unclear | 0.95 | The photo shows sidewalk alongside the street but no curb ramp meeting the street is clearly visible. |

## Decision (2026-10-28, 2b)

- **Pass.** SAM 3.1 finds the ramp with **"curb cut"** on 10/12 visible ramps (one false find on 5 photos without a ramp); "curb ramp" and "sidewalk ramp" find 0/12 (first run: `ramp_probe_phrases.md`). Muse Spark is right on 9/11 ramps whose domes were judged by eye (82%) and calls one distant ramp unclear, with the same prompt the pipeline will use (find = "curb cut").
- `pipeline/standards/us.toml`: `find = "curb cut"`; `ask` unchanged.
- The city record (2015) agrees on 7/11; at least 3 of the 4 differences are ramps the 2022 photos show with yellow domes, so the record is out of date there (2d labels).

Re-scored on 2d's relabelled probe set (2026-10-28: 3 gray pads now 'unclear', 1938591246337969 now 'no'): Spark is right on 7/9 decidable visible ramps, 1 unclear.
