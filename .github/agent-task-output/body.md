Replace the dashboard's general Copilot CLI classifier with the Python SDK's isolated, tool-free sessions. Each batch still contains up to ten discussions; a run reuses one client but starts a fresh session for each batch and invalid-output retry. The smaller task-specific system prompt avoids the default coding-agent prompt overhead, though token and cost savings have not been measured.

Separately, backfills now evaluate each PR before Git persistence retries, as targeted updates already do. A push conflict rereads the latest dashboard state and applies the existing evaluation rather than rerunning the model. This retry change is independent of the SDK switch; either runner could work either way.

Workflows install the pinned SDK and download its matching runtime. Evaluation scripts use the same SDK runner, and production and evaluation cache keys include the classifier configuration so results from the old setup aren't reused.
