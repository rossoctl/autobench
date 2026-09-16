#!/usr/bin/env python3
"""Re-download a run set's S3 artifacts into the `mirror_dir` its run JSON recorded.

Why this exists: the `reference/gen-*.py` report generators read each run's artifacts out of
`mirror_dir`, which `run-12.py` wrote under `/tmp/autobench/...`. macOS prunes `/tmp`, so the mirror
disappears while the run JSON survives — and the generators then **degrade instead of failing**,
emitting "_No exported artifacts._" and "_(no trace)_" for the missing sections. Regenerating a
tracked report against a pruned mirror therefore silently deletes most of its content. Run this first.

Usage:
    python3 reference/remirror.py results/v1.28-dev146/run12-ocp-dev146.json [more.json ...]

Needs no credentials: the sink is public (anonymous read). Each run JSON already carries the exact
`artifacts_prefix` and artifact list per run, so nothing is guessed and no bucket listing is needed.
Existing files of non-zero size are left alone, making this cheap to re-run.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

S3_BASE = os.environ.get("BM_S3_BASE", "https://rossoctl-benchmarking.s3.us-east-1.amazonaws.com")


def fetch(url: str, dest: str) -> tuple[bool, str]:
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True, "cached"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except OSError as exc:
        return False, str(exc)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(body)
    return True, f"{len(body)} B"


def main(paths: list[str]) -> int:
    failures = 0
    for path in paths:
        doc = json.load(open(path))
        runs = doc.get("runs", [])
        got = missing = 0
        for run in runs:
            prefix, mirror = run.get("artifacts_prefix"), run.get("mirror_dir")
            if not prefix or not mirror:
                # A leg that never published (deploy failed, or the run errored before export) has
                # no prefix. That is not a mirror problem, so it is not counted as a failure.
                continue
            for name in run.get("artifacts") or []:
                ok, note = fetch(f"{S3_BASE}/{prefix}/{name}", os.path.join(mirror, name))
                if ok:
                    got += 1
                else:
                    missing += 1
                    failures += 1
                    print(f"  MISS #{run.get('n')} {name}: {note}", file=sys.stderr)
        print(f"{path}: {got} objects present, {missing} missing ({len(runs)} legs)")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
