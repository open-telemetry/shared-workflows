Make pull request dashboard runs recover from isolated classification and delivery failures without discarding successful work.

- Require Copilot responses to match the requested discussion IDs and order. Retry structurally invalid responses once while preserving successful classifications and leaving CLI failures and timeouts for the next run.
- Retry GitHub HTTP 499 responses. Treat confirmed 404 pull request lookups as removed status-comment targets while leaving other delivery failures pending.
