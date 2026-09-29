#!/usr/bin/env python3
"""Point KinD's OTEL collector at the no-auth MLflow front end, idempotently.

Why this exists
---------------
rossoctl-deps installs the collector with its MLflow exporter behind an oauth2client
extension that fetches a token from Keycloak with the `mlflow` client's credentials. On a
rebuilt cluster those credentials no longer match the realm, and the export fails like this:

    error  Exporting failed. Dropping data.
      {"otelcol.component.id": "otlphttp/mlflow", ...,
       "error": "... request to http://mlflow:5000/v1/traces responded with HTTP Status Code 401",
       "dropped_items": 3}

That line is the whole bug, and nothing upstream of it complains: the collector *receives* the
agent's spans happily, the benchmark run succeeds, and the published report carries
`llm_input_tokens: 0` with `model: "unknown"` on every task. It reads like the agent never
emitted telemetry. The only visible tell is that `span_report.ndjson` holds exactly the four
spans the Service names itself (Agent.Session, MCP.CreateSession, Agent.Call,
Evaluator.Evaluate) and nothing from the agent pod — because those four do not travel through
the collector.

The fix is to export to `mlflow-reader` (deploy/kind/mlflow-reader.yaml) instead, which serves
the same postgres with no auth. That is also *why* the reader exists: on KinD the OIDC-gated
MLflow refuses the Service's read AND the collector's write, so both sides move to the reader
and the OIDC dependency leaves the KinD path entirely.

What it changes
---------------
Only the three keys that matter, in place, leaving everything else rossoctl-deps put in the
ConfigMap untouched:

    exporters."otlphttp/mlflow".traces_endpoint  -> the reader's /v1/traces
    exporters."otlphttp/mlflow".auth             -> removed
    extensions."oauth2client/mlflow"             -> removed (and from service.extensions)

Re-running is a no-op. Used by reference/kind-post-setup.sh; safe to run by hand:

    python3 reference/kind-collector-mlflow.py                 # patch + restart
    python3 reference/kind-collector-mlflow.py --check         # report only, exit 1 if unpatched
    python3 reference/kind-collector-mlflow.py --print-experiment-id

The experiment id is READ from the collector rather than chosen, because an instance config that
names a different one produces the same zero-token report as a refused write.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - operator script
    sys.exit("pyyaml required: pip install pyyaml, or run via `uv run python`")

NAMESPACE = "rossoctl-system"
CONFIG_MAP = "otel-collector-config"
CONFIG_KEY = "base.yaml"
DEPLOYMENT = "otel-collector"
EXPORTER = "otlphttp/mlflow"
EXTENSION = "oauth2client/mlflow"
DEFAULT_READER = "http://mlflow-reader.rossoctl-system.svc.cluster.local:5000/v1/traces"


def kubectl(ctx: str, *args: str, check: bool = True) -> str:
    cmd = ["kubectl", "--context", ctx, *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        sys.exit(f"{' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout


def load_config(ctx: str) -> dict:
    raw = kubectl(ctx, "-n", NAMESPACE, "get", "cm", CONFIG_MAP,
                  "-o", f"jsonpath={{.data.{CONFIG_KEY.replace('.', chr(92) + '.')}}}")
    if not raw.strip():
        sys.exit(f"{CONFIG_MAP} has no {CONFIG_KEY} — is this a rossoctl-deps cluster?")
    return yaml.safe_load(raw)


def experiment_id(cfg: dict) -> str | None:
    headers = cfg.get("exporters", {}).get(EXPORTER, {}).get("headers", {}) or {}
    value = headers.get("x-mlflow-experiment-id")
    return None if value is None else str(value)


def needs_patch(cfg: dict, reader: str) -> list[str]:
    exporter = cfg.get("exporters", {}).get(EXPORTER)
    if exporter is None:
        sys.exit(f"exporter {EXPORTER!r} not in the collector config — nothing to point at MLflow")
    pending = []
    if exporter.get("traces_endpoint") != reader:
        pending.append(f"traces_endpoint {exporter.get('traces_endpoint')!r} -> {reader!r}")
    if exporter.get("auth"):
        pending.append(f"drop exporter auth {exporter['auth']!r}")
    if EXTENSION in (cfg.get("extensions") or {}):
        pending.append(f"drop extension {EXTENSION!r}")
    if EXTENSION in (cfg.get("service", {}).get("extensions") or []):
        pending.append(f"drop {EXTENSION!r} from service.extensions")
    return pending


def apply_patch(ctx: str, cfg: dict, reader: str) -> None:
    exporter = cfg["exporters"][EXPORTER]
    exporter["traces_endpoint"] = reader
    exporter.pop("auth", None)
    (cfg.get("extensions") or {}).pop(EXTENSION, None)
    svc = cfg.setdefault("service", {})
    svc["extensions"] = [e for e in (svc.get("extensions") or []) if e != EXTENSION]

    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": CONFIG_MAP, "namespace": NAMESPACE},
        # The collector reads one embedded document, so the ConfigMap value is a string.
        "data": {CONFIG_KEY: yaml.safe_dump(cfg, sort_keys=True)},
    }
    # Apply from a file, not from a pipe: the ConfigMap was created by a Helm chart and has no
    # last-applied annotation, so `kubectl apply` warns and adopts it — worth seeing in full.
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        yaml.safe_dump(manifest, fh, sort_keys=False)
        path = fh.name
    try:
        print(kubectl(ctx, "apply", "-f", path).strip())
    finally:
        Path(path).unlink(missing_ok=True)

    # The collector loads its config once at start.
    kubectl(ctx, "-n", NAMESPACE, "rollout", "restart", f"deploy/{DEPLOYMENT}")
    print(kubectl(ctx, "-n", NAMESPACE, "rollout", "status", f"deploy/{DEPLOYMENT}",
                  "--timeout=180s").strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--context", default="kind-rossoctl", help="kubectl context (default: %(default)s)")
    ap.add_argument("--reader-url", default=DEFAULT_READER, help="MLflow /v1/traces URL to export to")
    ap.add_argument("--check", action="store_true", help="report only; exit 1 if a patch is pending")
    ap.add_argument("--print-experiment-id", action="store_true",
                    help="print the exporter's x-mlflow-experiment-id and exit")
    args = ap.parse_args()

    cfg = load_config(args.context)

    if args.print_experiment_id:
        exp = experiment_id(cfg)
        if exp is None:
            print("no x-mlflow-experiment-id header on the collector's MLflow exporter",
                  file=sys.stderr)
            return 1
        print(exp)
        return 0

    pending = needs_patch(cfg, args.reader_url)
    if not pending:
        print(f"collector already exports to {args.reader_url} with no auth — nothing to do")
        return 0

    for item in pending:
        print(f"  - {item}")
    if args.check:
        return 1
    apply_patch(args.context, cfg, args.reader_url)
    print(f"collector now exports traces to {args.reader_url} "
          f"(experiment id {json.dumps(experiment_id(cfg))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
