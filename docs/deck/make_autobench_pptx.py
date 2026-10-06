#!/usr/bin/env python3
"""The AutoBench architecture & design deck: docs/AutoBench.pptx and docs/AutoBench.pdf.

    docs/deck/.venv/bin/python docs/deck/make_autobench_pptx.py           # .pptx, then the PDF
    DECK_PDF_VIA=none docs/deck/.venv/bin/python docs/deck/make_autobench_pptx.py   # .pptx only

The venv is the deck's own (docs/deck/requirements.txt), never the project's runtime:
    uv venv docs/deck/.venv && uv pip install --python docs/deck/.venv/bin/python \\
        -r docs/deck/requirements.txt

Generated, never hand-edited: an edit made in PowerPoint is lost at the next build. The .pptx and
the PDF come from the same run, so they cannot describe different decks.

Sources, in the order they win when two disagree:
  1. docs/results/v1.35-2026-10-04/  -- the generated 12-run and plugin-study reports. Every
     measured figure on a results slide is copied from there, with a comment naming the report.
  2. docs/img/takeaways.json         -- written by reference/gen-cost-charts.py from the same
     artifacts. The cost charts are drawn NATIVELY from its `data` blocks (never its PNGs), and
     their titles and take-aways are its sentences, so a slide cannot disagree with the figure.
  3. reference/model_prices.json + reference/gen-cost-analysis.py -- every dollar figure.
  4. docs/SERVICE_DESIGN_DECISIONS.md, docs/DEVELOPER_GUIDE.md, docs/ADMIN_GUIDE.md,
     docs/BENCHMARKS_PRIMER.md, docs/PLUGIN_OVERHEAD.md -- the design and the prose findings.
Figures are literals rather than read from artifacts at build time, because the deck must build
on a machine that has never seen a run. That makes them go stale silently: when a new matrix
supersedes v1.35, grep this file for the bare numbers, not just the version string.

The visual language (deck_kit.py's palette, used identically on every diagram):
  brain()        teal, filled    -- the A2A agent: the thing under test, doing the thinking
  hands()        grey, filled    -- the MCP server pod: the environment the agent acts on
  orchestrator() outlined hexagon -- a control plane: the AutoBench Service, Rossoctl
  store()        outlined cylinder -- MLflow, S3
  infra()        outlined box    -- a service called but not driven: Keycloak, the LLM gateway,
                                    the OTEL collector, the IBAC judge, the AuthBridge sidecar
  note()         red-tinted      -- a caveat, never a component
"""
import json
import pathlib
import sys
from datetime import date

from deck_kit import *
from deck_kit import _bullet, _runs

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent
OUT = DOCS / "AutoBench.pptx"
FIGS = json.loads((DOCS / "img" / "takeaways.json").read_text())["figures"]

# Chart series colours. One per quantity, the same on every chart in the deck; teal stays the
# agent's, so the agent's own bill is teal wherever it appears.
TOKENS = RGBColor(0x6B, 0x72, 0x80)      # slate: a count of tokens
TIME = RGBColor(0x3B, 0x6E, 0xA8)        # blue: wall-clock
DOLLARS = RGBColor(0xA3, 0x34, 0x6E)     # magenta: money
DOLLARS_LT = RGBColor(0xD9, 0xA3, 0xC0)  # light magenta: the input part of a dollar total
JUDGE = RGBColor(0xC9, 0x8A, 0x1E)       # amber: the IBAC judge's calls, billed off-telemetry
AGENT = ACCENT[1]                        # teal: the agent's own calls


