# Threshold (working title)

Pick an area on the map; AI turns the open street-level photos already on Mapillary into an accessibility survey of shop entrances (steps, ramps, doors) and sidewalks (obstacles on tactile paving), with photo evidence. No fieldwork.

## Built during the Meta Global AI Developer Hackathon

All code in this repository was written during the hackathon, starting after kickoff on 2026-10-26 12:00 PM ET. The `spike/` folder contains the day-1 feasibility tests.

## Setup

Create a file named `.env` in the repo root (it is git-ignored) with two lines:

```
MODEL_API_KEY=<your Meta Model API key>
MAPILLARY_TOKEN=<your Mapillary client token>
```
