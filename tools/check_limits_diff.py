#!/usr/bin/env python3
"""Compare two limits_generated.py snapshots and decide if a refresh can auto-ship.

Compares EFFECTIVE limits (generated layer with CURATED overrides applied), so a
registry regression that a curated entry already corrects is not a blocker.

Verdicts (printed as `verdict=<v>`, also appended to $GITHUB_OUTPUT if set):
  unchanged  no name property added, removed or changed -> nothing to release
  safe       only additions and/or tightened limits     -> auto-release
  review     an effective limit was RAISED or a property was REMOVED -> needs a
             human (a looser limit lets names pass that AWS may reject; a removal
             silently stops checking a property)

A markdown report goes to stdout and to $GITHUB_STEP_SUMMARY if set.

Usage:
  python tools/check_limits_diff.py OLD_limits_generated.py NEW_limits_generated.py \
      [--curated python/cfn_name_check/limits.py] [--report report.md]
Exit code: 0 for unchanged/safe, 3 for review.
"""
from __future__ import annotations
import argparse, os, runpy, sys


def _load(path, var):
    # limits.py does a relative import of limits_generated inside try/except
    # ImportError; run standalone it falls back to GENERATED = {} — we only need CURATED.
    return runpy.run_path(path, run_name="_limits_snapshot")[var]


def _flat(db):
    return {(t, e[0]): e[1] for t, entries in db.items() for e in entries}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--curated", default=os.path.join("python", "cfn_name_check", "limits.py"))
    ap.add_argument("--report", default=None, help="also write the markdown report here")
    a = ap.parse_args(argv)

    old, new = _flat(_load(a.old, "GENERATED")), _flat(_load(a.new, "GENERATED"))
    curated = _flat(_load(a.curated, "CURATED"))
    eff = lambda k, db: curated.get(k, db.get(k))  # noqa: E731  curated wins

    added = sorted(new.keys() - old.keys())
    removed = sorted(k for k in old.keys() - new.keys() if k not in curated)
    raised, tightened, masked = [], [], []
    for k in sorted(old.keys() & new.keys()):
        if old[k] == new[k]:
            continue
        before, after = eff(k, old), eff(k, new)
        if k in curated:
            masked.append((k, old[k], new[k], curated[k]))
        elif after > before:
            raised.append((k, before, after))
        else:
            tightened.append((k, before, after))

    if not (added or removed or raised or tightened or masked):
        verdict = "unchanged"
    elif raised or removed:
        verdict = "review"
    else:
        verdict = "safe"

    fmt = lambda k: f"`{k[0]}.{k[1]}`"  # noqa: E731
    lines = [f"## Limits refresh: **{verdict}**", "",
             f"- added: {len(added)}", f"- removed: {len(removed)}",
             f"- raised (looser): {len(raised)}", f"- tightened: {len(tightened)}",
             f"- changed upstream but pinned by curated override: {len(masked)}", ""]
    if raised:
        lines += ["### Raised limits (blocking)", ""] + \
                 [f"- {fmt(k)}: {b} -> {n}" for k, b, n in raised] + [""]
    if removed:
        lines += ["### Removed properties (blocking)", ""] + \
                 [f"- {fmt(k)} (was {old[k]})" for k in removed] + [""]
    if tightened:
        lines += ["### Tightened limits", ""] + \
                 [f"- {fmt(k)}: {b} -> {n}" for k, b, n in tightened] + [""]
    if masked:
        lines += ["### Upstream changes masked by curated overrides", ""] + \
                 [f"- {fmt(k)}: registry {o} -> {n}, curated keeps {c}" for k, o, n, c in masked] + [""]
    if verdict == "review":
        lines += ["To ship anyway: confirm each blocking item against the AWS API reference, "
                  "add a curated override in `python/cfn_name_check/limits.py` where the registry "
                  "is wrong, then merge this PR and tag a release.", ""]
    if added:
        lines += [f"<details><summary>{len(added)} added properties</summary>", ""] + \
                 [f"- {fmt(k)}: {new[k]}" for k in added] + ["", "</details>", ""]
    report = "\n".join(lines)

    print(report)
    print(f"verdict={verdict}")
    for env, text in (("GITHUB_STEP_SUMMARY", report + "\n"), ("GITHUB_OUTPUT", f"verdict={verdict}\n")):
        if os.environ.get(env):
            with open(os.environ[env], "a", encoding="utf-8") as f:
                f.write(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    return 3 if verdict == "review" else 0


if __name__ == "__main__":
    sys.exit(main())
