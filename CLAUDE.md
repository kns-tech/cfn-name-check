# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`cfn-name-check` computes the **worst-case** resolved length of AWS resource name properties in CloudFormation templates and flags those that exceed service limits. Two deliverables, cfn-lint style:

- `python/` — pip package `cfn-name-check` (engine + CLI). All analysis lives here.
- `extension/` — thin VS Code wrapper that shells out to the Python CLI and renders its JSON as diagnostics, hovers, quick fixes and a status bar item. It contains no analysis logic.

Design rule: the extension is meant to be "install once, never update". New behavior, limits and fixes go in the Python core. Only touch the extension when the JSON contract or the editor UX changes.

## Commands

```bash
# Python core (run from repo root)
pip install -e ./python pytest
python -m pytest tests/ -q                                  # full regression suite (what CI runs)
python -m pytest tests/test_engine.py::test_strict_mode_takes_longest_branch -q   # single test
cfn-name-check tests/fixtures/regression.yml --show-info    # manual run
cfn-name-check - --filename x.yml --format json < x.yml     # stdin mode, as the extension calls it
python -m build python -o dist                              # wheel + sdist

# Regenerate the limits DB from CloudFormation registry schemas
pip install -U cfn-lint
python tools/generate_limits.py --from-cfnlint -o python/cfn_name_check/limits_generated.py

# Extension
cd extension && npm install && npm run compile              # tsc -> out/
npx vsce package --allow-missing-repository                 # .vsix
```

To debug the extension, open the `extension/` folder in VS Code and press F5.

There is no linter or formatter configured.

## Architecture

**Engine (`python/cfn_name_check/engine.py`)**: `check_template(text, ...)` is the single entry point that the CLI, the tests and (indirectly) the extension all use.
- `CfnLoader` is a PyYAML SafeLoader that turns CFN short-form tags (`!Sub`, `!If`, ...) into long-form dicts. The engine keeps the YAML node tree so it can map each finding back to source line and column ranges (`find_node` / `node_range`).
- `ConditionEvaluator` enumerates every combination of the `AllowedValues` of the parameters referenced by Conditions (capped at `MAX_CONDITION_COMBOS = 512`; past that it falls back to longest-branch). Each `!If` is evaluated under each combination, so unreachable branches are pruned ("exact mode"). `strict=True` skips pruning and always takes the longest branch.
- `Resolver` resolves expressions to worst-case strings. A parameter resolves to its longest AllowedValue, then its Default, then a MaxLength placeholder. `!Ref`/`!GetAtt` to another resource recurse into that resource's own name property, and resources with no name are modeled as CFN-generated names. `AWS::Region` resolves to `ap-southeast-1`.
- The stack name is taken from `Description` via `DESCRIPTION_REGEX_DEFAULT` (`StackName=...`) or an override. If neither is available, the engine runs in **budget mode** and reports the maximum stack-name length each property tolerates (`maxStackNameLen`).
- Severities: over limit → error; within `warn_margin` → warning; otherwise info. Values that are only known at deploy time (an unknown stack name, or a free-form parameter) get the warnings controlled by `deploy_time_threshold`.

**Limits DB, three layers** (`limits.py`). On conflicts for the same type + property path, the later layer wins:
1. `limits_generated.py`: auto-generated from registry schemas, ~725 types. **Never hand-edit it**; regenerate it with `tools/generate_limits.py`. The generator only selects top-level string props ending in Name/Identifier/AliasName/Family that have a `maxLength`.
2. `CURATED` in `limits.py`: hand-verified corrections, nested paths (`[]` = every list element) and extra constraints such as `{"lowercase": True}` (used for S3 and ECR).
3. User `extraLimits` (`--extra-limits` JSON / `cfnNameCheck.extraLimits`), applied through `merge_extra`.

**CLI ↔ extension contract**: the extension calls `python -m cfn_name_check.cli - --filename <f> --format json --fail-on never` and sends unsaved buffer content on stdin. The JSON is a list of per-file results with `findings[]`. Each finding carries `range`, `removeRange` and `removable` (used by the "Remove name property" quick fix), plus `worstCase`, `length`, `limit`, `margin` and `maxStackNameLen` (built in `engine._mk`). Renaming or removing these fields breaks extensions that are already installed, so only add fields.

**CLI settings mirror the VS Code settings** (`stackName`, `descriptionRegex`, `warnMargin`, `strictMode`, `extraLimits`, ...) so CI and the editor get the same results. When you add an option, add it in both places.

**Extension security model** (`extension/src/extension.ts`): settings that affect which process runs or where updates come from (`pythonPath`, `updateCheck`, `updateGithubRepo`, `updatePackage`) are read through `cfgGlobal()`, which reads user-level values only, and they are declared with `"scope": "application"` in `package.json`. The goal is that a malicious workspace `.vscode/settings.json` can't redirect execution or `pip install`. Keep that pattern for any new setting of this kind.

## Hard-won gotchas

- **Never spawn with `shell: true`** in the extension. On Windows, cmd.exe treated the `|` in the default `descriptionRegex` as a pipe. That silently broke every check and the Python probe (fixed in 0.1.1). Always spawn the interpreter directly.
- **The extension must compile before anything is tagged.** Once, a half-finished TS change shipped and the tagged CI run failed with TS2367. String-literal settings need explicit generics, e.g. `cfg<"off"|"pypi"|"github">(...)`. Run `npm run compile` after any `extension.ts` edit.
- Condition pruning: a Condition that references a free-form parameter (no AllowedValues) leaves that parameter unassigned and falls back per condition. Pruning must still work for every other condition (`test_free_form_condition_param_does_not_disable_pruning`).
- Generator quirks worth keeping: it keeps props that are both readOnly and createOnly (a registry quirk, e.g. SageMaker `EndpointName`), and it resolves local `$ref` chains into `definitions`. Regenerate on a clean machine: a bogus type once leaked in from a sandbox's copy of cfn-lint.
- The stack-name convention must stay pluggable (a regex setting/flag, not hard-coded). The tool is meant for general use, not just KNS's `pipeline-{repo}-{env}` convention.

## Tests

`tests/test_engine.py` pins exact behavior (lengths, worst-case strings, severities) against `tests/fixtures/regression.yml`. Its Description sets the stack name to `pipeline-example-sample-service-api-dev`. Many assertions hard-code lengths, so editing the fixture usually means updating those numbers. CI runs tests **only** on version tags and manual dispatch (`release.yml`) and in the monthly `refresh-limits` workflow, not on pushes or PRs. Run them locally before tagging.

## Releases

- Core version is in **two places** and they must match: `python/pyproject.toml` and `python/cfn_name_check/__init__.py` (`__version__`). The extension has its own independent version in `extension/package.json`.
- Release flow: bump both core version strings → `git tag vX.Y.Z && git push --tags`. `.github/workflows/release.yml` fails the build if the tag doesn't match `pyproject.toml`. Otherwise it runs the tests and builds the wheel and `.vsix` into a GitHub Release (publishing to PyPI exists but is disabled with `if: false`).
- Public repo: https://github.com/kns-tech/cfn-name-check. Users install the core wheel from GitHub Releases. The extension's update check can point at `kns-tech/cfn-name-check` (github mode).
- `HANDOFF.md` (local only, not committed — may be absent) holds project history, the incident log and the agreed next steps (PyPI trusted publishing, Marketplace, CodeBuild lint stage). Read it for context on in-flight work.
- `python/build/` is a stale setuptools build directory that is committed to git. Edit `python/cfn_name_check/`, never `python/build/lib/`.
