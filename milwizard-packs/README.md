# milwizard-packs

Rate packs for Mil Wizard. This directory exists for one reason: to serve `latest.json` over HTTPS so the app can refresh its pay tables without a store release.

It lives inside the `ephemerislabs.github.io` site repo, so GitHub Pages serves it at

    https://ephemerislabs.github.io/milwizard-packs/latest.json

which is the `DATA_PACK_URL` compiled into the app. (Drill Wizard's packs live in their own repo, `DrillWizard-packs`. Mil Wizard's are here because a project repo could not be created from the release session; if a `milwizard-packs` repo with Pages is created later, it shadows this path automatically and the files move there unchanged.) The site repo must stay public.

## What the app does

Mil Wizard fetches `latest.json` once per day when opened, and on *Settings → Check for rate updates*. Accepted packs are cached in the member's own storage. The built-in pack stays as a floor: a failed or rejected fetch never leaves the app without rates.

## The file format

`latest.json` is an object with a `packs` array, schema version 3, the same as Drill Wizard's:

```json
{
  "schema_version": 3,
  "generated": "2026-09-18",
  "packs": [ { "pack_id": "2026", ... } ]
}
```

Only three keys make a pack valid: `pack_id`, `basic_pay`, and `effective.military_pay_start`. Everything else is optional; the app backfills a missing table from its built-in pack (`PACK_FILL` in the app). A January refresh can ship only the tables that changed.

The 2026 pack here is the app's built-in CY2026 pack exported in full, plus the two tables the app cannot carry in the binary:

| Table | Source | Refreshed by |
| --- | --- | --- |
| `tsp_prices` | tsp.gov daily share prices | `mw-tsp-prices` workflow, weekday evenings |
| `oconus_mie` | DTMO OCONUS per diem supplement | `mw-oconus-rates` workflow, the 2nd of each month |

Both updaters are ported from Drill Wizard unchanged and are fail-closed: a bad feed fails the job and writes nothing.

## Publishing a new pack

1. Add the new pack object to the `packs` array. Keep the old ones; the app picks by date.
2. Set its `generated` later than the one it replaces. A pack with the same `pack_id` only replaces the cached one when `generated` is greater.
3. Set `effective.military_pay_start` to the date the new tables take effect.
4. Commit to `main`. Pages redeploys in about a minute and the app picks it up on its next check.

## Rate calendar

| Table | Rolls |
| --- | --- |
| Basic pay, BAS, DLA, TLE, TSP limits, FICA wage base | 1 January |
| CONUS per diem (GSA) | 1 October (compiled into the binary; needs a store release) |
| OCONUS per diem (DTMO) | monthly (this pack) |
| TSP share prices | daily (this pack) |

Locality BAH, the ZIP to MHA map, CONUS per diem and the state-tax table are compiled into the binary and still need a store release; the app's rate freshness panel says so.

© 2026 Ephemeris Labs LLC
