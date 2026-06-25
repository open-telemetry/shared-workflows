# `.github/workflows/`

This directory holds the GitHub Actions YAML files for every workflow in this repository. [GitHub Actions only discovers workflows directly under this directory](https://docs.github.com/en/actions/writing-workflows/about-workflows) — YAMLs in subdirectories are silently ignored — so all `.yml` files must live flat here.

The YAML files here fall into three categories:

- **Reusable workflows** called by other repos via `uses:` (for example [`zizmor.yml`](./zizmor.yml)). The consumer-facing README lives in a companion docs folder at the repo root (for example [`zizmor/`](../../zizmor/)).
- **Centrally-executed workflows** that run from this repo against opted-in target repositories. Each ships one or more flat YAML files (grouped by filename prefix, e.g. `pull-request-dashboard*.yml`) plus a companion docs folder under root (for example [`pull-request-dashboard/`](../../pull-request-dashboard/)). Supporting scripts and the `repositories.json` opt-in list live under [`../scripts/<workflow-name>/`](../scripts/).
- **Repo-internal workflows** that only run for this repository (for example [`codeql.yml`](./codeql.yml) and [`scorecard.yml`](./scorecard.yml)). These are not shared and have no companion docs folder.

```
<workflow-name>/                          # root folder, shared workflows only
  README.md                               # consumer-facing docs

.github/workflows/
  <workflow-name>.yml                     # all YAML lives here flat (required)
  <workflow-name>-<other>.yml             # multi-YAML workflows use a filename prefix

.github/scripts/<workflow-name>/          # centrally-executed workflows only
  repositories.json                       # per-workflow opt-in list
  <workflow scripts...>
```

See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the full proposal process, the per-workflow README convention, and code ownership requirements.
