Replace the dashboard's general Copilot CLI classifier with isolated, tool-free Python SDK sessions. Each batch still contains up to ten discussions. A run reuses one client but starts a fresh session for each batch and invalid-output retry. The task-specific system prompt avoids the default coding-agent prompt overhead, though token and cost savings have not been measured.

Backfills now evaluate each PR before Git persistence retries, as targeted updates already do. A push conflict rereads the latest dashboard state and applies the existing evaluation rather than rerunning the model. This retry change is independent of the SDK switch; either runner could work either way.

Production dashboard workflows install the pinned SDK and download its matching runtime. Evaluation scripts use the same SDK runner, and production and evaluation cache keys include the classifier configuration so results from the old setup are not reused.

The workflow watchdog now checks every job page before deciding that a dashboard run is stalled. It cancels a partially completed run only when all remaining jobs are stale, waiting, and unassigned, and rejects inconsistent pagination instead of acting on incomplete data.
