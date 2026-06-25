# `.github/workflows/`

This directory holds the GitHub Actions YAML files for every shared workflow in this repository. GitHub Actions requires the YAML to live here.

Workflows in this repo come in two shapes:

- **Reusable workflows** called by other repos via `uses:` (for example [`zizmor.yml`](./zizmor.yml)). These are typically a single YAML file with no supporting scripts.
- **Centrally-executed workflows** that run from this repo against opted-in target repositories. These keep their supporting scripts, per-workflow `README.md`, and `repositories.json` opt-in list in their own folder under [`.github/scripts/<workflow-name>/`](../scripts/). A single centrally-executed workflow may ship more than one YAML file here.

```
.github/scripts/<workflow-name>/      # only for centrally-executed workflows
  README.md                           # what the workflow does and how to use it
  repositories.json                   # per-workflow opt-in list
  <workflow scripts...>
.github/workflows/<workflow-name>.yml # the YAML, here for both shapes
```

See [`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the full proposal process, the per-workflow README convention, and code ownership requirements.
