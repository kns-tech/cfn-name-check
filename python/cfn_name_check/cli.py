"""cfn-name-check CLI.

Examples:
  cfn-name-check template.yml
  cfn-name-check template.yml --stack-name my-very-long-stack-name
  cfn-name-check - --filename template.yml --format json   # stdin (editor use)
  cfn-name-check "infra/**/*.yml" --format json
"""
from __future__ import annotations
import argparse, contextlib, glob, json, signal, sys
from .engine import check_template, DESCRIPTION_REGEX_DEFAULT

with contextlib.suppress(Exception):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

SEV_ORDER = {"error": 0, "warning": 1, "info": 2}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cfn-name-check",
                                 description="Worst-case AWS resource name length checker for CloudFormation templates.")
    ap.add_argument("paths", nargs="+", help="Template file(s), glob patterns, or '-' for stdin")
    ap.add_argument("--filename", default="<stdin>", help="Reported filename when reading stdin")
    ap.add_argument("--stack-name", default=None, help="Override stack name (else parsed from Description)")
    ap.add_argument("--description-regex", default=DESCRIPTION_REGEX_DEFAULT,
                    help="Regex with one capture group to extract the stack name from Description")
    ap.add_argument("--warn-margin", type=int, default=5, help="Warn when margin <= N chars (default 5)")
    ap.add_argument("--deploy-time-threshold", type=int, default=64,
                    help="Warn when fewer than N chars remain for a deploy-time value (default 64)")
    ap.add_argument("--strict", action="store_true",
                    help="Conservative mode: take the longest !If branch even if unreachable")
    ap.add_argument("--extra-limits", default=None, help="JSON file with additional {type: [[prop, limit, note]]} entries")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--fail-on", choices=["error", "warning", "never"], default="error")
    ap.add_argument("--show-info", action="store_true", help="Include passing (info) findings in text output")
    args = ap.parse_args(argv)

    extra = None
    if args.extra_limits:
        with open(args.extra_limits) as f:
            extra = json.load(f)

    files = []
    for p in args.paths:
        if p == "-":
            files.append(("-", args.filename))
        else:
            matched = glob.glob(p, recursive=True) or [p]
            files.extend((m, m) for m in matched)

    results = []
    for src, reported in files:
        try:
            text = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
        except OSError as e:
            print(f"error: cannot read {src}: {e}", file=sys.stderr)
            return 2
        try:
            results.append(check_template(
                text, filename=reported, stack_name=args.stack_name,
                description_regex=args.description_regex,
                warn_margin=args.warn_margin, strict=args.strict,
                extra_limits=extra,
                deploy_time_threshold=args.deploy_time_threshold))
        except Exception as e:  # malformed YAML etc. -> report, don't crash editor
            results.append({"file": reported, "isTemplate": False,
                            "parseError": str(e), "findings": []})

    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        _print_text(results, args.show_info)

    worst = min((SEV_ORDER.get(f["severity"], 2)
                 for r in results for f in r["findings"]), default=2)
    if args.fail_on == "never":
        return 0
    if args.fail_on == "error":
        return 1 if worst == 0 else 0
    return 1 if worst <= 1 else 0


def _print_text(results, show_info):
    for r in results:
        if not r.get("isTemplate"):
            if r.get("parseError"):
                print(f"{r['file']}: parse error: {r['parseError']}")
            continue
        shown = [f for f in r["findings"] if show_info or f["severity"] != "info"]
        header = f"{r['file']}  (stack: {r.get('stackName') or 'budget mode'})"
        print(header)
        if not shown:
            print("  all name lengths OK")
        for f in sorted(shown, key=lambda x: (SEV_ORDER[x["severity"]], -(x.get("length") or 0))):
            loc = f["range"]["startLine"] + 1
            print(f"  {f['severity'].upper():7} L{loc:<4} {f['resource']}.{f['property']}: {f['message']}")
        print()


if __name__ == "__main__":
    sys.exit(main())
