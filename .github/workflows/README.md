# `.github/workflows/`

This directory holds the GitHub Actions YAML files for every workflow in this repository. GitHub Actions requires the YAML to live here.

The YAML files here fall into three categories:

- **Reusable workflows** called by other repos via `uses:` (for example [`zizmor.yml`](./zizmor.yml)). The consumer-facing README for a reusable workflow lives in its root-level folder (e.g. [`zizmor/README.md`](../../zizmor/README.md)). The YAML must stay here because [GitHub Actions requires reusable workflows to live in `.github/workflows/`](https://docs.github.com/en/actions/using-workflows/reusing-workflows#calling-a-reusable-workflow).
- **Centrally-executed workflows** that run from this repo against opted-in target repositories. The YAML and supporting scripts live under `.github/`; the consumer-facing README and the `repositories.json` opt-in list live alongside the scripts under [`.github/scripts/<workflow-name>/`](../scripts/), with the public-facing README mirrored at the repo root (e.g. [`pull-request-dashboard/README.md`](../../pull-request-dashboard/)). A single centrally-executed workflow may ship more than one YAML file here.
- **Repo-internal workflows** that only run for this repository (for example [`codeql.yml`](./codeql.yml) and [`scorecard.yml`](./scorecard.yml)). These are not shared and have no root folder.

```
<workflow-name>/                          # root folder, shared workflows only
  README.md                               # consumer-facing docs

.github/scripts/<workflow-name>/          # centrally-executed workflows only
  repositories.json                       # per-workflow opt-in list
  <workflow scripts...>

.github/workflows/<workflow-name>.yml     # all YAML lives here (required by GitHub)
```

See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the full proposal process, the per-workflow README convention, and code ownership requirements.