# ------------------------------------------------------------------------------- helpers
def rich(s, x, y, w, h, items, size=13, gap=5):
    """One text frame of headings and bullets. items: ("h", text) a bold heading, ("b", text) a
    bullet, ("s", text) a muted sub-bullet, ("p", text) a plain paragraph."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.04)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    for i, (kind, t) in enumerate(items):
        assert t.strip(), "a blank line inside a text frame"
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(gap)
        if kind == "h":
            p.space_before = Pt(0 if i == 0 else 4)
            _runs(p, f"**{t}**", size + 1, INK)
        elif kind == "b":
            _runs(p, t, size, INK)
            _bullet(p, 0, size)
        elif kind == "s":
            _runs(p, t, size - 2, MUTED)
            _bullet(p, 1, size - 2)
        else:
            _runs(p, t, size, INK)
    return tb


def card(s, x, y, w, h, title, body, pal=INFRA, size=12, title_size=13, bullets=False):
    """A titled, top-anchored, left-aligned box: the deck's unit of prose. Size `h` to the
    content: a half-empty card reads as missing content."""
    return box(s, x, y, w, h, pal, title, body, size=size, title_size=title_size,
               align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, gap=3, bullets=bullets)


def head(s, x, y, w, t, pal=INFRA, h=0.46, size=15):
    """A column header bar."""
    return box(s, x, y, w, h, pal, t, size=size, shape=MSO_SHAPE.RECTANGLE)


def group(s, x, y, w, h, t, stacked=False, size=13):
    """A dashed outline holding components, its label top-left. `stacked` adds two offset
    outlines behind it: there are many of these."""
    if stacked:
        for off in (0.16, 0.08):
            box(s, x + off, y + off, w, h, (WHITE, RULE))
    box(s, x, y, w, h, (WHITE, INK), dash=True, line_w=1.25)
    text(s, x + 0.15, y + 0.06, w - 0.3, 0.34, [f"**{t}**"], size=size)


def legend(s, x, y, w, h, cols, size=11):
    """A flow legend panel: each column a list of lines."""
    box(s, x, y, w, h, (RGBColor(0xF6, 0xF7, 0xF9), RULE))
    cw = (w - 0.3) / len(cols)
    for k, lines in enumerate(cols):
        text(s, x + 0.15 + k * cw, y + 0.08, cw - 0.1, h - 0.16, lines, size=size, gap=1)


# ======================================================================= 1. the design
def s_overview(d):
    s = d.slide("What AutoBench is, and the seven decisions that shape it",
                "A pure-Python, HTTP-only service that deploys, runs and grades agent benchmarks "
                "on Rossoctl clusters")
    rich(s, 0.45, 1.45, 5.6, 5.4, [
        ("p", "Deploys a benchmark's MCP tool and A2A agent, runs the evaluation, reads the "
              "results back and exports them, all over HTTP."),
        ("h", "Objectives"),
        ("b", "Automate the benchmarking lifecycle"),
        ("b", "Share run results across users and clusters"),
        ("b", "One Service, many clusters: the caller's JWT `iss` picks the instance"),
        ("b", "Asynchronous, parallel benchmark runs"),
        ("b", "Secure, auditable, resilient"),
        ("h", "Three benchmarks"),
        ("b", "**gsm8k**, single-turn: one prompt in, one answer out"),
        ("b", "**tau2**, multi-turn: a user-simulator LLM plays the customer"),
        ("b", "**appworld**, long-horizon: many API calls across simulated apps"),
        ("h", "Client contract"),
        ("b", "A hostname and `Authorization: Bearer <caller JWT>`, the same on every cluster"),
    ], size=13)
    table(s, 6.35, 1.45, 6.53, [
        ["Decision", "Why"],
        ["Pure-Python, HTTPS-only to Rossoctl", "No shell-out: a smaller image, no injection "
         "surface, structured errors, testable"],
        ["Two-token auth", "The caller's JWT attributes and routes, and is never forwarded; "
         "the Service mints its own token"],
        ["`iss` is the trust anchor", "The issuer selects the instance: no instance argument, "
         "so no confused-deputy escape"],
        ["Per-request ROPC login", "A fresh Service token on every request: no expiry handling"],
        ["One config file per issuer", "Rossoctl URL, `benchmarker` credential, MLflow, S3, an "
         "optional Keycloak backchannel"],
        ["Enact only what the API allows", "Deploy, run, report, export, plugin presets; "
         "workload Secrets are prechecked (424), never ignored"],
        ["MLflow with the Service, S3 in the cloud", "Traces live beside the Service; S3 is the "
         "shared, cross-cluster sink for results"],
    ], [2.3, 4.2], size=12, row_h=0.64, right_from=99, bold_first_col=True)


def s_motif(d):
    s = d.slide("One client, one Service, N workload deployments",
                "The shape of the system before the wiring: who drives whom, and where every "
                "result lands",
                notes="Deliberately omits Keycloak (the auth model has its own slide), the "
                      "workload's internals and anything cluster-specific.")
    infra(s, 0.45, 1.45, 2.8, 0.75, "Client", ["host or CI"], size=12)
    orchestrator(s, 0.45, 2.55, 2.8, 1.55, "AutoBench Service", size=13)
    store(s, 0.45, 4.45, 1.35, 1.0, "Results store", ["S3"], size=11)
    store(s, 1.9, 4.45, 1.35, 1.0, "Tracker", ["MLflow"], size=11)
    group(s, 4.05, 1.4, 8.75, 4.1, "Workload deployment instances", stacked=True)
    orchestrator(s, 4.35, 2.45, 2.5, 1.2, "Deployment helper", ["Rossoctl"], size=12)
    box(s, 8.6, 2.0, 3.95, 1.95, (WHITE, ACCENT[1]), dash=True)
    text(s, 8.7, 2.04, 3.7, 0.3, ["**Benchmark workload**"], size=11)
    brain(s, 8.75, 2.45, 1.75, 1.3, "Agent", ["A2A"], size=12)
    hands(s, 10.65, 2.45, 1.75, 1.3, "MCP server", ["tasks + grading"], size=12)
    infra(s, 6.15, 4.55, 2.7, 0.7, "Telemetry collector", size=12)
    arrow(s, 1.85, 2.2, 1.85, 2.55); badge(s, 2.1, 2.37, 1)
    arrow(s, 3.25, 3.05, 4.35, 3.05); badge(s, 3.8, 2.85, 2)
    arrow(s, 6.85, 3.05, 8.75, 3.05); badge(s, 7.8, 2.85, 3)
    arrow(s, 3.25, 3.85, 8.6, 3.85); badge(s, 7.3, 3.85, 4)
    arrow(s, 2.35, 4.1, 2.35, 4.45); badge(s, 2.1, 4.27, 5)
    arrow(s, 9.6, 3.95, 8.85, 4.55, dash=True); badge(s, 9.35, 4.3, "6a")
    arrow(s, 6.15, 4.95, 3.25, 5.05, dash=True); badge(s, 4.7, 5.0, "6b")
    arrow(s, 3.0, 4.45, 3.0, 4.1); badge(s, 3.0, 4.27, 7)
    arrow(s, 1.1, 4.1, 1.1, 4.45); badge(s, 0.85, 4.27, 8)
    legend(s, 0.45, 5.75, 12.43, 1.15, [
        ["1  Client → Service",
         "2  Service → deployment helper: create the workload",
         "3  helper → workload",
         "4  Service → workload: run and monitor"],
        ["5  Service → tracker: emit the trace",
         "6a  agent → collector   ·   6b  collector → tracker",
         "7  tracker → Service: read the records back",
         "8  Service → results store: export the artifacts"],
    ])


def s_auth(d):
    s = d.slide("The caller's token is never forwarded upstream",
                "Two tokens: one says who is asking, the other is what the Service acts with")
    head(s, 0.45, 1.45, 6.0, "Caller JWT (inbound)")
    rich(s, 0.55, 2.05, 5.85, 3.3, [
        ("p", "Presented by the client as `Authorization: Bearer`."),
        ("b", "Used only to attribute (`preferred_username`) and route (`iss` → instance)"),
        ("b", "Signature validated against the issuer's JWKS"),
        ("b", "`aud` and `exp` deliberately lenient in the dev/ops context"),
        ("b", "**Never forwarded to Rossoctl**"),
        ("h", "`benchmarker` is special"),
        ("b", "A JWT whose `preferred_username` is `benchmarker` authorizes `GET`/`PUT /config`"),
    ], size=14)
    head(s, 6.88, 1.45, 6.0, "Service token (outbound)")
    rich(s, 6.98, 2.05, 5.85, 3.4, [
        ("p", "Minted per request by ROPC, with the instance's `benchmarker` credential."),
        ("b", "Always fresh, so no expiry handling"),
        ("b", "Presented to Rossoctl for every cluster-facing operation"),
        ("b", "**The Service acts under its own identity, not the caller's**"),
        ("h", "Backchannel split (`iss` ≠ the host dialled)"),
        ("b", "When the `iss` host is unreachable from the pod (in-cluster KinD), JWKS and "
              "tokens come from a backchannel URL; `iss` is matched, never dialled"),
    ], size=14)
    card(s, 0.45, 5.6, 12.43, 0.95, "Implication",
         ["At the Rossoctl and cluster layer every action is the Service's identity, so per-user "
          "attribution and audit live in the Service, keyed on (`iss`, `preferred_username`)."],
         size=13)


def s_boundary(d):
    s = d.slide("What the Service can and cannot enact",
                "The HTTP-only boundary, made explicit: anything else is reported, never "
                "silently ignored")
    head(s, 0.45, 1.45, 6.0, "Enacts over HTTP")
    rich(s, 0.55, 2.05, 5.85, 4.0, [
        ("b", "Deploy the MCP tool and A2A agent (CPU and memory, image, env)"),
        ("b", "Swap the model at deploy time (a per-experiment agent)"),
        ("b", "`authbridge_enabled` injects the sidecar (layer 2)"),
        ("b", "`plugin_preset`, `plugins`, `on_error` set AuthBridge layer 3"),
        ("s", "the operator renders the pipeline ConfigMap"),
        ("b", "Run benchmark sessions; collect pass/fail and latency"),
        ("b", "Emit and read MLflow traces; export to S3"),
        ("b", "Its own config, MLflow and S3, via `PUT /config` (`benchmarker` only)"),
    ], size=14)
    head(s, 6.88, 1.45, 6.0, "Out-of-band: reports it, does not do it")
    rich(s, 6.98, 2.05, 5.85, 4.0, [
        ("b", "Cluster Secrets (`hf-secret`, `openai-secret`)"),
        ("s", "the operator provisions them; the run precheck returns 424 naming the missing one"),
        ("b", "AuthBridge cluster config: `ibac.judgeEndpoint`, `judgeModel`"),
        ("s", "in the platform ConfigMap, not the agent API"),
        ("b", "Any cluster-level API call: Rossoctl does those server-side"),
        ("h", "The principle"),
        ("b", "An un-enactable request is prechecked (424) or rejected (422) with an actionable "
              "reason, never dropped"),
    ], size=14)
    card(s, 0.45, 6.0, 12.43, 0.75, "Cluster-agnostic by construction",
         ["The same chart on KinD and OpenShift; the `iss`-keyed instance decides where the "
          "workloads run."], size=12)


# ============================================================ 2. architecture & deployment
def s_architecture(d):
    s = d.slide("The architecture: one Service, a per-cluster instance selected by `iss`",
                "Client, Service and the cluster-specific Keycloak, Rossoctl and workload; "
                "MLflow sits with the Service, S3 in the cloud")
    infra(s, 0.45, 1.45, 2.75, 0.72, "Client", ["off-cluster: host or CI"], size=12)
    orchestrator(s, 0.45, 2.45, 2.75, 1.65, "AutoBench Service",
                 ["`iss`-keyed instances", "per-request ROPC", "deploy · run · report"], size=11)
    store(s, 0.45, 4.5, 1.3, 0.9, "S3", ["cloud"], size=11)
    store(s, 1.9, 4.5, 1.3, 0.9, "MLflow", ["with the Service"], size=10)
    group(s, 4.05, 1.4, 8.75, 3.95, "Per-cluster instance, selected by JWT `iss`", stacked=True,
          size=12)
    infra(s, 4.35, 1.95, 2.55, 0.9, "Keycloak", ["issuer + backchannel"], size=12)
    orchestrator(s, 4.35, 3.1, 2.55, 1.05, "Rossoctl", ["backend API"], size=12)
    box(s, 9.15, 1.9, 3.45, 2.3, (WHITE, ACCENT[1]), dash=True)
    text(s, 9.25, 1.94, 3.2, 0.3, ["**Benchmark workload**"], size=11)
    hands(s, 9.35, 2.3, 3.05, 0.8, "MCP tool", ["`exgentic-mcp-<benchmark>`"], size=11)
    brain(s, 9.35, 3.25, 3.05, 0.8, "A2A agent", ["`exgentic-a2a-tool_calling-<b>`"], size=11)
    infra(s, 6.15, 4.5, 2.7, 0.7, "OTEL collector", ["forwards agent spans"], size=11)
    arrow(s, 3.2, 1.8, 4.35, 2.2); badge(s, 3.72, 1.98, 1)
    arrow(s, 1.82, 2.17, 1.82, 2.45); badge(s, 2.08, 2.31, 2)
    arrow(s, 3.2, 2.75, 4.35, 2.6); badge(s, 3.78, 2.68, 3)
    arrow(s, 3.2, 3.6, 4.35, 3.62); badge(s, 3.78, 3.61, 4)
    arrow(s, 6.9, 3.62, 9.15, 3.3); badge(s, 7.6, 3.52, 5)
    arrow(s, 3.2, 4.0, 3.2, 3.0, head=False)
    arrow(s, 3.2, 3.0, 6.9, 3.0, head=False)
    arrow(s, 6.9, 3.0, 9.15, 2.9); badge(s, 5.05, 3.0, 6)
    arrow(s, 2.15, 4.1, 2.15, 4.5); badge(s, 2.15, 4.3, 7)
    arrow(s, 9.6, 4.05, 8.7, 4.5, dash=True); badge(s, 9.12, 4.28, "8a")
    arrow(s, 6.15, 4.85, 3.2, 5.0, dash=True); badge(s, 4.65, 4.93, "8b")
    arrow(s, 2.9, 4.5, 2.9, 4.1); badge(s, 2.9, 4.3, 9)
    arrow(s, 1.1, 4.1, 1.1, 4.5); badge(s, 0.84, 4.3, 10)
    legend(s, 0.45, 5.55, 12.43, 1.4, [
        ["1  Client logs in to Keycloak (ROPC): the caller JWT",
         "2  Client → Service: `Bearer <caller JWT>`",
         "3  Service validates the JWT (JWKS) and mints its own token",
         "4  Service → Rossoctl: deploy, list agents and tools",
         "5  Rossoctl creates the MCP tool and A2A agent"],
        ["6  Service drives the MCP session and the A2A call",
         "7  Service emits the `Agent.Session` trace → MLflow, first",
         "8a  agent → OTEL collector · 8b  collector → MLflow (optional)",
         "9  Service reads the traces back from MLflow",
         "10  Service exports `run.json`, `report.*` to S3"],
    ], size=11)


def s_workload(d):
    s = d.slide("Inside the workload: the fixed parts and the optional ones",
                "Dashed = present only in some configurations; with no preset, flows 5 and 6 "
                "are one direct agent → MCP call")
    infra(s, 0.45, 1.4, 12.43, 0.55, "LLM gateway (per instance): any OpenAI-compatible LLM or "
          "LiteLLM service", size=12)
    # The Service sits beside the agent container it prompts, the judge beside the sidecar that
    # calls it: the other way round, flows 1 and 7 cross.
    orchestrator(s, 0.45, 2.45, 2.0, 0.85, "AutoBench Service", size=11)
    infra(s, 0.45, 3.75, 1.95, 0.8, "IBAC judge", ["an LLM call"], size=11, dash=True)
    group(s, 2.75, 2.1, 10.13, 3.2, "Benchmark workload · `team1`", size=11)
    box(s, 3.0, 2.5, 4.5, 2.2, (WHITE, ACCENT[1]))
    text(s, 3.1, 2.52, 4.3, 0.28, ["**A2A agent pod**"], size=11)
    brain(s, 3.12, 2.82, 4.26, 0.75, "agent container", ["the LLM loop"], size=11)
    infra(s, 3.12, 3.85, 4.26, 0.75, "AuthBridge sidecar", ["a proxy, only with a preset"],
          size=11, dash=True)
    box(s, 8.2, 2.5, 4.5, 2.2, (WHITE, NEUTRAL[1]))
    text(s, 8.3, 2.52, 4.3, 0.28, ["**MCP tool pod**"], size=11)
    hands(s, 8.32, 2.82, 4.26, 0.75, "user simulator LLM", ["tau2 only: plays the customer"],
          size=11, dash=True)
    hands(s, 8.32, 3.85, 4.26, 0.75, "MCP server", ["tasks + evaluation"], size=11)
    arrow(s, 2.45, 2.95, 3.12, 3.15); badge(s, 2.78, 2.78, 1)
    arrow(s, 1.45, 3.3, 1.45, 3.52, head=False)
    arrow(s, 1.45, 3.52, 2.58, 3.52, head=False)
    arrow(s, 2.58, 3.52, 2.58, 4.98, head=False)
    arrow(s, 2.58, 4.98, 9.1, 4.98, head=False)
    arrow(s, 9.1, 4.98, 9.1, 4.6); badge(s, 6.0, 4.98, 2)
    arrow(s, 6.2, 2.82, 6.2, 1.95); badge(s, 6.2, 2.3, 3)
    arrow(s, 10.45, 2.82, 10.45, 1.95); badge(s, 10.45, 2.3, 4)
    arrow(s, 5.25, 3.57, 5.25, 3.85); badge(s, 5.6, 3.71, 5)
    arrow(s, 7.38, 4.25, 8.32, 4.25); badge(s, 7.85, 4.25, 6)
    arrow(s, 3.12, 4.3, 2.4, 4.2); badge(s, 2.9, 4.5, 7)
    arrow(s, 0.45, 4.15, 0.28, 4.15, head=False, dash=True)
    arrow(s, 0.28, 4.15, 0.28, 1.68, head=False, dash=True)
    arrow(s, 0.28, 1.68, 0.45, 1.68, dash=True); badge(s, 0.28, 2.95, 8)
    table(s, 0.45, 5.48, 6.6, [
        ["benchmark", "LLMs per task", "tool calls"],
        ["gsm8k", "1 (agent)", "~1"],
        ["tau2", "2 (+ user simulator)", "~11"],
        ["appworld", "1 (agent)", "~15"],
        ["+ any preset", "+1 judge call per action", "unchanged"],
    ], [1.6, 3.0, 1.4], size=10, row_h=0.29, right_from=99, bold_first_col=True)
    legend(s, 7.25, 5.48, 5.63, 1.45, [[
        "1  Service → agent: `send_prompt`, once per task",
        "2  Service → MCP server: the session, and the verdict",
        "3  agent → gateway: N chat calls per task",
        "4  tau2: the user simulator is a second LLM",
        "5–6  with a preset, MCP calls go via the sidecar",
        "7  sidecar → judge per action · 8  the judge is an LLM call",
    ]], size=10)


def s_interception(d):
    s = d.slide("The sidecar sees traffic only because of two env vars",
                "Interception is a blanket forward proxy plus an allowlist, not a tool-aware hook")
    for k, (t, body) in enumerate([
        ("`HTTP_PROXY = HTTPS_PROXY = 127.0.0.1:8081`",
         ["The operator injects it into the agent pod when AuthBridge is on. That address is "
          "the sidecar, so by default every outbound HTTP call is captured."]),
        ("`no_proxy`: gateway, collector, Keycloak",
         ["Instance config: the hosts that stay direct. The only reason the agent's own chat "
          "calls (flow 3) skip the sidecar."]),
        ("`judge_inference: false`",
         ["IBAC's default: even proxied inference is not judged. Both this and `no_proxy` "
          "would have to change for model calls to be authorized."]),
    ]):
        card(s, 0.45 + k * 4.2, 1.45, 4.03, 1.6, t, body, size=13, title_size=13)
    card(s, 0.45, 3.35, 6.1, 1.95, "Through the sidecar", [
        "agent → MCP tool calls (flows 5 → 6): the interception point",
        "`tools/call` is `isAction=true`: one judge call each (flow 7)",
        "inbound A2A `message/stream` is `isAction=false`: the Service's `send_prompt` is "
        "logged, never judged",
    ], size=13, bullets=True)
    card(s, 6.78, 3.35, 6.1, 1.95, "Direct: the plugins never see it", [
        "agent → LLM gateway (flow 3): named in `no_proxy`",
        "Service → MCP server (flow 2): a different pod, not dialled through the proxy",
        "MCP tool → gateway (flow 4, tau2's user simulator): a different pod",
    ], size=13, bullets=True)
    card(s, 0.45, 5.6, 12.43, 1.0, "The only test that settles it",
         ["Count judge completions across a run. IBAC logs nothing of its own, so a healthy "
          "sidecar log proves nothing; and a zero means something only if real tool calls ran."],
         size=13)


def s_bypass(d):
    s = d.slide("A ready sidecar proves nothing: the double bypass we shipped",
                "Four steps, each defensible alone; together the judge was called zero times, "
                "on both clusters",
                notes="Working config: leave workload_llm.disable_proxy off, keep the NAMED hosts "
                      "in no_proxy (gateway, collector, Keycloak), and drop the wildcards "
                      "(.svc, .svc.cluster.local).")
    for k, (t, body) in enumerate([
        ("1  The Service", ["`workload_llm.disable_proxy: true` makes it inject `HTTP_PROXY=\"\"` "
                            "first"]),
        ("2  The operator", ["adds a var only when it is absent: skips `HTTP_PROXY`, still sets "
                             "`HTTPS_PROXY`"]),
        ("3  The agent", ["`MCP_URL` is `http://`, so tool calls take the empty `HTTP_PROXY` and "
                          "go direct"]),
        ("4  The allowlist", ["`no_proxy` carried `.svc.cluster.local`: the same calls excluded "
                              "a second time"]),
    ]):
        card(s, 0.45 + k * 3.15, 1.5, 2.98, 1.6, t, body, size=13)
        if k < 3:
            arrow(s, 0.45 + k * 3.15 + 2.98, 2.3, 0.45 + (k + 1) * 3.15, 2.3)
    warn(s, 0.45, 3.4, 12.43, 1.0, "Result",
         ["Sidecar ready, pipeline ConfigMap rendered, plugins loaded, and the judge called "
          "ZERO times: a plausible config that takes interception back out of the path."],
         size=13)
    card(s, 0.45, 4.7, 6.1, 1.6, "The working config", [
        "leave `disable_proxy` off",
        "keep the named hosts in `no_proxy`",
        "drop the wildcards (`.svc`, `.svc.cluster.local`)",
    ], size=13, bullets=True)
    card(s, 6.78, 4.7, 6.1, 1.6, "How to verify it", [
        "count judge completions across a run, never sidecar logs",
        "`initialize` and `tools/list` are `isAction=false`: never judged",
        "so a zero count needs real tool calls behind it",
    ], size=13, bullets=True)


def s_install(d):
    s = d.slide("Installing and uninstalling: the scripts cover one cluster",
                "A cross-cluster service–workload setup is supported by the Service, and left to "
                "you",
                notes="reference/autobench-install.sh --env-file <env> [--dry-run]\n"
                      "reference/autobench-uninstall.sh --env-file <env> [--dry-run] "
                      "[--teams team1]\nDetails: docs/ADMIN_GUIDE.md §5, 'What the scripts "
                      "install: one cluster'.")
    head(s, 0.45, 1.45, 6.0, "`reference/autobench-install.sh`")
    rich(s, 0.55, 2.05, 5.85, 2.3, [
        ("b", "One env file, one command, on KinD or OpenShift"),
        ("b", "Preflight first: 0 failures, or it stops"),
        ("b", "MLflow read path, instance file, Secret, Helm, restart"),
        ("b", "Verifies `/healthz`, the S3 block, then preflight with the MLflow round trip"),
        ("b", "`--ibac-judge` adds the judge for the plugin legs"),
    ], size=13)
    head(s, 0.45, 4.45, 6.0, "`reference/autobench-uninstall.sh`")
    rich(s, 0.55, 5.05, 5.85, 1.8, [
        ("b", "Workloads first, through the Service; then `helm uninstall`"),
        ("b", "Verifies every owned object is gone and the judge fields restored"),
        ("b", "Removes only what the installer recorded creating"),
    ], size=13)
    head(s, 6.88, 1.45, 6.0, "Scope: one cluster")
    rich(s, 6.98, 2.05, 5.85, 1.0, [
        ("b", "The Service and its workloads on the same cluster"),
        ("s", "an instance file for that cluster's own `iss`; endpoint templates `null`"),
    ], size=13)
    card(s, 6.88, 3.05, 6.0, 2.2, "Cross-cluster service–workload: supported, not scripted", [
        "an instance file keyed to the workload cluster's `iss`: its Rossoctl URL, endpoint "
        "templates, `workload_otel`, the Service-side MLflow",
        "a collector on the workload cluster, forwarding to the Service's MLflow",
        "the IBAC judge on the workload cluster",
        "callers take their token from the workload cluster's Keycloak",
    ], size=12, bullets=True)
    text(s, 6.88, 5.4, 6.0, 0.7, ["The Service needs no change for it: only provisioning, "
                                   "which the scripts do not yet do."], size=12, color=MUTED)


# ===================================================================== 3. running a benchmark
def s_lifecycle(d):
    s = d.slide("The catalog, and the REST calls that drive a run",
                "Three benchmarks in a static registry; six calls from deploy to the artifacts")
    head(s, 0.45, 1.45, 5.0, "Catalog (static registry)")
    for k, (t, body) in enumerate([
        ("gsm8k", ["single-turn; needs `hf-secret` and `openai-secret`"]),
        ("tau2", ["multi-turn; the user-simulator LLM runs server-side in the MCP pod; raise "
                  "the timeout"]),
        ("appworld", ["long-horizon across simulated apps; raise `task_timeout_seconds`"]),
    ]):
        card(s, 0.45, 2.1 + k * 1.15, 5.0, 1.0, t, body, size=13, title_size=15)
    head(s, 5.75, 1.45, 7.13, "Lifecycle (REST)")
    table(s, 5.75, 2.1, 7.13, [
        ["call", "what it does"],
        ["`POST /benchmarks/{n}/deploy`", "create the MCP tool and A2A agent → 201"],
        ["`GET /benchmarks/{n}/status`", "poll until `tool_ready` and `agent_ready`"],
        ["`POST /benchmarks/{n}/runs`", "precheck → 202 `run_id` (409 not deployed, 424 a "
         "Secret missing)"],
        ["`GET …/runs/{id}`", "poll pending → running → succeeded or failed"],
        ["`GET …/runs/{id}/report`", "MLflow records and artifacts (409 if MLflow is unset)"],
        ["download from S3", "`run.json`, `report.*`, `token_report.*`, `span_report.*`, "
         "`manifest.json`"],
    ], [2.9, 4.2], size=12, row_h=0.6, right_from=99)


def s_bakes(d):
    # Source of truth: benchmarks/registry.py; keep in step with DEVELOPER_GUIDE.md §4.4.
    s = d.slide("The image carries the benchmark; `tool_env` adds credentials",
                "What each image bakes in, and the two env vars per benchmark that it does not")
    table(s, 0.45, 1.45, 12.43, [
        ["", "MCP tool image", "baked into the image", "added by `tool_env`"],
        ["gsm8k", "`exgentic-mcp-gsm8k`", "the HuggingFace loader, pinned to main/test: those "
         "1,319 rows, fetched at pod start", "`HF_TOKEN` (from `hf-secret`), plus "
         "`EXGENTIC_SET_BENCHMARK_RUNNER=direct`"],
        ["tau2", "`exgentic-mcp-tau2`", "tau2-bench at v0.1.3, its retail domain (114 tasks), and "
         "a user-simulator LLM", "`OPENAI_API_KEY` (from `openai-secret`) and an action timeout "
         "of 1000: the simulator makes its own calls"],
        ["appworld", "`exgentic-mcp-appworld`", "the whole app-suite sandbox at upstream "
         "`edc96012`, behind a custom tool-per-API adapter; test_normal split",
         "nothing beyond `BENCHMARK_NAME`"],
    ], [1.1, 2.4, 4.6, 4.3], size=12, row_h=0.62, right_from=99, bold_first_col=True)
    card(s, 0.45, 4.15, 6.1, 1.2, "One agent image serves all three",
         ["`exgentic-a2a-tool_calling:latest` is the only agent; per benchmark it gains a name "
          "suffix. The benchmark lives in the MCP pod; the subject is the same binary."], size=12)
    card(s, 6.78, 4.15, 6.1, 1.2, "The LLM base is never baked in",
         ["`OPENAI_API_BASE` comes per deploy from the instance's `workload_llm.api_base`; a "
          "deploy with no gateway configured is rejected (422), not defaulted."], size=12)
    warn(s, 0.45, 5.6, 12.43, 1.25, "Two traps in the last column", [
        "appworld rejects the action-timeout override tau2 needs, and crashes at start: the env "
        "is per benchmark, not a shared default.",
        "`hf-secret` must exist for gsm8k although the dataset is public, or the MCP pod sits in "
        "`CreateContainerConfigError`. A missing Secret surfaces as 424 on the precheck.",
    ], size=12, bullets=True)


def s_one_task(d):
    # Latencies are medians over the v1.35 mirrors; mirrors DEVELOPER_GUIDE.md §1.
    s = d.slide("One task, end to end: everything the agent does is one A2A turn",
                "The same system in time, with the span each step appears as")
    table(s, 0.45, 1.45, 12.43, [
        ["who", "step", "span"],
        ["Service", "`create_session(task_id)` → MCP pod", "`MCP.CreateSession`"],
        ["Service", "`send_prompt` → agent: the task text, `session_id` in A2A metadata; once per "
         "task", "`Agent.Call`"],
        ["agent", "`connect_mcp`: `tools/list` → MCP pod (per task; ~40–50 ms)", "`connect_mcp`"],
        ["agent", "`create_agent` (~12–15 ms warm, up to 5.2 s on a cold pod)", "`create_agent`"],
        ["agent", "`initial_observation`: local, no I/O, ~70 µs", "never counted"],
        ["agent", "the loop: chat → `execute_tool` → MCP pod → observation → chat → …",
         "`chat` · `execute_tool`"],
        ["agent", "returns its final message when the model asks for no further tool",
         "the runner discards it"],
        ["Service", "`evaluate_session` → MCP pod: the verdict, off the session's final state",
         "`Evaluator.Evaluate`"],
        ["Service", "`delete_session` → MCP pod, in a `finally`", "no span"],
    ], [1.1, 8.0, 3.3], size=11, row_h=0.36, right_from=99)
    card(s, 0.45, 5.25, 4.0, 1.3, "One A2A turn per task",
         ["`Agent.Call` minus the agent's `POST /` span is the Service's own cost: a median 16.3 "
          "ms of a 2.5 s gsm8k task, 15.9 ms of a 40 s tau2 one."], size=12)
    card(s, 4.67, 5.25, 4.0, 1.3, "The Service issues no tool call",
         ["Its MCP traffic is the session lifecycle. `execute_tool` is the agent's call; the "
          "Service never learns a tool's name."], size=12)
    card(s, 8.88, 5.25, 4.0, 1.3, "The session id joins the halves",
         ["It rides in A2A metadata, never the prompt: the agent's tool calls mutate the session "
          "the Service grades, so prose alone fails a gsm8k task."], size=12)


def s_who_decides(d):
    # Counts over every mirrored span_report.ndjson of the v1.35 pair, both platforms.
    s = d.slide("Nobody scripts a task's trajectory: the model produces it, step by step",
                "What each component defines, and what it does not")
    table(s, 0.45, 1.45, 12.43, [
        ["component", "defines", "does NOT define"],
        ["MCP pod", "the task list; each task's initial state and instruction; the tool surface; "
         "the evaluator", "any ordering: it answers calls, never asks"],
        ["agent runtime", "the loop (model → tool → observation → model) and when to stop",
         "which tool, with which arguments"],
        ["the Service", "one prompt per task (no tool instructions), the session lifecycle, the "
         "timeout, the telemetry", "anything about the trajectory"],
        ["the model", "every step", "—"],
    ], [2.0, 6.6, 3.8], size=12, row_h=0.52, right_from=99, bold_first_col=True)
    card(s, 0.45, 4.25, 6.1, 1.4, "Selection is deterministic; execution is not",
         ["Same task, model and platform, two legs: tau2 task 7 took 12 chat / 12 tool in one "
          "and 10 / 10 in the other. 14 of 20 repeated tau2 tasks and 10 of 10 appworld differ; "
          "all 20 gsm8k agree."], size=12)
    card(s, 6.78, 4.25, 6.1, 1.4, "The stop signal comes from the MCP pod",
         ["No terminal tool name exists in the Service. gsm8k calls `submit` 172/172; appworld "
          "`finish` 41/49; tau2 has no terminal tool, and 60/60 end on a message."], size=12)
    card(s, 0.45, 5.9, 12.43, 0.8, "Why it matters downstream",
         ["Grading reads the final state, never a reference trajectory: per-task cost and "
          "latency are distributions, hence the CV columns in every report."], size=12)


# ===================================================================== 4. the three benchmarks
def s_ladder(d):
    # v1.35 pair, both platforms pooled: 281 task rows, none with lost telemetry.
    s = d.slide("Three rungs of a ladder, each ~an order of magnitude dearer",
                "Per task, measured on our own v1.35 runs, both platforms pooled; not the "
                "benchmarks' published papers")
    table(s, 0.45, 1.45, 12.43, [
        ["", "gsm8k", "tau2", "appworld"],
        ["model (the default)", "Azure/gpt-5-mini", "aws/claude-sonnet-5", "gemini-2.5-pro"],
        ["its rate, in / out $ per 1M", "0.25 / 2.00", "1.52 / 7.60", "1.25 / 10.00"],
        ["what it tests", "multi-step arithmetic", "multi-turn dialogue + tools",
         "long-horizon app automation"],
        ["task rows measured", "172 of 172", "60 of 60", "49 of 50 (1 timed out)"],
        ["pass rate", "0.98", "0.90", "0.00"],
        ["input / output tokens", "343 / 170", "87,935 / 2,071", "291,509 / 26,950"],
        ["cost, at those rates", "$0.00053", "$0.149", "$0.634"],
        ["LLM / tool calls", "1.1 / 1.1", "11.1 / 11.1", "30.2 / 14.9"],
        ["median / slowest task", "2.5 s / 15 s", "48 s / 75 s", "222 s / 591 s"],
        ["task pool", "1,319 (main/test)", "114 (retail)", "168 (test_normal)"],
    ], [2.9, 3.17, 3.17, 3.17], size=12, row_h=0.36, right_from=99, bold_first_col=True)
    warn(s, 0.45, 5.6, 12.43, 1.25, "Read the model row before any dollar figure", [
        "The rungs do not run the same model, so no cost here transfers to another one. A tau2 "
        "task is ~256× a gsm8k task's input tokens and 282× its dollars; appworld ~850× and "
        "1,195×. Budget by benchmark, not by task count."], size=12)


def s_stresses(d):
    s = d.slide("What each benchmark stresses, and what a result from it tells you",
                "Why all three are in the matrix")
    for k, (t, lines) in enumerate([
        ("gsm8k: the canary", [
            "One prompt in, one answer out: ~1 LLM call and 1 tool call",
            "A failure means infrastructure: deploy, auth, LLM reach, telemetry",
            "Saturates near 1.0, so it cannot rank models",
            "Task 0 costs 320 input tokens on every cluster: proof two environments compare",
            "A 50-task leg is ~24 K tokens, less than one appworld task",
        ]),
        ("tau2: the discriminator", [
            "A server-side user-simulator LLM plays the customer; ~11 tool calls",
            "Retail domain (114 tasks), the library default, recorded nowhere",
            "Model choice dominates: 0.1 on gpt-5-mini, 0.9 on claude-sonnet-5, same 10 tasks",
            "Nondeterministic: the same 10 tasks scored 0.90 and 1.00 on two clusters",
            "22% of wall time is the simulator, whose tokens no report of ours sees",
        ]),
        ("appworld: the stress test", [
            "Chores across simulated apps: ~30 LLM calls, ~15 tool calls, ~290 K input",
            "Graded by programmatic tests over the final app state, no LLM judge",
            "0.00 is honest: runs complete and tokens record; the job is not done",
            "41 of 49 tasks call `finish`; the assertions disagree",
            "Tool shortlisting: two model calls per tool call, 67% of the input",
        ]),
    ]):
        card(s, 0.45 + k * 4.2, 1.45, 4.03, 3.1, t, lines, size=13, title_size=15, bullets=True)


def s_compared(d):
    s = d.slide("They differ in the shape of a task, not just its size",
                "What a task is, how it ends, and what a result from it is worth",
                notes="gsm8k is a smoke test with a score: reading its 0.98 as a model "
                      "measurement is the most common misreading. tau2 is the only rung that "
                      "discriminates, and it fails informatively. appworld earns its place "
                      "because it fails: it is the leg that exposed a timeout, a context limit or "
                      "a cache effect before a user did.")
    table(s, 0.45, 1.45, 12.43, [
        ["", "gsm8k", "tau2", "appworld"],
        ["a task is", "one grade-school word problem, answered in text",
         "one retail customer-service conversation with a simulated user",
         "one multi-app scenario automated through an API surface"],
        ["turns", "1 model call, ~1 tool call", "~11 calls alternating with the simulator",
         "~30 calls, each re-sending the whole conversation"],
        ["ends when", "the answer is emitted", "the dialogue resolves, or policy is violated",
         "the goal state is reached, or the 600 s timeout kills it"],
        ["scored by", "exact numeric match", "completion and policy compliance",
         "appworld's state assertions, all or nothing"],
        ["it tests", "that the plumbing works", "that the agent holds state and uses tools "
         "under a policy", "that the agent survives long horizons"],
        ["a result is worth", "a go/no-go on infrastructure", "a real model comparison, with "
         "headroom both ways", "a stress signal: what breaks, not who is better"],
        ["watch out for", "1.0 proves nothing about the agent", "the simulator is billed but not "
         "in our telemetry", "a killed task leaves no report row"],
    ], [1.9, 3.5, 3.5, 3.5], size=12, row_h=0.6, right_from=99, bold_first_col=True)


def s_traps(d):
    s = d.slide("Five things that mislead in reading the numbers",
                "Every one of these cost us a wrong conclusion first")
    for k, (t, body) in enumerate([
        ("1  `pass_rate` = evaluated passes / total",
         "A task that errors before evaluation counts as not passed: check the error column."),
        ("2  The `llm` column changed meaning between agent versions",
         "Agents up to exgentic 0.3.5.dev131 sent a counted probe; the current one sends none "
         "(absent across all 2,338 chat spans). Never compare across it."),
        ("3  Tiny tokens are lost telemetry, and `tokens == 0` will not catch it",
         "Use the structural test: `llm` ≤ 1 with `tool` ≥ 2 is impossible."),
        ("4  Output varies more than input, in most runs",
         "OUT CV > IN CV in 16 of 22 multi-task legs; only appworld is input-led. Read the CV."),
        ("5  Task selection is deterministic",
         "The first `max_tasks` tasks: the same `task_id` is the same task on every cluster."),
    ]):
        warn(s, 0.45, 1.45 + k * 1.08, 12.43, 0.95, t, [body], size=12, title_size=13)


def s_picking(d):
    s = d.slide("Pick a benchmark by what it is for",
                "Dollars at the rate card on the cost slides; v1.35 measured costs")
    table(s, 0.45, 1.45, 12.43, [
        ["if you want to …", "use", "costs"],
        ["check a cluster, deploy, auth or telemetry path works", "gsm8k, 1–10 tasks", "< $0.01"],
        ["exercise concurrency and volume cheaply", "gsm8k, 50 tasks at p=4", "$0.02"],
        ["compare models meaningfully", "tau2: it discriminates, gsm8k saturates", "$1.60 / 10"],
        ["stress long contexts, long tasks, timeouts", "appworld", "$3.10–3.40 / 5"],
        ["get a fast signal that nothing regressed", "gsm8k: if it fails, fix infrastructure",
         "< $0.01"],
    ], [5.5, 4.6, 2.3], size=13, row_h=0.5, right_from=2)
    card(s, 0.45, 4.7, 12.43, 1.3, "Budget by benchmark, not by task count",
         ["The eight gsm8k legs together are 0.2% of the matrix's bill on both platforms; "
          "appworld's two legs are 78% of it on OpenShift (77% on KinD). A 50-task gsm8k leg "
          "costs 2 cents, about a thirtieth of a single appworld task."], size=13)


def s_leg_cost(d):
    # Summed over the mirrored report.ndjson rows of the v1.35 pair.
    s = d.slide("What each leg costs: the same work, close to the same bill",
                "Tokens and dollars per leg, v1.35, OpenShift (OCP) and KinD")
    table(s, 0.45, 1.45, 7.0, [
        ["leg", "tok OCP", "$ OCP", "tok KinD", "$ KinD"],
        ["#1 gsm8k, 1 task", "406", "$0.0003", "406", "$0.0003"],
        ["#2 gsm8k, 10 tasks", "5.1 K", "$0.005", "5.1 K", "$0.005"],
        ["#3 gsm8k, 50 tasks p=4", "24 K", "$0.020", "25 K", "$0.022"],
        ["#9 tau2, 10 tasks", "0.96 M", "$1.59", "0.94 M", "$1.56"],
        ["#10 tau2, 20 tasks p=4", "1.74 M", "$2.88", "1.77 M", "$2.93"],
        ["#11 appworld, 5 tasks", "1.55 M", "$3.10", "1.71 M", "$3.39"],
        ["#12 appworld, 20 tasks p=4", "6.47 M", "$12.63", "5.87 M", "$11.94"],
        ["**all 12 legs**", "**10.8 M**", "**$20.25**", "**10.3 M**", "**$19.87**"],
    ], [2.6, 1.1, 1.1, 1.1, 1.1], size=12, row_h=0.42)
    card(s, 7.7, 1.45, 5.18, 1.45, "Within 2%, but not by law",
         ["The totals land within 2% ($20.25 / $19.87): the same request bodies through one "
          "gateway. appworld turn counts are nondeterministic, so size it on your cluster."],
         size=12)
    warn(s, 7.7, 3.1, 5.18, 2.15, "The totals understate it", [
        "a task killed by its timeout burns tokens and leaves no report row",
        "tau2's user simulator is billed by the gateway and counted nowhere here",
        "a replayed completion re-reports usage never made upstream",
        "the IBAC judge's calls are not in the agent's spans",
    ], size=12, bullets=True)


# ================================================================================== 5. cost
def s_cost_model(d):
    # Every dollar figure: reference/gen-cost-analysis.py over the v1.35 pair × model_prices.json.
    s = d.slide("Money amplifies the difficulty ladder rather than tracking it",
                "Per task, pooled over both platforms, at our gateway's posted rates")
    table(s, 0.45, 1.45, 6.6, [
        ["per task", "gsm8k", "tau2", "appworld"],
        ["model", "gpt-5-mini", "claude-sonnet-5", "gemini-2.5-pro"],
        ["LLM calls", "1.11", "11.10", "30.22"],
        ["total tokens", "513", "90,006", "318,459"],
        ["× gsm8k, in tokens", "1×", "175×", "621×"],
        ["cost", "$0.00053", "$0.149", "$0.634"],
        ["× gsm8k, in dollars", "1×", "282×", "1,195×"],
        ["cost of 100 tasks", "$0.05", "$14.94", "$63.39"],
        ["input share of tokens", "67%", "98%", "92%"],
        ["input share of cost", "32%", "89%", "57%"],
        ["time inside model calls", "82%", "60%", "95%"],
    ], [2.4, 1.4, 1.5, 1.5], size=12, row_h=0.4, bold_first_col=True)
    card(s, 7.3, 1.45, 5.58, 1.3, "The formula",
         ["cost per task = (input tokens × P_in + output tokens × P_out) / 1 M. Which model is "
          "cheaper is a property of the price list, not of the models."], size=13)
    card(s, 7.3, 2.95, 5.58, 1.6, "Why money climbs faster",
         ["The harder benchmarks also run the dearer models: tau2 is 175× a gsm8k task in tokens "
          "but 282× in dollars, appworld 621× but 1,195×. A budget scaled off the token ratios "
          "is short by 1.6–1.9×."], size=13)


def s_rate_card(d):
    s = d.slide("Output is priced 4–8× input at every provider",
                "The rate card: read off the gateway's admin UI on 2026-09-17, kept in "
                "`reference/model_prices.json`; posted rates, not an invoice")
    table(s, 0.45, 1.45, 12.43, [
        ["model (as `report.ndjson` records it)", "provider", "in $/1M", "out $/1M", "out/in",
         "used by"],
        ["Azure/gpt-5-mini-2025-08-07", "azure", "0.25", "2.00", "8.0×", "gsm8k"],
        ["gemini-2.5-pro", "vertex_ai", "1.25", "10.00", "8.0×", "appworld"],
        ["aws/claude-sonnet-5", "bedrock", "1.52", "7.60", "5.0×", "tau2"],
        ["Azure/gpt-4.1", "azure", "2.00", "8.00", "4.0×", "leg #4, and the IBAC judge"],
    ], [4.0, 1.6, 1.3, 1.3, 1.1, 3.1], size=13, row_h=0.46, right_from=2)
    card(s, 0.45, 4.05, 12.43, 1.15, "That one fact explains the counter-intuitive results",
         ["A reasoning model's verbosity is charged at the expensive end, so a model can win on "
          "tokens and lose on the bill. Read the input share of cost, not of tokens."], size=13)


def s_dollars_wrong(d):
    s = d.slide("Which way the dollars are wrong: one bias each way",
                "The judge makes our totals too low; an unmodelled cache discount, too high")
    warn(s, 0.45, 1.45, 6.1, 3.0, "Too low: the IBAC judge is not in our telemetry", [
        "~1 completion per authorized tool call, on Azure/gpt-4.1, the dearest model on the card",
        "a fixed 1,577-character system prompt that does not shrink with the task",
        "≥ $0.00111 per call: 2.6× the whole gsm8k task it authorizes",
        "1.7–3.4× the agent's own bill on legs #6–#8",
        "tau2's user simulator is invisible the same way",
    ], size=13, bullets=True)
    warn(s, 6.78, 1.45, 6.1, 3.0, "Too high: an unmodelled cache discount", [
        "the gateway reports cached input but publishes no cached-input rate",
        "`report.ndjson` stores one undifferentiated input count: no past run can be re-priced",
        "if cached input were free: tau2 floors at $0.0157 a task (not $0.149), appworld at "
        "$0.270 (not $0.634)",
        "gsm8k barely moves: caching pays off on a long re-sent prefix",
    ], size=13, bullets=True)


def _fig(d, key, kicker):
    f = FIGS[key]
    return d.slide(f["title"], kicker, notes=f"{f['caption']}\n\n{f['note']}".strip(),
                   outline=FIG_LABEL[key]), f


FIG_LABEL = {"ladder-amplification": "Chart: money vs tokens", "cost-composition":
             "Chart: share of the bill", "model-choice": "Chart: model choice", "leg-pareto":
             "Chart: where the bill goes", "cost-per-pass": "Table: cost per success",
             "judge-overhead": "Chart: the judge's bill"}


def warn(s, x, y, w, h, title, body, **kw):
    """note(), top-anchored like every other card."""
    kw.setdefault("anchor", MSO_ANCHOR.TOP)
    return note(s, x, y, w, h, title, body, **kw)


def _takeaway(s, f, y=6.2):
    text(s, 0.45, y, 12.43, 0.7, [f["takeaway"]], size=13, color=MUTED)


def s_fig_ladder(d):
    s, f = _fig(d, "ladder-amplification",
                f"Multiple of {f_base()} per task; v1.35, both platforms pooled")
    dd = f["data"]
    bar_chart(s, 0.45, 1.4, 12.43, 4.7, dd["categories"], dd["series"],
              [TOKENS, TIME, DOLLARS], number_format='#,##0"×"', size=12)
    _takeaway(s, f)


def f_base():
    return FIGS["ladder-amplification"]["data"]["base"]


def s_fig_composition(d):
    s, f = _fig(d, "cost-composition",
                "Input's share, % of a task's tokens against % of its bill; per benchmark and model")
    dd = f["data"]
    gf = bar_chart(s, 0.45, 1.4, 12.43, 4.7, dd["categories"], dd["series"], [TOKENS, DOLLARS],
                   number_format='0"%"', size=12)
    gf.chart.value_axis.maximum_scale = 100
    _takeaway(s, f)


def s_fig_model(d):
    s, f = _fig(d, "model-choice",
                "Dollars per task, split into what input and output cost; the same 5 gsm8k tasks")
    dd = f["data"]
    for parts, total in zip(zip(*dd["series"].values()), dd["total"]):
        assert abs(sum(parts) - total) < 1e-9, "the stacked parts must sum to the task's cost"
    bar_chart(s, 0.45, 1.4, 7.6, 4.0, dd["categories"], dd["series"], [DOLLARS_LT, DOLLARS],
              stacked=True, number_format='"$"0.0000', size=12)
    table(s, 8.3, 1.55, 4.58, [
        ["per task", *dd["categories"]],
        ["cost", *[f"${v:.5f}" for v in dd["total"]]],
        ["input tokens", *[f"{t[0]:,}" for t in dd["tokens"]]],
        ["output tokens", *[f"{t[1]:,}" for t in dd["tokens"]]],
        ["pass rate", *[f"{p:.2f}" for p in dd["pass_rate"]]],
    ], [1.6, 1.5, 1.5], size=12, row_h=0.42, bold_first_col=True)
    text(s, 8.3, 3.85, 4.58, 1.4, [f"**{dd['ratio']:.1f}×** the cost per task, on identical "
                                   "work: the reasoning model answers in one call; gpt-4.1 takes "
                                   "~3 tool round-trips."], size=13)
    _takeaway(s, f, y=5.75)


def s_fig_pareto(d):
    dd = FIGS["leg-pareto"]["data"]
    s, f = _fig(d, "leg-pareto", f"Dollars per leg, {dd['platform']} v1.35: ${dd['total']:.2f} "
                                 f"for all twelve, the top two {dd['top2_pct']:.0f}% of it")
    gf = bar_chart(s, 0.45, 1.4, 12.43, 4.75, dd["categories"], dd["series"], [DOLLARS],
                   number_format='[>=0.01]"$"#,##0.00;"under $0.01"', size=11, gap_width=40)
    tl = gf.chart.value_axis.tick_labels      # the bar labels' format must not reach the axis
    tl.number_format, tl.number_format_is_linked = '"$"#,##0', False
    _takeaway(s, f)


def s_fig_per_pass(d):
    s, f = _fig(d, "cost-per-pass", "Dollars per task attempted and per task passed, pooled; "
                                    "a table, because the three span three orders of magnitude")
    rows = [["benchmark", "per task attempted", "pass rate", "per task PASSED"]]
    for r in f["data"]["rows"]:
        rows.append([r["bench"], f"${r['per_task']:.5f}", f"{r['pass_rate']:.2f}",
                     f"${r['per_pass']:.5f}" if r["per_pass"] else "no finite cost: it passed "
                                                                   "nothing"])
    table(s, 0.45, 1.6, 12.43, rows, [2.2, 3.0, 2.0, 5.2], size=15, row_h=0.62,
          bold_first_col=True)
    card(s, 0.45, 4.4, 12.43, 1.2, "Never quote tokens per task as efficiency",
         ["A change that halves your token use and halves your pass rate has gained you nothing. "
          "The unbounded row is the honest report of a benchmark that runs and then fails."],
         size=13)
    _takeaway(s, f, y=5.85)


def s_fig_judge(d):
    dd = FIGS["judge-overhead"]["data"]
    s, f = _fig(d, "judge-overhead", f"Dollars per plugin leg, the agent's own calls and the "
                                     f"judge's floor (${dd['per_call']:.5f} a call)")
    bar_chart(s, 0.45, 1.4, 12.43, 4.7, dd["categories"], dd["series"], [AGENT, JUDGE],
              stacked=True, number_format='"$"0.000', size=12)
    _takeaway(s, f)


# =============================================================================== 6. results
def s_matrix(d):
    s = d.slide("The canonical 12-run matrix: one fixed set of request bodies",
                "The same 12 legs on every platform, every version",
                notes="Driven by reference/run-12.py: about an hour of run time per platform, "
                      "plus the deploys and ~40 min of gateway-cache gaps (BM_CACHE_GAP=900).")
    table(s, 0.45, 1.45, 7.6, [
        ["#", "benchmark", "tasks", "parallel", "what it varies"],
        ["1", "gsm8k", "1", "1", "baseline: the smoke test"],
        ["2", "gsm8k", "10", "1", "volume, still serial"],
        ["3", "gsm8k", "50", "4", "volume + concurrency"],
        ["4", "gsm8k", "5", "4", "model swap → Azure/gpt-4.1"],
        ["5", "gsm8k", "5", "4", "preset: auth-only"],
        ["6", "gsm8k", "5", "4", "preset: ibac-only"],
        ["7", "gsm8k", "5", "4", "preset: full (enforce)"],
        ["8", "gsm8k", "5", "4", "full + `ibac:observe`"],
        ["9", "tau2", "10", "1", "multi-turn + user simulator"],
        ["10", "tau2", "20", "4", "multi-turn under concurrency"],
        ["11", "appworld", "5", "1", "long-horizon, gemini-2.5-pro"],
        ["12", "appworld", "20", "4", "long-horizon under concurrency"],
    ], [0.6, 1.5, 0.9, 1.1, 3.5], size=12, row_h=0.4, right_from=99)
    card(s, 8.3, 1.45, 4.58, 1.75, "Why a fixed matrix", [
        "task selection is deterministic: the same leg runs the same tasks anywhere",
        "so a difference is attributable to the platform, not the workload",
    ], size=13, bullets=True)
    card(s, 8.3, 3.4, 4.58, 1.75, "Every leg deploys fresh", [
        "only a newly created pod re-pulls `:latest`",
        "the reports in `docs/results/` derive every number from the mirrored artifacts",
    ], size=13, bullets=True)


def s_bands(d):
    s = d.slide("Each band of legs changes one thing, and established one thing",
                "So a difference between two legs has one candidate explanation")
    table(s, 0.45, 1.45, 12.43, [
        ["legs", "what they vary", "what running them established"],
        ["#1–#3", "gsm8k 1 → 10 → 50 tasks, p=1 → 4", "the pipeline is stable and deterministic: "
         "7 of 12 legs have byte-identical input tokens across two unlike clusters"],
        ["#4", "gsm8k on Azure/gpt-4.1, identical tasks", "the one clean model comparison: 4.6× "
         "the cost of gpt-5-mini for no pass-rate gain"],
        ["#5–#8", "AuthBridge presets, the same 5 tasks", "the cost lands in the IBAC judge: "
         "+1.36 s/task on OpenShift, +1.48 s on KinD"],
        ["#9–#10", "tau2 10 → 20 tasks, p=1 → 4", "multi-turn works end to end; pass rates carry "
         "information (0.85–1.00)"],
        ["#11–#12", "appworld 5 → 20 tasks, p=1 → 4", "the limits are upstream: 0.00, the only "
         "timeout, ~77% of the bill in two legs"],
    ], [1.2, 4.3, 6.9], size=12, row_h=0.56, right_from=99, bold_first_col=True)
    card(s, 0.45, 5.05, 6.1, 1.3, "What the matrix as a whole is worth",
         ["That the same 12 request bodies measure comparably on two unlike clusters: 0 of 281 "
          "rows lost their usage, nothing lost to infrastructure, 8 of 12 pass rates identical."],
         size=12)
    warn(s, 6.78, 5.05, 6.1, 1.3, "Read the per-cause table, then the rate",
         ["A socket loss and a wrong answer share a denominator. OCP/KinD: timeout 0/1, agent "
          "defect 4/4, wrong answer 1/1. Latency travels only approximately (up to 1.7×)."],
         size=12)


def s_measured(d):
    # docs/results/v1.35-2026-10-04/12run-ocp.md
    s = d.slide("What the 12 runs measured on OpenShift",
                "v1.35 on OpenShift (ykt3): 141 tasks, all 12 legs succeeded")
    table(s, 0.45, 1.45, 6.3, [
        ["#", "bench", "pass", "err", "wall", "input tokens"],
        ["1", "gsm8k", "1.00", "0/1", "4 s", "320"],
        ["2", "gsm8k", "1.00", "0/10", "29 s", "3,166"],
        ["3", "gsm8k", "1.00", "0/50", "34 s", "15,684"],
        ["4", "gsm8k", "0.80", "0/5", "9 s", "3,908"],
        ["5", "gsm8k", "1.00", "0/5", "6 s", "1,564"],
        ["6", "gsm8k", "1.00", "0/5", "6 s", "1,564"],
        ["7", "gsm8k", "0.80", "1/5", "9 s", "1,564"],
        ["8", "gsm8k", "1.00", "0/5", "7 s", "1,564"],
        ["9", "tau2", "0.90", "0/10", "511 s", "936,612"],
        ["10", "tau2", "0.90", "0/20", "282 s", "1,695,959"],
        ["11", "appworld", "0.00", "1/5", "1,284 s", "1,420,531"],
        ["12", "appworld", "0.00", "3/20", "1,402 s", "5,949,228"],
    ], [0.5, 1.3, 0.9, 0.9, 1.1, 1.6], size=12, row_h=0.4, right_from=2)
    for k, (t, body) in enumerate([
        ("gsm8k nearly saturates", "6 of 8 legs at 1.00; #4 and #7 lose one task each, both the "
                                   "model's own answers."),
        ("tau2 is the leg that moves", "0.90 and 0.90 here, 1.00 and 0.85 on KinD: at n=10 one "
                                       "task is worth 0.10."),
        ("appworld 0.00 is the honest result", "Runs complete and tokens record; all 4 errors are "
                                               "the agent's own defect."),
        ("0 tasks lost to infrastructure", "v1.35 caps an agent LLM call at 120 s, so a stalled "
                                           "call is cut and retried."),
        ("10.0 M input, 728 K output tokens", "over 3,583 s of run time; appworld is 73% of the "
                                              "input on 25 of 141 rows."),
    ]):
        card(s, 7.0, 1.45 + k * 1.08, 5.88, 0.98, t, [body], size=12, title_size=13)


def s_xplat(d):
    # docs/results/v1.35-2026-10-04/12run-comparison.md
    s = d.slide("OpenShift and KinD, like for like: seven legs match to the byte",
                "The same 12 request bodies, Service version and instance config")
    table(s, 0.45, 1.45, 7.4, [
        ["#", "bench", "pass OCP", "pass KinD", "input OCP", "input KinD"],
        ["1", "gsm8k", "1.00", "1.00", "320", "320"],
        ["2", "gsm8k", "1.00", "1.00", "3,166", "3,166"],
        ["3", "gsm8k", "1.00", "1.00", "15,684", "15,684"],
        ["4", "gsm8k", "0.80", "0.80", "3,908", "4,177"],
        ["5", "gsm8k", "1.00", "1.00", "1,564", "1,564"],
        ["6", "gsm8k", "1.00", "0.80", "1,564", "1,564"],
        ["7", "gsm8k", "0.80", "1.00", "1,564", "1,564"],
        ["8", "gsm8k", "1.00", "1.00", "1,564", "1,564"],
        ["9", "tau2", "0.90", "1.00", "936,612", "916,576"],
        ["10", "tau2", "0.90", "0.85", "1,695,959", "1,726,957"],
        ["11", "appworld", "0.00", "0.00", "1,420,531", "1,564,276"],
        ["12", "appworld", "0.00", "0.00", "5,949,228", "5,349,900"],
    ], [0.5, 1.3, 1.1, 1.1, 1.7, 1.7], size=12, row_h=0.4, right_from=2)
    for k, (t, body) in enumerate([
        ("8 of 12 pass rates identical", "Each delta is one task; wrong answers 1 to 1, agent "
                                         "defects 4 to 4."),
        ("7 legs byte-identical on input", "320, 3,166, 15,684 and 1,564 ×4: identical work, the "
                                           "strongest like-for-like proof."),
        ("#6 and #7 differ in pass, not work", "Same prompts, different answers: tokens prove the "
                                               "work matched, not that it was right."),
        ("Wall time within 6%", "3,583 s against 3,791 s; the long legs stay within 15%."),
        ("0 lost rows, 0 probe failures", "141 rows against 140: a KinD appworld task timed out "
                                          "before its row was written."),
    ]):
        card(s, 8.1, 1.45 + k * 1.08, 4.78, 0.98, t, [body], size=12, title_size=13)


def s_plugin_cost(d):
    # docs/results/v1.35-2026-10-04/plugin-study-xplat.md
    s = d.slide("What AuthBridge costs: almost all of it is the judge",
                "Non-LLM seconds per task, steady state; the designed 13-leg study, n=50, v1.35")
    table(s, 0.45, 1.45, 7.0, [
        ["condition", "OpenShift", "KinD", "OCP step", "KinD step"],
        ["baseline (AuthBridge off)", "0.168", "0.097", "—", "—"],
        ["auth-only", "0.214", "0.130", "+0.05", "+0.03"],
        ["ibac-only", "1.569", "1.610", "**+1.36**", "**+1.48**"],
        ["full (auth + ibac)", "1.584", "1.602", "+0.01", "−0.01"],
        ["full + `ibac:observe`", "1.583", "1.607", "−0.00", "+0.00"],
    ], [2.6, 1.1, 1.1, 1.1, 1.1], size=12, row_h=0.46)
    table(s, 0.45, 4.45, 7.0, [
        ["does the finding travel?", "OpenShift", "KinD", "travels?"],
        ["which layer costs anything", "the judge", "the judge", "yes"],
        ["serial tool calls judged (p=1)", "10 / 10", "10 / 10", "yes"],
        ["judged / tool-call ratio, n=50", "0.94–1.00", "0.96–1.02", "yes"],
        ["tau2 measured / projected", "1.66×", "2.01×", "partly"],
    ], [2.9, 1.4, 1.4, 1.3], size=12, row_h=0.46)
    card(s, 7.7, 1.45, 5.18, 1.55, "The judge is the cost, on both clusters",
         ["+1.36 s/task on OpenShift, +1.48 s on KinD. The sidecar's presence costs 0.03–0.05 s, "
          "every other layer is noise, and with the judge on the two land within 3%."], size=12)
    warn(s, 7.7, 3.2, 5.18, 1.3, "Still name the cluster", [
        "The between-deploy noise floor is 0.02 s on OpenShift and 0.12 s on KinD, and ~0.26 s "
        "of each judged call on KinD is the node's DNS being retried."], size=12)


def s_plugin_trust(d):
    s = d.slide("Why the plugin result can be trusted",
                "The checks behind the numbers on the previous slide")
    for k, (t, body) in enumerate([
        ("Not a timeout: we checked",
         "A timeout piles values on a round number. Judged tasks run 0.21–9.63 s (OpenShift) and "
         "0.12–7.75 s (KinD) around a ~1.6 s median, no 0.1 s bin above 12%: variable latency, "
         "inherited from the judge's own LLM call."),
        ("One flag decides whether it measures anything",
         "Every gsm8k leg sends the same 50 prompts, and the gateway replays completions for ~10 "
         "min. `BM_CACHE_GAP=900` spaces the legs; the nesting check (no sidecar leg can beat a "
         "sidecar-free one) clears by 1.3× on both."),
        ("The deploy is the unit of replication",
         "A per-task interval measures variance within one deploy. The honest floor is between "
         "two deploys, and only the judge's step clears it: add deploys to rank presets, not "
         "tasks."),
        ("Two deploys already resolve a 1 s effect",
         "At σ ≈ 0.02 s (OpenShift) and 0.13 s (KinD), so the small steps are genuinely small "
         "rather than unresolved."),
    ]):
        card(s, 0.45 + (k % 2) * 6.33, 1.45 + (k // 2) * 2.2, 6.1, 2.0, t, [body], size=14,
             title_size=15)


SECTIONS = [
    ("1", "The design", "what it is, its decisions, auth, its boundary",
     [s_overview, s_motif, s_auth, s_boundary]),
    ("2", "Architecture & deployment", "the system, the workload, interception, installing",
     [s_architecture, s_workload, s_interception, s_bypass, s_install]),
    ("3", "Running a benchmark", "the REST lifecycle, the images, one task in time",
     [s_lifecycle, s_bakes, s_one_task, s_who_decides]),
    ("4", "The three benchmarks", "what each measures, the traps, what a leg costs",
     [s_ladder, s_stresses, s_compared, s_traps, s_picking, s_leg_cost]),
    ("5", "Cost", "the model, the rate card, six efficiency charts",
     [s_cost_model, s_rate_card, s_dollars_wrong, s_fig_ladder, s_fig_composition, s_fig_model,
      s_fig_pareto, s_fig_per_pass, s_fig_judge]),
    ("6", "Results (v1.35)", "the 12-run matrix, OpenShift vs KinD, plugins",
     [s_matrix, s_bands, s_measured, s_xplat, s_plugin_cost, s_plugin_trust]),
]

# Short labels for the outline slide and the PDF bookmarks, keyed by a title's start: a full
# message title wraps and overflows its outline card.
OUTLINE = {
    'What AutoBench is, and the seven decisions th': 'What it is, seven decisions',
    'One client, one Service, N workload deploymen': 'The design motif',
    "The caller's token is never forwarded upstrea": 'The two-token auth model',
    'What the Service can and cannot enact': 'What the Service can enact',
    'The architecture: one Service, a per-cluster ': 'The architecture',
    'Inside the workload: the fixed parts and the ': 'Inside the workload',
    'The sidecar sees traffic only because of two ': 'How interception is wired',
    'A ready sidecar proves nothing: the double by': 'The double bypass',
    'Installing and uninstalling: the scripts cove': 'Installing & uninstalling',
    'The catalog, and the REST calls that drive a ': 'Catalog & run lifecycle',
    'The image carries the benchmark; `tool_env` a': 'What each image bakes in',
    'One task, end to end: everything the agent do': 'One task, end to end',
    "Nobody scripts a task's trajectory: the model": 'Who decides inside a task',
    'Three rungs of a ladder, each ~an order of ma': 'The difficulty ladder',
    'What each benchmark stresses, and what a resu': 'What each stresses',
    'They differ in the shape of a task, not just ': 'The three compared',
    'Five things that mislead in reading the numbe': 'Five traps',
    'Pick a benchmark by what it is for': 'Picking a benchmark',
    'What each leg costs: the same work, close to ': 'What each leg costs',
    'Money amplifies the difficulty ladder rather ': 'The cost model',
    'Output is priced 4–8× input at every provider': 'The rate card',
    'Which way the dollars are wrong: one bias eac': 'Which way the dollars are wrong',
    'The canonical 12-run matrix: one fixed set of': 'The 12-run matrix',
    'Each band of legs changes one thing, and esta': 'What each band established',
    'What the 12 runs measured on OpenShift': 'What the 12 runs measured',
    'OpenShift and KinD, like for like: seven legs': 'OpenShift vs KinD',
    'What AuthBridge costs: almost all of it is th': 'What AuthBridge costs',
    'Why the plugin result can be trusted': 'Why the plugin result holds',
}


class AutoBenchDeck(Deck):
    """Looks a slide's short outline label up by its title, so the slide functions stay plain."""

    def slide(self, title, *a, **kw):
        kw.setdefault("outline", next((v for k, v in OUTLINE.items() if title.startswith(k)),
                                      None))
        return super().slide(title, *a, **kw)


REFUSE = [
    (r"hcp\.res\.ibm\.com|apps\.ykt\d", "a live cluster host: name the platform, or use "
                                        "example.com keeping the route shape"),
    (r"ete-litellm|litemaas|rh-aiservices", "the internal gateway host: say 'the LLM gateway'"),
    (r"amazonaws\.com", "an S3 endpoint: cite the object key, not the URL"),
    (r"\b(?:sk|AKIA|ghp)[-_A-Za-z0-9]{12,}", "a credential-shaped string"),
]


def title(d):
    d.title_slide("Architecture & design overview",
                  f"v1.35 · OpenShift and KinD · {date.today().isoformat()}")


if __name__ == "__main__":
    d = AutoBenchDeck("AutoBench", footer="AutoBench · architecture & design", out=OUT, refuse=REFUSE)
    d.build(SECTIONS, title)
