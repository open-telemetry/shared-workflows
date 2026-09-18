Adds the queue collector to the existing workflow-failure issue lifecycle.

Scheduled and manual full-collector runs now open or update a tracking issue when collection fails or is cancelled, then close it after the next successful monitored run. Fork runs remain excluded, and only the notification job receives `issues: write`.
