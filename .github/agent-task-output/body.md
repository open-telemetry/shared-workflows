Unblocks dashboard backfills when a run has completed most of its matrix but a protected-environment job remains in `waiting` without a runner, as in [this stalled run](https://github.com/open-telemetry/shared-workflows/actions/runs/35879562232).

The watchdog cancels a partially completed run only when every unfinished job has remained in `waiting` for at least 30 minutes without receiving a runner or starting a step and a newer run is queued. It reads every page of the job list before deciding, so active work past the first 100 jobs cannot be missed.
