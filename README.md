# cfn-name-check

Worst-case AWS resource name length checking for CloudFormation templates.
Catches `Member must have length less than or equal to 64` **before** you
deploy, instead of mid-rollback.

## Install

From [GitHub Releases](../../releases) (grab the latest assets):

```bash
pip install cfn_name_check-<version>-py3-none-any.whl     # engine + CLI
code --install-extension cfn-name-check-<version>.vsix    # VS Code wrapper (one-time)
```

Or straight from source: `pip install "git+https://github.com/<you>/cfn-name-check#subdirectory=python"`

**Updating:** only the Python package ever needs updating (`pip install -U` the new
wheel). The extension is a frozen JSON renderer — install once, forget it.

## Architecture

Architecture mirrors cfn-lint: a pip-installable **Python core** does all
analysis; a thin **VS Code extension** renders its JSON output as native
diagnostics (squiggles, Problems panel, hovers, quick fixes).

```
cfn-name-check/
├── python/                 # pip package: engine + CLI
│   ├── cfn_name_check/
│   │   ├── engine.py       # YAML+tags loader, positions, condition eval, resolver
│   │   ├── limits.py       # curated name-limit database (~50 resource types)
│   │   └── cli.py          # cfn-name-check CLI (text / JSON, stdin support)
│   └── dist/               # built wheel + sdist
└── extension/              # VS Code wrapper
    ├── src/extension.ts
    └── cfn-name-check-0.1.0.vsix
```

## What it resolves

| Expression | Worst-case resolution |
|---|---|
| `${AWS::StackName}` | parsed from `Description` (`StackName=...`), or setting/CLI override, or budget mode |
| `!Ref <Parameter>` | longest AllowedValue → Default → MaxLength placeholder |
| `!If` | **exact mode** (default): conditions evaluated over all AllowedValues combinations, unreachable branches pruned; strict mode: longest branch |
| `!Ref` / `!GetAtt` to a resource | recursively resolves that resource's own name property; unnamed resources modeled as CFN-generated (truncated → safe) |
| `!Sub` (incl. var maps), `!Join`, `!Select`, `!FindInMap` | resolved structurally |
| `AWS::Region` | `ap-southeast-1` (longest common region) |

Severities: over limit = **Error**, within `warnMargin` (default 5) = **Warning**.
Deploy-time values (stack name not resolvable, or a parameter with no
Default/AllowedValues that gets typed into the console at deploy time) get a
**Warning** when the value could realistically overflow: for the stack name,
when the budget is below CloudFormation's 128-char stack-name cap; for
parameters, when fewer than `--deploy-time-threshold` chars (default 64)
remain after the fixed part. A fixed part that alone exceeds the limit is an
**Error** regardless of the value.
Every message includes the resolved worst-case string and the **max stack-name
length** that property tolerates. S3/ECR also get a lowercase check.

## Local testing (step by step)

### 1. Install the Python core

```bash
pip install /path/to/cfn-name-check/python/dist/cfn_name_check-0.1.0-py3-none-any.whl
# or editable while iterating:  pip install -e /path/to/cfn-name-check/python
```

Sanity check from the terminal:

```bash
cfn-name-check my-template.yml                       # stack name from Description
cfn-name-check my-template.yml --stack-name whatever # override
cfn-name-check my-template.yml --strict              # conservative branches
cfn-name-check "infra/**/*.yml" --format json        # batch / CI mode
```

Exit codes: 1 when errors found (configurable via `--fail-on error|warning|never`).

### 2. Install the extension

```bash
code --install-extension /path/to/extension/cfn-name-check-0.1.0.vsix
```

(Or in VS Code: Extensions panel → `...` menu → *Install from VSIX*.)

On Windows, if `python` isn't on PATH for VS Code, set
`cfnNameCheck.pythonPath` to the interpreter where you pip-installed the core
(e.g. your WSL or venv python won't be auto-found — point at the Windows one
you actually used).

### 3. Try it

Open any of your pipeline templates. Within ~half a second you should see:

- red squiggle on `EventBridgeExecutionRole.RoleName` with the full worst-case
  string and "Max stack name this tolerates: 34 chars"
- yellow squiggles on the near-limit names
- hover any flagged name → breakdown (worst case, length/limit, budget)
- lightbulb → **"Remove RoleName — let CloudFormation auto-generate"** quick fix
- status bar item bottom-right: `names: 2 over limit`
- Command Palette → **CFN Name Check: Scan Workspace** for a repo-wide report

It re-checks live as you type (debounced, unsaved content included — checked
via stdin, no temp files).

### 4. Developing the extension itself (optional)

```bash
cd extension && npm install && npm run compile
```

Open the `extension` folder in VS Code and press **F5** → Extension Development
Host launches with the extension loaded from source.

## Configuration (VS Code settings)

| Setting | Default | Purpose |
|---|---|---|
| `cfnNameCheck.stackName` | `""` | Fixed stack name override (skips Description parsing) |
| `cfnNameCheck.descriptionRegex` | `StackName=([^\s\|,;]+)` | How to extract the stack name — change this when your convention changes |
| `cfnNameCheck.warnMargin` | `5` | Warn when within N chars of the limit |
| `cfnNameCheck.strictMode` | `false` | Longest-branch `!If` handling |
| `cfnNameCheck.extraLimits` | `{}` | Add resource types: `{"AWS::X::Y": [["Name", 64, "note"]]}` |
| `cfnNameCheck.pythonPath` | auto | Interpreter with the core installed |
| `cfnNameCheck.showBudgetHints` | `true` | Info hints in budget mode |

The same knobs exist as CLI flags, so CI and editor share one engine and can't
drift.

## CI usage (the "alert" half)

```bash
pip install cfn_name_check-0.1.0-py3-none-any.whl
cfn-name-check "**/*.yml" --fail-on error
```

Wire into a CodeBuild lint stage — a failure trips your existing
pipeline-failure SNS/Lambda notification. Add `--stack-name` per template if
you generate names outside the Description convention.

## Known limitations (v0.1)

- Limits DB (v0.2.0) = auto-generated from the CloudFormation registry schemas
  (~725 types / ~890 name properties, via cfn-lint's vendored schema set)
  + ~50 hand-curated overrides that win on conflict + your `extraLimits`.
  Types whose registry schema publishes no maxLength for a name property
  still can't be checked. Refresh the snapshot any time:
  `pip install -U cfn-lint && python tools/generate_limits.py --from-cfnlint -o python/cfn_name_check/limits_generated.py`
  (or `--from-url https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip`
  to pull straight from AWS).
- `!ImportValue` and cross-stack references can't be resolved; findings are
  marked "length is a lower bound".
- Condition enumeration caps at 512 combinations, then falls back to
  longest-branch (conservative, never under-reports).

## Maintenance automation

- `.github/workflows/refresh-limits.yml` — monthly: regenerates the limits DB
  from the newest CloudFormation registry schemas and opens a PR when AWS adds
  or changes resource types. Merge = approve.
- `.github/workflows/release.yml` — on version tag: runs the regression suite,
  builds wheel + vsix, attaches both to a GitHub Release. Optional PyPI
  publishing via trusted publishing (see the commented job).

Release flow: merge changes → bump version in `python/pyproject.toml` and
`cfn_name_check/__init__.py` → `git tag v0.x.y && git push --tags`.
