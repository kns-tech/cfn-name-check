#!/usr/bin/env python3
"""Bump the core version in BOTH places it lives, and keep them in sync.

  python/pyproject.toml              version = "X.Y.Z"
  python/cfn_name_check/__init__.py  __version__ = "X.Y.Z"

Usage: python tools/bump_version.py patch|minor|major [--root .]
Prints the new version (e.g. 0.2.6) on stdout. Fails if the two files
disagree before the bump or if either replacement doesn't take.
The extension version (extension/package.json) is independent and untouched.
"""
from __future__ import annotations
import argparse, os, re, sys

FILES = {
    os.path.join("python", "pyproject.toml"): re.compile(r'^(version = ")(\d+)\.(\d+)\.(\d+)(")', re.M),
    os.path.join("python", "cfn_name_check", "__init__.py"): re.compile(r'^(__version__ = ")(\d+)\.(\d+)\.(\d+)(")', re.M),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=["patch", "minor", "major"])
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)

    texts, versions = {}, set()
    for rel, rx in FILES.items():
        path = os.path.join(a.root, rel)
        with open(path, encoding="utf-8", newline="") as f:
            texts[path] = f.read()
        m = rx.search(texts[path])
        if not m:
            sys.exit(f"error: no version found in {rel}")
        versions.add(tuple(int(x) for x in m.group(2, 3, 4)))
    if len(versions) != 1:
        sys.exit(f"error: version mismatch between files: {sorted(versions)}")

    major, minor, patch = versions.pop()
    if a.part == "major":
        major, minor, patch = major + 1, 0, 0
    elif a.part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"

    for (rel, rx), path in zip(FILES.items(), texts):
        text, n = rx.subn(lambda m: f"{m.group(1)}{new}{m.group(5)}", texts[path], count=1)
        if n != 1:
            sys.exit(f"error: failed to update {rel}")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    print(new)


if __name__ == "__main__":
    main()
