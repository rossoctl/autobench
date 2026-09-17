"""Generate docs/AutoBench.pptx — an overview + architecture deck.

Run with the project env plus python-pptx (no need to add it as a project dep):

    uv run --with python-pptx python docs/generate_pptx.py

Produces a 17-slide 16:9 deck: title, agenda, overview + key design decisions, the
design motif, three
architecture diagrams (the whole system, inside the workload, then how the sidecar is wired into
the path at all), the two-token auth
model, the benchmark catalog + run lifecycle, three benchmark slides, four results slides
(the 12-run matrix, what it measured, OpenShift vs KinD, AuthBridge overhead), and the
enact/report boundaries.

Content is sourced from docs/SERVICE_DESIGN_DECISIONS.md, docs/BENCHMARKS_PRIMER.md,
docs/PLUGIN_OVERHEAD.md and the generated docs/results/v1.28-2026-09-15/ documents. Every
measured figure comes from our own runs -- the benchmark and 12-run slides from the v1.28
matrices' mirrored artifacts, the AuthBridge slide from the designed 13-leg plugin study on the
same image -- not from the benchmarks' published papers.

Figures are hardcoded literals here rather than read from artifacts at build time, because the
deck has to render on a machine that has never seen a run. That makes them go stale silently:
when a new matrix supersedes v1.28, grep this file for the bare numbers, not just for the
version string.

Slide titles carry their agenda number, so keep the agenda list and the title_band() prefixes
in step when adding or reordering slides. Page numbers are stamped in a final pass over
prs.slides, so they follow the real order automatically.
"""

import datetime as _dt

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

# ---- palette ---------------------------------------------------------------
INK = RGBColor(0x1F, 0x2A, 0x37)       # near-black text
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
NAVY = RGBColor(0x1B, 0x3A, 0x5C)      # title band
BLUE = RGBColor(0x2E, 0x6F, 0xB5)      # service
LTBLUE = RGBColor(0xE8, 0xF1, 0xFB)
CLIENT = RGBColor(0x5B, 0x8C, 0x5A)    # client (green)
LTGREEN = RGBColor(0xE9, 0xF3, 0xE8)
KC = RGBColor(0xB5, 0x5A, 0x2E)        # keycloak (rust)
LTORANGE = RGBColor(0xFB, 0xEE, 0xE4)
ROSSO = RGBColor(0x8E, 0x44, 0xAD)     # rossoctl (purple)
LTPURPLE = RGBColor(0xF1, 0xE8, 0xF6)
WORK = RGBColor(0x3D, 0x6E, 0x70)      # workload (teal)
LTTEAL = RGBColor(0xE4, 0xF0, 0xF0)
STORE = RGBColor(0x6B, 0x6B, 0x6B)     # mlflow/s3 (gray)
LTGRAY = RGBColor(0xEE, 0xEF, 0xF1)
BORDER = RGBColor(0xC7, 0xCE, 0xD6)
ACCENT = RGBColor(0xE8, 0x7A, 0x1E)    # arrow accent

prs = Presentation()
prs.slide_width = Emu(12192000)   # 13.333"
prs.slide_height = Emu(6858000)   # 7.5"
BLANK = prs.slide_layouts[6]

SW = prs.slide_width
SH = prs.slide_height


def _set_font(run, size, bold=False, color=INK, italic=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = "Calibri"
    run.font.color.rgb = color


def textbox(slide, x, y, w, h, lines, size=14, color=INK, align=PP_ALIGN.LEFT,
            anchor=MSO_ANCHOR.TOP, bold=False):
    """lines: str, or list of (text, size, bold, color[, bullet_level])."""
    tb = slide.shapes.add_textbox(Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    if isinstance(lines, str):
        lines = [(lines, size, bold, color)]
    for i, spec in enumerate(lines):
        text, sz, bd, col = spec[0], spec[1], spec[2], spec[3]
        lvl = spec[4] if len(spec) > 4 else 0
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.level = lvl
        p.space_after = Pt(3)
        r = p.add_run()
        r.text = text
        _set_font(r, sz, bd, col)
    return tb


def box(slide, x, y, w, h, text, fill, line=BORDER, font=13, bold=True,
        font_color=INK, shape=MSO_SHAPE.ROUNDED_RECTANGLE, sub=None, sub_color=None):
    sp = slide.shapes.add_shape(shape, Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)))
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    sp.line.color.rgb = line
    sp.line.width = Pt(1.0)
    sp.shadow.inherit = False
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_top = Pt(2)
    tf.margin_bottom = Pt(2)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    _set_font(r, font, bold, font_color)
    if sub:
        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.CENTER
        r2 = p2.add_run()
        r2.text = sub
        _set_font(r2, font - 3, False, sub_color or font_color)
    return sp


def connector(slide, x1, y1, x2, y2, color=ACCENT, width=2.0, dashed=False, arrow=True):
    cn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Emu(int(x1)), Emu(int(y1)),
                                    Emu(int(x2)), Emu(int(y2)))
    cn.line.color.rgb = color
    cn.line.width = Pt(width)
    ln = cn.line._get_or_add_ln()
    if arrow:
        tail = ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"})
        ln.append(tail)
    if dashed:
        dash = ln.makeelement(qn("a:prstDash"), {"val": "dash"})
        ln.append(dash)
    cn.shadow.inherit = False
    return cn


def title_band(slide, title, subtitle=None):
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, Emu(950000))
    band.fill.solid()
    band.fill.fore_color.rgb = NAVY
    band.line.fill.background()
    band.shadow.inherit = False
    tf = band.text_frame
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Pt(28)
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    _set_font(r, 26, True, WHITE)
    if subtitle:
        p2 = tf.add_paragraph()
        r2 = p2.add_run()
        r2.text = subtitle
        _set_font(r2, 14, False, RGBColor(0xC9, 0xD9, 0xEC))
    return band


IN = 914400  # EMU per inch


def inch(v):
    return int(v * IN)



def grid(slide, x, y, w, h, rows, col_w, head_fill=NAVY, head_color=WHITE, font=10.5,
         first_col_bold=True):
    """A compact data table. python-pptx's native table beats hand-placed textboxes here: the
    column widths stay locked, so nothing drifts out of alignment when a label grows."""
    shape = slide.shapes.add_table(len(rows), len(rows[0]), Emu(int(x)), Emu(int(y)),
                                   Emu(int(w)), Emu(int(h)))
    tbl = shape.table
    tbl.first_row = True
    tbl.horz_banding = False
    for i, cw in enumerate(col_w):
        tbl.columns[i].width = Emu(int(cw))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = tbl.cell(r, c)
            cell.margin_left = cell.margin_right = Pt(5)
            cell.margin_top = cell.margin_bottom = Pt(1)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = head_fill if r == 0 else (
                WHITE if r % 2 else RGBColor(0xF4, 0xF6, 0xF8))
            para = cell.text_frame.paragraphs[0]
            para.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
            run = para.add_run()
            run.text = str(val)
            _set_font(run, font, r == 0 or (c == 0 and first_col_bold),
                      head_color if r == 0 else INK)
    return tbl


def dot(cx, cy, n):
    """Numbered badge on the CURRENT slide -- reads the module-level `s`, so it must be called
    after `s` is rebound to the slide being drawn."""
    dia = 0.26 if len(str(n)) < 2 else 0.34  # widen for 2-digit numbers so they don't wrap
    d = s.shapes.add_shape(MSO_SHAPE.OVAL, int(cx) - inch(dia / 2), int(cy) - inch(dia / 2), inch(dia), inch(dia))
    d.fill.solid(); d.fill.fore_color.rgb = ACCENT; d.line.color.rgb = WHITE; d.line.width = Pt(1)
    d.shadow.inherit = False
    tf = d.text_frame; tf.word_wrap = False
    tf.margin_left = 0; tf.margin_right = 0; tf.margin_top = 0; tf.margin_bottom = 0
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = str(n); _set_font(r, 10.5, True, WHITE)

# ============================================================ SLIDE 1: TITLE
s = prs.slides.add_slide(BLANK)
bg = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, SW, SH)
bg.fill.solid(); bg.fill.fore_color.rgb = NAVY; bg.line.fill.background(); bg.shadow.inherit = False
accent = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, inch(4.35), SW, inch(0.10))
accent.fill.solid(); accent.fill.fore_color.rgb = ACCENT; accent.line.fill.background(); accent.shadow.inherit = False
textbox(s, inch(0.9), inch(2.3), inch(11.5), inch(1.4),
        [("AutoBench", 44, True, WHITE)], anchor=MSO_ANCHOR.MIDDLE)
textbox(s, inch(0.9), inch(3.5), inch(11.5), inch(0.8),
        [("Architecture & Design Overview", 22, False, RGBColor(0xC9, 0xD9, 0xEC))])
textbox(s, inch(0.9), inch(4.7), inch(11.5), inch(1.4),
        [("A pure-Python, HTTP-only service that deploys and evaluates agent benchmarks", 16, False, RGBColor(0xD8, 0xE3, 0xF0)),
         ("across multiple cluster-specific Rossoctl instances.", 16, False, RGBColor(0xD8, 0xE3, 0xF0))])
textbox(s, inch(0.9), inch(6.25), inch(11.5), inch(0.4),
        [(f"Last modified {_dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
          12, False, RGBColor(0x9A, 0xB4, 0xD0))])

# ================================================================ SLIDE 2: AGENDA
s = prs.slides.add_slide(BLANK)
title_band(s, "Agenda", "What this deck covers, in order")
items = [
    ("Overview & key design decisions", "what the Service is, and the seven choices that shape it"),
    ("Auto-benchmarking design motif",
     "the shape of the system, before the wiring detail"),
    ("Architecture", "client, Service, and the per-cluster instance selected by JWT iss"),
    ("Architecture with workload specific components",
     "4.1 inside the workload: sidecar, user simulator, IBAC judge · 4.2 how interception is wired"),
    ("The two-token auth model", "why the caller's token is never forwarded upstream"),
    ("Benchmark catalog & run lifecycle",
     "6.1 the three benchmarks and the REST flow that drives them · 6.2 what each image bakes in"),
    ("The three benchmarks — what they measure",
     "7.1 the ladder · 7.2 what each stresses · 7.3 the traps · 7.4 picking one · "
     "7.5 what it costs"),
    ("The canonical 12-run matrix",
     "8.1 what the 12 runs parameterize · 8.2 what they measured"),
    ("Cross-platform & plugin overhead",
     "9.1 OpenShift vs KinD, like-for-like · 9.2 what AuthBridge costs"),
    ("What the Service can & cannot enact", "the HTTP-only boundary, made explicit"),
]
# Two columns: a single column left ~54% of a 16:9 canvas empty. Split 4 + 3 and set the row pitch
# so the taller column reaches roughly the same depth as the content on the other slides.
SPLIT = 5  # 10 items, five per column
COLS = ((inch(0.75), inch(1.40), inch(4.95)), (inch(7.10), inch(7.75), inch(5.10)))
for idx, (head, sub) in enumerate(items):
    col = 0 if idx < SPLIT else 1
    row = idx if col == 0 else idx - SPLIT
    chip_x, text_x, text_w = COLS[col]
    y = inch(1.42) + row * inch(1.04)
    chip = s.shapes.add_shape(MSO_SHAPE.OVAL, chip_x, y + inch(0.06), inch(0.46), inch(0.46))
    chip.fill.solid(); chip.fill.fore_color.rgb = ACCENT
    chip.line.color.rgb = WHITE; chip.line.width = Pt(1.25); chip.shadow.inherit = False
    ctf = chip.text_frame; ctf.margin_left = ctf.margin_right = 0
    ctf.margin_top = ctf.margin_bottom = 0
    cp = ctf.paragraphs[0]; cp.alignment = PP_ALIGN.CENTER
    cr = cp.add_run(); cr.text = str(idx + 1); _set_font(cr, 14, True, WHITE)
    textbox(s, text_x, y, text_w, inch(0.32), [(head, 17, True, NAVY)])
    textbox(s, text_x, y + inch(0.33), text_w, inch(0.50),
            [(sub, 12.5, False, RGBColor(0x3A, 0x46, 0x54))])
# a hairline between the columns, so the split reads as deliberate structure
connector(s, inch(6.65), inch(1.40), inch(6.65), inch(6.05), color=BORDER, width=1.0, arrow=False)

# ================================================ SLIDE 3: OVERVIEW + DECISIONS
s = prs.slides.add_slide(BLANK)
title_band(s, "1.  Overview & Key Design Decisions")

# Left column: What it is
box(s, inch(0.45), inch(1.25), inch(5.9), inch(0.55), "What it is",
    NAVY, NAVY, font=16, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
textbox(s, inch(0.5), inch(1.95), inch(5.85), inch(4.9),
        [("Deploys a benchmark's MCP tool + A2A agent, runs the evaluation, reads results, and exports them — all over HTTP.", 14, False, INK),
         ("Objectives", 14, True, BLUE),
         ("• Automate benchmarking lifecycle operations", 13.5, False, INK, 1),
         ("• Cross-user/cluster sharing of benchmark run results", 13.5, False, INK, 1),
         ("• Easy to configure in-cluster & cross-cluster benchmark runs", 13.5, False, INK, 1),
         ("• Asynchronous parallel benchmark runs", 13.5, False, INK, 1),
         ("• Secure, scalable, auditable & resilient", 13.5, False, INK, 1),
         ("3 benchmarks", 14, True, BLUE),
         ("• gsm8k — single-turn: one prompt in, one answer out, no dialogue partner", 13, False, INK, 1),
         ("• tau2 — multi-turn: a server-side user simulator LLM plays the customer", 13, False, INK, 1),
         ("• appworld — long-horizon: many API calls across simulated apps", 13, False, INK, 1),
         ("Client contract", 14, True, BLUE),
         ("• Point at a hostname, send Authorization: Bearer <caller JWT> — same from kind to prod", 13.5, False, INK, 1)])

# Right column: Key design decisions (the "overview chart")
box(s, inch(6.6), inch(1.25), inch(6.25), inch(0.55), "Key design decisions",
    NAVY, NAVY, font=16, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
decisions = [
    ("Pure-Python, HTTPS-only to Rossoctl", "No shell-out: smaller image, no injection surface, structured errors, testable."),
    ("Two-token auth model", "Caller JWT attributes + routes only (never forwarded); Service mints its own benchmarker (ROPC) token to Rossoctl."),
    ("iss is the trust anchor", "The JWT issuer selects the per-instance config — no separate instance argument, so no confused-deputy escape."),
    ("Per-request ROPC login", "Fresh Service token every request — no expiry handling."),
    ("iss-keyed per-instance config", "One file per issuer: Rossoctl URL, benchmarker cred, MLflow/S3, optional Keycloak backchannel + workload route templates."),
    ("Enact only what the API allows", "Deploy/run/report/export — now including AuthBridge plugin presets (layer-3). Only workload Secrets remain out-of-band: prechecked and reported (424), never silently ignored."),
    ("MLflow with Service; S3 in the cloud", "MLflow is co-located with the Service (per-service traces it emits + reads), not in the workload cluster; S3 is an external cloud service — the shared cross-service sink."),
]
y = inch(1.95)
for head, body in decisions:
    b = box(s, inch(6.6), y, inch(6.25), inch(0.66),
            head, LTBLUE, BLUE, font=12.5, bold=True, font_color=INK,
            shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    b.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(8)
    p2 = b.text_frame.add_paragraph()
    p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = body
    _set_font(r2, 10.5, False, RGBColor(0x3A, 0x46, 0x54))
    y += inch(0.70)


# ============================ SLIDE 4: AUTO-BENCHMARKING DESIGN MOTIF
# The conceptual shape, ahead of the wiring detail on the next slide: who talks to whom, and where
# results land. Deliberately omits Keycloak (auth has its own slide), the workload's internals (the
# slide after next), and anything cluster-specific.
s = prs.slides.add_slide(BLANK)
title_band(s, "2.  Auto-Benchmarking Design Motif",
           "One client, one Service, N workload deployments — and every result ends up in one place")

box(s, inch(0.4), inch(1.30), inch(2.8), inch(0.85),
    "AutoBench Client", CLIENT, CLIENT, font=13.5, font_color=WHITE)

box(s, inch(0.4), inch(2.45), inch(2.8), inch(1.85),
    "AutoBench Service", BLUE, BLUE, font=13.5, font_color=WHITE)

# Both stores widened and their type sized down to carry the longer role-based labels. The "shared
# sink" subtitle is dropped: the new S3 label already says it.
box(s, inch(0.4), inch(4.75), inch(1.45), inch(0.95),
    "Shared store of benchmark run results", STORE, ACCENT, font=9.5, font_color=WHITE)
box(s, inch(1.95), inch(4.75), inch(1.45), inch(0.95), "Experiment Tracker", STORE, STORE,
    font=11, font_color=WHITE)

# the instances container — stacked shadow implies "many of these"
CX3, CY3, CW3, CH3 = inch(4.05), inch(1.2), inch(8.75), inch(4.35)
for _off, _col in ((inch(0.16), RGBColor(0xF0, 0xF1, 0xF3)), (inch(0.08), RGBColor(0xE3, 0xE5, 0xE8))):
    _sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, CX3 + _off, CY3 + _off, CW3, CH3)
    _sh.fill.solid(); _sh.fill.fore_color.rgb = _col
    _sh.line.fill.background(); _sh.shadow.inherit = False
_c3 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, CX3, CY3, CW3, CH3)
_c3.fill.solid(); _c3.fill.fore_color.rgb = RGBColor(0xFB, 0xFC, 0xFD)
_c3.line.color.rgb = NAVY; _c3.line.width = Pt(1.5)
_c3.line._get_or_add_ln().append(
    _c3.line._get_or_add_ln().makeelement(qn("a:prstDash"), {"val": "dash"}))
_c3.shadow.inherit = False
textbox(s, inch(4.25), inch(1.30), inch(8.4), inch(0.4),
        [("Workload Deployment Instances", 13, True, NAVY)])

box(s, inch(4.35), inch(2.60), inch(2.55), inch(1.35),
    "Deployment helper for harnessed benchmark workload", ROSSO, ROSSO, font=12,
    font_color=WHITE)

_wg = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(9.15), inch(2.30), inch(3.45), inch(1.60))
_wg.fill.solid(); _wg.fill.fore_color.rgb = LTTEAL
_wg.line.color.rgb = WORK; _wg.line.width = Pt(1.25); _wg.shadow.inherit = False
_wtf = _wg.text_frame; _wtf.vertical_anchor = MSO_ANCHOR.MIDDLE
_wp = _wtf.paragraphs[0]; _wp.alignment = PP_ALIGN.CENTER
_wr = _wp.add_run(); _wr.text = "Harnessed benchmark workload"; _set_font(_wr, 14, True, WORK)

box(s, inch(6.15), inch(4.72), inch(2.7), inch(0.70), "Telemetry Collector", WORK, WORK,
    font=12.5, font_color=WHITE)

connector(s, inch(1.8), inch(2.15), inch(1.8), inch(2.45)); dot(inch(2.05), inch(2.30), 1)
connector(s, inch(3.2), inch(3.15), inch(4.35), inch(3.15), color=ROSSO); dot(inch(3.78), inch(2.96), 2)
connector(s, inch(6.9), inch(3.00), inch(9.15), inch(3.00), color=ROSSO); dot(inch(8.02), inch(2.81), 3)
connector(s, inch(3.2), inch(4.10), inch(9.15), inch(3.70), color=WORK); dot(inch(7.70), inch(3.79), 4)
connector(s, inch(2.35), inch(4.30), inch(2.35), inch(4.75), color=STORE); dot(inch(2.35), inch(4.52), 5)
connector(s, inch(9.55), inch(3.90), inch(8.85), inch(4.72), color=WORK, dashed=True)
dot(inch(9.25), inch(4.33), "6a")
connector(s, inch(6.15), inch(5.07), inch(3.40), inch(5.22), color=STORE, dashed=True)
dot(inch(4.78), inch(5.145), "6b")
connector(s, inch(3.05), inch(4.75), inch(3.05), inch(4.30), color=STORE); dot(inch(3.05), inch(4.52), 7)
connector(s, inch(1.05), inch(4.30), inch(1.05), inch(4.75), color=ACCENT); dot(inch(0.83), inch(4.52), 8)

_lgl = ["1  Client → Service",
        "2  Service → deployment helper:  create the harnessed workload",
        "3  helper → harnessed workload",
        "4  Service → harnessed workload:  run and monitor benchmark execution"]
_lgr = ["5  Service → Experiment Tracker:  emit the trace",
        "6a workload → Telemetry Collector  ·  6b collector → Tracker",
        "7  Experiment Tracker → Service:  read the records back",
        "8  Service → shared store:  export the run's artifacts"]
_lb = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(0.4), inch(5.85), inch(12.5), inch(1.15))
_lb.fill.solid(); _lb.fill.fore_color.rgb = RGBColor(0xF4, 0xF6, 0xF8)
_lb.line.color.rgb = BORDER; _lb.shadow.inherit = False
for _cx, _items in ((inch(0.6), _lgl), (inch(6.7), _lgr)):
    _ltf = s.shapes.add_textbox(_cx, inch(5.95), inch(6.0), inch(1.0)).text_frame
    _ltf.word_wrap = True
    for _i, _it in enumerate(_items):
        _pa = _ltf.paragraphs[0] if _i == 0 else _ltf.add_paragraph()
        _pa.space_after = Pt(0)
        _ru = _pa.add_run(); _ru.text = _it; _set_font(_ru, 11, False, INK)

# ================================================== SLIDE 5: ARCHITECTURE
s = prs.slides.add_slide(BLANK)
title_band(s, "3.  Implementation Architecture: Kubernetes-Based Benchmark Workloads",
           "Relations between client, service, cluster-specific Keycloak / Rossoctl / workload, MLflow & S3")

# Client (off-cluster / host)
box(s, inch(0.4), inch(1.30), inch(2.8), inch(0.85),
    "AutoBench Client", CLIENT, CLIENT, font=13.5, font_color=WHITE,
    sub="off-cluster (host / CI)", sub_color=RGBColor(0xE6, 0xF0, 0xE6))

# Service (center-left) — title anchored top, bullets below (no overlap)
sv = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(0.4), inch(2.45), inch(2.8), inch(1.85))
sv.fill.solid(); sv.fill.fore_color.rgb = BLUE; sv.line.color.rgb = BLUE; sv.shadow.inherit = False
svtf = sv.text_frame; svtf.word_wrap = True; svtf.vertical_anchor = MSO_ANCHOR.TOP
svtf.margin_left = Pt(8); svtf.margin_top = Pt(7)
p = svtf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
r = p.add_run(); r.text = "AutoBench Service"; _set_font(r, 13.5, True, WHITE)
for t in ("pure-Python · async", "iss-keyed InstanceRegistry", "per-request ROPC login",
          "deploy / run / report"):
    pp = svtf.add_paragraph(); pp.alignment = PP_ALIGN.LEFT; pp.level = 1
    rr = pp.add_run(); rr.text = "• " + t; _set_font(rr, 10, False, WHITE)

# MLflow + S3 sit on the SERVICE side, NOT inside the per-cluster workload instance:
# MLflow is co-located with the Service (same cluster); S3 is an external cloud service.
# S3 on the left, MLflow on the right (nearer the collector hop that feeds it).
box(s, inch(0.4), inch(4.75), inch(1.35), inch(0.95), "S3", STORE, ACCENT,
    font=13, font_color=WHITE, sub="cloud (external)", sub_color=LTGRAY)
ml = box(s, inch(1.85), inch(4.75), inch(1.35), inch(0.95), "MLflow", STORE, STORE,
         font=13, font_color=WHITE, sub="same cluster as Service", sub_color=LTGRAY)

# Per-cluster instance container (dashed) — stacked shadow implies "many instances"
CX, CY, CW, CH = inch(4.05), inch(1.2), inch(8.75), inch(4.35)
for off, col in ((inch(0.16), RGBColor(0xF0, 0xF1, 0xF3)), (inch(0.08), RGBColor(0xE3, 0xE5, 0xE8))):
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, CX + off, CY + off, CW, CH)
    sh.fill.solid(); sh.fill.fore_color.rgb = col; sh.line.fill.background(); sh.shadow.inherit = False
cont = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, CX, CY, CW, CH)
cont.fill.solid(); cont.fill.fore_color.rgb = RGBColor(0xFB, 0xFC, 0xFD)
cont.line.color.rgb = NAVY; cont.line.width = Pt(1.5)
cont.line._get_or_add_ln().append(cont.line._get_or_add_ln().makeelement(qn("a:prstDash"), {"val": "dash"}))
cont.shadow.inherit = False
textbox(s, inch(4.2), inch(1.28), inch(8.4), inch(0.4),
        [("Per-cluster instance — selected by JWT  iss   (ykt3 · kind · …)", 12.5, True, NAVY)])

# Inside the cluster container
kc = box(s, inch(4.35), inch(1.95), inch(2.55), inch(1.0),
         "Keycloak", KC, KC, font=13, font_color=WHITE,
         sub="issuer + backchannel", sub_color=LTORANGE)
ro = box(s, inch(4.35), inch(3.2), inch(2.55), inch(1.1),
         "Rossoctl", ROSSO, ROSSO, font=13, font_color=WHITE,
         sub="backend API (server-side ops)", sub_color=LTPURPLE)

# Workload group
wg = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(9.15), inch(1.95), inch(3.45), inch(2.35))
wg.fill.solid(); wg.fill.fore_color.rgb = LTTEAL; wg.line.color.rgb = WORK; wg.line.width = Pt(1.25)
wg.shadow.inherit = False
textbox(s, inch(9.25), inch(2.02), inch(3.25), inch(0.35),
        [("Benchmark Workload", 12, True, WORK)])
box(s, inch(9.35), inch(2.42), inch(3.05), inch(0.8), "MCP tool", WORK, WORK,
    font=12.5, font_color=WHITE, sub="exgentic-mcp-<benchmark>", sub_color=LTTEAL)
box(s, inch(9.35), inch(3.35), inch(3.05), inch(0.8), "A2A agent", WORK, WORK,
    font=12.5, font_color=WHITE, sub="exgentic-a2a-tool_calling-<b>", sub_color=LTTEAL)

# OTEL collector — lives in the workload cluster; forwards the agent's own OTLP spans to MLflow
# (the agent can't auth to MLflow directly). Optional / off by default (workload_otel).
box(s, inch(6.15), inch(4.72), inch(2.7), inch(0.70), "OTEL collector", WORK, WORK,
    font=12.5, font_color=WHITE, sub="forwards agent spans → MLflow", sub_color=LTTEAL)

# ---- connectors (numbered): dot() is a module-level helper (see above) ----

# 1 Client -> Keycloak (get caller JWT)
connector(s, inch(3.2), inch(1.7), inch(4.35), inch(2.25))
dot(inch(3.72), inch(1.95), 1)
# 2 Client -> Service (bearer)
connector(s, inch(1.8), inch(2.15), inch(1.8), inch(2.45))
dot(inch(2.05), inch(2.30), 2)
# 3 Service -> Keycloak (validate + mint token)
connector(s, inch(3.2), inch(2.75), inch(4.35), inch(2.6))
dot(inch(3.78), inch(2.66), 3)
# 4 Service -> Rossoctl
connector(s, inch(3.2), inch(3.55), inch(4.35), inch(3.65))
dot(inch(3.78), inch(3.58), 4)
# 5 Rossoctl -> Workload (deploys). 6 Service -> Workload (runs sessions).
# BOTH deliberately terminate on the GROUP boundary, not on an inner box: each addresses the MCP
# tool AND the A2A agent (5 creates both; 6 drives MCP sessions + A2A calls). Earlier they ended at
# y=2.9 / y=3.7, which lined up with the two inner boxes and so read as arrows aimed at a specific
# box and falling short. Aiming both at the group's vertical centre makes the group-level intent
# unambiguous; the per-component wiring is drawn on the next slide.
connector(s, inch(6.9), inch(3.55), inch(9.15), inch(3.22), color=ROSSO)
dot(inch(7.55), inch(3.455), 5)
# Arrow 6 must NOT take the direct line from the Service: (3.2,4.1) -> the group edge passes clean
# through the Rossoctl box and strikes out its label. Route it instead through the free 0.25" lane
# between Keycloak (bottom 2.95) and Rossoctl (top 3.20), then across to the group.
connector(s, inch(3.2), inch(4.05), inch(3.2), inch(3.07), color=WORK, arrow=False)
connector(s, inch(3.2), inch(3.07), inch(6.9), inch(3.07), color=WORK, arrow=False)
connector(s, inch(6.9), inch(3.07), inch(9.15), inch(3.35), color=WORK)
dot(inch(5.05), inch(3.07), 6)
# 7 Service -> MLflow: EMIT the Agent.Session trace (establishes it first, before the workload spans)
connector(s, inch(2.15), inch(4.30), inch(2.15), inch(4.75), color=STORE)
dot(inch(2.15), inch(4.52), 7)
# 8 is a TWO-HOP path (the agent cannot authenticate to MLflow itself), so both hops are badged
# 8a / 8b in flow order rather than leaving the first hop unlabelled, and both are dashed because
# the whole path is optional (workload_otel, off by default).
connector(s, inch(9.55), inch(4.15), inch(8.70), inch(4.72), color=WORK, dashed=True)
dot(inch(9.05), inch(4.48), "8a")
connector(s, inch(6.15), inch(5.07), inch(3.2), inch(5.24), color=STORE, dashed=True)
dot(inch(4.65), inch(5.155), "8b")
# 9 MLflow -> Service: READ back the Agent.Session traces (after the workload spans have landed)
connector(s, inch(2.9), inch(4.75), inch(2.9), inch(4.30), color=STORE)
dot(inch(2.9), inch(4.52), 9)
# 10 Service -> S3 (export) — terminal, after run completion
connector(s, inch(1.1), inch(4.30), inch(1.1), inch(4.75), color=ACCENT)
dot(inch(0.88), inch(4.52), 10)

# Legend — two-column panel below the frame
legend_l = [
    "1  Client logs in to Keycloak (ROPC) → caller JWT",
    "2  Client → Service:  Authorization: Bearer <caller JWT>",
    "3  Service validates JWT (JWKS) + mints its own",
    "      benchmarker token (ROPC, via backchannel)",
    "4  Service → Rossoctl: deploy / list agents + tools",
    "5  Rossoctl creates the MCP tool + A2A agent in-cluster",
]
legend_r = [
    "6  Service drives the MCP session + the A2A call itself;",
      "      the agent only issues execute_tool inside that session",
    "7  Service emits the Agent.Session trace → MLflow (first)",
    "8a A2A agent → OTEL collector   ·   8b collector → MLflow",
      "      (optional workload spans — off by default)",
    "9  Service reads back Agent.Session traces (MLflow)",
    "10  Service exports run.json / report.* (S3)",
]
lb = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(0.4), inch(5.80), inch(12.5), inch(1.35))
lb.fill.solid(); lb.fill.fore_color.rgb = RGBColor(0xF4, 0xF6, 0xF8); lb.line.color.rgb = BORDER
lb.shadow.inherit = False
for col_x, items in ((inch(0.6), legend_l), (inch(6.7), legend_r)):
    ltf = s.shapes.add_textbox(col_x, inch(5.88), inch(6.0), inch(1.22)).text_frame
    ltf.word_wrap = True
    for i, item in enumerate(items):
        p = ltf.paragraphs[0] if i == 0 else ltf.add_paragraph()
        p.space_after = Pt(0)  # 7 lines in the right column; Pt(1) pressed line 10 into the border
        r = p.add_run(); r.text = item; _set_font(r, 10.5, False, INK)

# ============================ SLIDE 5: ARCHITECTURE — WORKLOAD SPECIFIC COMPONENTS
# Zooms into the "Benchmark Workload" group of the previous slide. The point of the slide is that
# the workload is NOT the same shape for every benchmark: two of the four components are optional,
# and how many LLMs a task involves depends on the benchmark and on the plugin preset.
s = prs.slides.add_slide(BLANK)
title_band(s, "4.1  Architecture with Workload Specific Components",
           "Inside the Benchmark Workload — what every benchmark has, and what only some of them add")


def dash(sp):
    """Dashed outline = the component is conditional, not always deployed.

    The outline must CONTRAST with the fill or the dashes are invisible — the `box()` helper is
    normally called with line == fill for a flat look, which silently defeated this on the first
    render. White reads as a cut-out dash on every fill used here.
    """
    sp.line.color.rgb = WHITE
    sp.line.width = Pt(1.75)
    ln = sp.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:prstDash"), {"val": "dash"}))
    return sp


# Shared LLM gateway across the top: the agent, the tau2 user simulator and the IBAC judge all
# call it, so drawing it once as a bar keeps three flows short and non-crossing. Which gateway is
# per-instance config (workload_llm.api_base) with no default: OCP reaches an external ete-litellm,
# KinD must reach the internal (vpc-int) one, and they have separate API-key tables.
gw = box(s, inch(0.45), inch(1.20), inch(12.4), inch(0.6),
         "LLM gateway (per instance)   ·   ete-litellm   ·   external for OCP / internal vpc-int for KinD",
         STORE, STORE,
         font=12.5, font_color=WHITE, shape=MSO_SHAPE.ROUNDED_RECTANGLE)

# Left column. The judge sits ABOVE the Service deliberately: the sidecar that calls it is in the
# lower row of the agent pod, so an upward-left arrow to the judge stays clear of the Service's own
# two arrows. Ordering these the other way forces the judge and Service flows to cross.
dash(box(s, inch(0.45), inch(2.35), inch(2.10), inch(0.75), "IBAC judge", KC, KC,
         font=11.5, font_color=WHITE, sub="an LLM call", sub_color=LTORANGE))
box(s, inch(0.45), inch(3.35), inch(2.10), inch(0.85), "AutoBench Service", BLUE, BLUE,
    font=11, font_color=WHITE)

# The workload container. Its bottom band is intentionally deep (pods stop at 4.55) because the
# Service → MCP server flow is routed through it — the Service drives the session lifecycle on the
# MCP tool directly, it does not reach it via the agent.
CX2, CY2, CW2, CH2 = inch(2.72), inch(1.95), inch(10.13), inch(3.30)
cont2 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, CX2, CY2, CW2, CH2)
cont2.fill.solid(); cont2.fill.fore_color.rgb = RGBColor(0xFB, 0xFC, 0xFD)
cont2.line.color.rgb = NAVY; cont2.line.width = Pt(1.5)
cont2.line._get_or_add_ln().append(
    cont2.line._get_or_add_ln().makeelement(qn("a:prstDash"), {"val": "dash"}))
cont2.shadow.inherit = False
# Label sits in the BAND BELOW the pods, not above them: the two vertical arrows to the LLM gateway
# leave the pods through the container's top edge, and a header there was struck through by arrow 2.
textbox(s, inch(2.90), inch(4.98), inch(9.7), inch(0.22),
        [("Benchmark Workload — one per benchmark, namespace team1", 11, True, NAVY)])

# --- A2A agent pod (left) ---
ap = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(3.00), inch(2.30), inch(4.55), inch(2.40))
ap.fill.solid(); ap.fill.fore_color.rgb = LTTEAL; ap.line.color.rgb = WORK; ap.line.width = Pt(1.25)
ap.shadow.inherit = False
textbox(s, inch(3.12), inch(2.32), inch(4.3), inch(0.24), [("A2A agent pod", 11, True, WORK)])
box(s, inch(3.12), inch(2.58), inch(4.31), inch(0.80), "agent container", WORK, WORK,
    font=12, font_color=WHITE, sub="exgentic-a2a-tool_calling-<b>  ·  the LLM loop", sub_color=LTTEAL)
dash(box(s, inch(3.12), inch(3.70), inch(4.31), inch(0.80), "AuthBridge sidecar", ROSSO, ROSSO,
         font=12, font_color=WHITE, sub="proxy  ·  only with --plugin-preset", sub_color=LTPURPLE))

# --- MCP tool pod (right) ---
mp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(8.20), inch(2.30), inch(4.50), inch(2.40))
mp.fill.solid(); mp.fill.fore_color.rgb = LTTEAL; mp.line.color.rgb = WORK; mp.line.width = Pt(1.25)
mp.shadow.inherit = False
textbox(s, inch(8.32), inch(2.32), inch(4.2), inch(0.24), [("MCP tool pod", 11, True, WORK)])
dash(box(s, inch(8.32), inch(2.58), inch(4.26), inch(0.80), "user simulator LLM", WORK, WORK,
         font=12, font_color=WHITE, sub="tau2 only  ·  plays the customer", sub_color=LTTEAL))
box(s, inch(8.32), inch(3.70), inch(4.26), inch(0.80), "MCP server", WORK, WORK,
    font=12, font_color=WHITE, sub="exgentic-mcp-<benchmark>  ·  tasks + evaluation", sub_color=LTTEAL)

# --- flows. Every arrow lands on the component it actually addresses, never on a group edge. ---
# 1 Service -> agent container (A2A send_prompt, once per task)
connector(s, inch(2.55), inch(3.42), inch(3.12), inch(3.12), color=BLUE)
dot(inch(2.84), inch(3.27), 1)
# 2 Service -> MCP server. Leaves the MIDDLE of the Service box's bottom edge (x = 0.45 + 2.10/2)
# and runs along the container's bottom band: the Service calls create_session / evaluate_session /
# delete_session on the MCP tool ITSELF -- it does not reach the MCP tool via the agent. Dropping it
# from a box EDGE rather than from the corner gap is what makes it read as leaving the Service; the
# earlier 2.64 start sat 0.09" clear of the lower-right corner and looked unattached.
connector(s, inch(1.50), inch(4.20), inch(1.50), inch(4.82), color=BLUE, arrow=False)
connector(s, inch(1.50), inch(4.82), inch(9.10), inch(4.82), color=BLUE, arrow=False)
connector(s, inch(9.10), inch(4.82), inch(9.10), inch(4.50), color=BLUE)
dot(inch(6.00), inch(4.82), 2)
# 3 agent -> LLM gateway (N real chat calls per task)
connector(s, inch(5.25), inch(2.58), inch(5.25), inch(1.80), color=ACCENT)
dot(inch(5.25), inch(2.13), 3)
# 4 tau2 only: the user simulator in the MCP pod is a second LLM
connector(s, inch(10.45), inch(2.58), inch(10.45), inch(1.80), color=ACCENT)
dot(inch(10.45), inch(2.13), 4)
# 5 with a preset, the agent's MCP traffic goes through the sidecar first
# the badge is offset beside this arrow, not on it: the gap is only 0.32" and a centred badge
# covered the arrowhead, leaving the direction of travel unreadable.
connector(s, inch(5.25), inch(3.38), inch(5.25), inch(3.70), color=ROSSO)
dot(inch(5.62), inch(3.54), 5)
# 6 sidecar -> MCP server, once the action is authorized. The inter-pod corridor is 0.65" wide so
# this badge sits ON the line without touching either pod border.
connector(s, inch(7.43), inch(4.10), inch(8.32), inch(4.10), color=ROSSO)
dot(inch(7.88), inch(4.10), 6)
# 7 sidecar -> IBAC judge, per isAction tool call
connector(s, inch(3.12), inch(3.95), inch(2.55), inch(2.95), color=KC)
dot(inch(2.95), inch(3.63), 7)
# 8 the judge is itself an LLM call -- drawn, so the gateway's left third is not left unmoored
connector(s, inch(1.50), inch(2.35), inch(1.50), inch(1.80), color=KC, dashed=True)
dot(inch(1.50), inch(2.08), 8)

# --- bottom: per-benchmark matrix (left) + flow legend (right) ---
mtx = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(0.45), inch(5.40), inch(7.35), inch(1.88))
mtx.fill.solid(); mtx.fill.fore_color.rgb = RGBColor(0xF4, 0xF6, 0xF8)
mtx.line.color.rgb = BORDER; mtx.shadow.inherit = False
textbox(s, inch(0.62), inch(5.46), inch(7.0), inch(0.28),
        [("Which components a benchmark actually gets", 11.5, True, NAVY)])
COLS = ((inch(0.62), inch(1.15)), (inch(1.80), inch(2.50)),
        (inch(4.35), inch(2.15)), (inch(6.55), inch(1.15)))
ROWS = [
    # tool-call figures are MEDIANS per task, measured over the v1.28 matrices on both platforms
    # (gsm8k n=172 → 1, tau2 n=60 → 11, appworld n=35 → 15; appworld's spread is wide).
    ("benchmark", "MCP tool image", "LLMs per task", "tool calls"),
    ("gsm8k", "exgentic-mcp-gsm8k", "1  (agent)", "~1"),
    ("tau2", "exgentic-mcp-tau2", "2  (+ user simulator)", "~11"),
    ("appworld", "exgentic-mcp-appworld", "1  (agent)", "~15"),
    ("+ any preset", "unchanged", "+1 IBAC judge call per action", "—"),
]
for ri, row in enumerate(ROWS):
    y = inch(5.80) + ri * inch(0.27)
    head = ri == 0
    for (cx, cw), cell in zip(COLS, row):
        textbox(s, cx, y, cw, inch(0.28),
                [(cell, 10, head, NAVY if head else INK)])

lg2 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(8.05), inch(5.40), inch(4.8), inch(1.88))
lg2.fill.solid(); lg2.fill.fore_color.rgb = RGBColor(0xF4, 0xF6, 0xF8)
lg2.line.color.rgb = BORDER; lg2.shadow.inherit = False
flows = [
    "1  Service → agent:  send_prompt, once per task",
    "2  Service → MCP server:  list_tasks, create_session,",
    "      evaluate_session, delete_session  (the verdict)",
    "3  agent → gateway:  N real chat calls per task",
    "4  tau2 only:  the user simulator is a 2nd LLM",
    "5  with a preset:  the agent's MCP calls go via the sidecar",
    "6  sidecar → MCP server, once authorized",
    "7  sidecar → judge, per isAction call",
    "8  the judge is itself an LLM call",
    "Dashed outline = present only in some configurations.",
    "No preset: 5 + 6 become one direct agent → MCP call.",
]
ftf = s.shapes.add_textbox(inch(8.22), inch(5.46), inch(4.5), inch(1.80)).text_frame
ftf.word_wrap = True
for i, item in enumerate(flows):
    p = ftf.paragraphs[0] if i == 0 else ftf.add_paragraph()
    p.space_after = Pt(0)  # 11 lines must fit the panel; Pt(1) overflowed past the slide edge
    r = p.add_run(); r.text = item; _set_font(r, 9.5, False, INK)

# ==================== SLIDE 5b: HOW INTERCEPTION IS WIRED (and how it silently isn't)
# The previous slide draws WHERE the sidecar sits; nothing on it says WHY traffic reaches it. That
# mechanism is two env vars and an allowlist, and every part of it is invertible by a config that
# looks correct — which is exactly how we shipped a matrix whose ibac legs never called the judge.
# Sources: docs/DEVELOPER_GUIDE.md §1 "The run-time data path", docs/SERVICE_DESIGN_DECISIONS.md
# (workload_llm.disable_proxy / no_proxy), and the measured double-bypass on KinD + ykt2.
s = prs.slides.add_slide(BLANK)
title_band(s, "4.2  How Interception Is Wired — and How It Silently Isn't",
           "The sidecar sees traffic only because of two env vars; a plausible config takes it "
           "back out of the path")

# --- band 1: the three settings that decide what the sidecar sees ---
b1 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(0.45), inch(1.22), inch(12.43), inch(1.50))
b1.fill.solid(); b1.fill.fore_color.rgb = LTPURPLE
b1.line.color.rgb = ROSSO; b1.shadow.inherit = False
textbox(s, inch(0.62), inch(1.28), inch(12.1), inch(0.26),
        [("Interception is a blanket forward proxy plus an allowlist — not a tool-aware hook. "
          "Three settings decide what the sidecar actually sees:", 11.5, True, NAVY)])
CHIPS = [
    (inch(0.62), inch(3.86), "HTTP_PROXY = HTTPS_PROXY = http://127.0.0.1:8081",
     "the OPERATOR injects this into the agent pod when AuthBridge is enabled — 127.0.0.1:8081 IS "
     "the sidecar, so by default every outbound HTTP call the agent makes is captured"),
    (inch(4.62), inch(3.86), "no_proxy = LLM gateway, OTEL collector, Keycloak",
     "instance config — the hosts that stay DIRECT. This is the only reason flow 3, the agent's own "
     "chat calls, does not go through the sidecar"),
    (inch(8.62), inch(4.08), "judge_inference: false",
     "IBAC's own default — even proxied inference is not judged. BOTH this and no_proxy would have "
     "to change for the agent's model calls to be authorized"),
]
for cx, cw, head, sub in CHIPS:
    box(s, cx, inch(1.60), cw, inch(1.02), head, WHITE, ROSSO,
        font=11, font_color=NAVY, sub=sub, sub_color=RGBColor(0x3A, 0x46, 0x54))

# --- band 2: captured vs direct. Same eight flows as slide 4.1, sorted by who sees them. ---
for px, pw, fill, line, head, items in (
    (inch(0.45), inch(6.05), LTGREEN, CLIENT, "✓  Through the sidecar", [
        "•  agent → MCP tool calls  (flows 5 → 6) — the interception point",
        "•  tools/call → isAction=true → one IBAC judge call each  (flow 7)",
        "•  inbound A2A message/stream → a2a-parser, isAction=false,",
        "    so the Service's send_prompt is logged but never judged",
    ]),
    (inch(6.83), inch(6.05), LTGRAY, STORE, "✗  Direct — the plugins never see it", [
        "•  agent → LLM gateway  (flow 3) — named in no_proxy, and",
        "    judge_inference: false would exempt it even if proxied",
        "•  Service → MCP server  (flow 2) — a different pod; the Service",
        "    does not dial through the agent's proxy",
        "•  MCP tool → gateway  (flow 4, tau2's user simulator) — different pod",
    ]),
):
    p = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, px, inch(2.84), pw, inch(1.58))
    p.fill.solid(); p.fill.fore_color.rgb = fill
    p.line.color.rgb = line; p.shadow.inherit = False
    textbox(s, px + inch(0.17), inch(2.90), pw - inch(0.34), inch(0.26),
            [(head, 12, True, line)])
    textbox(s, px + inch(0.17), inch(3.20), pw - inch(0.34), inch(1.16),
            [(it, 10.5, False, INK) for it in items])

# --- band 3: the double bypass, as measured. Four steps, each individually defensible. ---
b3 = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, inch(0.45), inch(4.54), inch(12.43), inch(1.66))
b3.fill.solid(); b3.fill.fore_color.rgb = LTORANGE
b3.line.color.rgb = KC; b3.shadow.inherit = False
textbox(s, inch(0.62), inch(4.60), inch(12.1), inch(0.26),
        [("A ready sidecar proves nothing about enforcement — the double bypass we actually shipped:",
          11.5, True, NAVY)])
STEPS = [
    (inch(0.62), inch(2.94), "1.  workload_llm.disable_proxy: true makes the SERVICE inject "
                             "HTTP_PROXY=\"\" first"),
    (inch(3.68), inch(2.94), "2.  the operator adds a var only when ABSENT → it skips HTTP_PROXY, "
                             "but still sets HTTPS_PROXY"),
    (inch(6.74), inch(2.94), "3.  MCP_URL is http:// → tool calls take the empty HTTP_PROXY and go "
                             "direct"),
    (inch(9.80), inch(2.90), "4.  no_proxy carried .svc.cluster.local → the same calls are excluded "
                             "a second time"),
]
for sx, sw, text in STEPS:
    box(s, sx, inch(4.92), sw, inch(0.82), text, WHITE, KC, font=10, bold=False, font_color=INK)
box(s, inch(0.62), inch(5.82), inch(12.08), inch(0.32),
    "Result: sidecar ready = True, pipeline ConfigMap rendered, plugins loaded — and the judge "
    "called ZERO times, on both clusters.", KC, KC, font=10.5, font_color=WHITE)

# --- band 4: the only test that settles it ---
_v = box(s, inch(0.45), inch(6.32), inch(12.43), inch(0.84),
         "Verify by COUNTING judge chat completions across a run. IBAC emits no log line of its own, "
         "so a healthy sidecar log proves nothing — and initialize / tools/list are isAction=false and "
         "legitimately never reach the judge, so a zero count only means something if real tool calls "
         "occurred.   Working config: leave disable_proxy off, keep the NAMED hosts in no_proxy, drop "
         "the WILDCARDS (.svc, .svc.cluster.local).",
         LTBLUE, BLUE, font=11, bold=False, font_color=INK)
_v.text_frame.margin_left = _v.text_frame.margin_right = Pt(14)

# ================================================ SLIDE 6: TWO-TOKEN AUTH
s = prs.slides.add_slide(BLANK)
title_band(s, "5.  The Two-Token Auth Model", "Why the caller's token is never forwarded upstream")

box(s, inch(0.5), inch(1.4), inch(5.9), inch(0.5), "Caller JWT  (inbound)", CLIENT, CLIENT,
    font=15, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
textbox(s, inch(0.6), inch(2.0), inch(5.8), inch(3.6),
        [("Presented by the client as Authorization: Bearer.", 13.5, False, INK),
         ("• Used ONLY to attribute (preferred_username) and route (iss → instance)", 13, False, INK, 1),
         ("• Signature validated via the issuer's JWKS", 13, False, INK, 1),
         ("• aud / exp deliberately lenient in the dev/ops context", 13, False, INK, 1),
         ("• NEVER forwarded to Rossoctl", 13, True, KC, 1),
         ("benchmarker is special:", 13.5, True, CLIENT),
         ("• A caller JWT with preferred_username == benchmarker authorizes config ops (GET/PUT /config)", 13, False, INK, 1)])

box(s, inch(6.9), inch(1.4), inch(5.9), inch(0.5), "Service token  (outbound)", BLUE, BLUE,
    font=15, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
textbox(s, inch(7.0), inch(2.0), inch(5.8), inch(3.6),
        [("Minted by the Service per request via ROPC (password grant) using the instance's benchmarker credential.", 13.5, False, INK),
         ("• Always fresh → no expiry handling", 13, False, INK, 1),
         ("• Presented to Rossoctl for all cluster-facing ops", 13, False, INK, 1),
         ("• The Service acts under its OWN identity, not the caller's", 13, True, BLUE, 1),
         ("Backchannel split (iss ≠ dialed host):", 13.5, True, BLUE),
         ("• When the iss host is unreachable (in-cluster kind), JWKS + token come from a configured backchannel URL; iss is matched, never dialed", 13, False, INK, 1)])

box(s, inch(1.6), inch(5.7), inch(10.1), inch(1.1),
    "Implication:  at the Rossoctl / cluster layer every action appears as the Service's identity — "
    "so per-user attribution & audit live in the Service, keyed on (iss, preferred_username).",
    LTBLUE, BLUE, font=13.5, bold=True, font_color=INK)

# ============================================ SLIDE 7: CATALOG + LIFECYCLE
s = prs.slides.add_slide(BLANK)
title_band(s, "6.1  Benchmark Catalog & Run Lifecycle")

box(s, inch(0.45), inch(1.25), inch(5.75), inch(0.5), "Catalog (static registry)", NAVY, NAVY,
    font=15, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
for i, (name, desc, col, lt) in enumerate([
    ("gsm8k", "single-turn · needs hf-secret + openai-secret", WORK, LTTEAL),
    ("tau2", "multi-turn · user-simulator LLM runs server-side in the MCP pod · raise timeout", ROSSO, LTPURPLE),
    ("appworld", "long-horizon across simulated apps · runs e2e · raise task_timeout_seconds", KC, LTORANGE),
]):
    y = inch(1.95) + i * inch(1.15)
    b = box(s, inch(0.45), y, inch(5.75), inch(1.0), name, lt, col, font=15, bold=True, font_color=col)
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(10)
    p2 = b.text_frame.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = desc; _set_font(r2, 11.5, False, INK)

box(s, inch(6.55), inch(1.25), inch(6.3), inch(0.5), "Lifecycle (REST)", NAVY, NAVY,
    font=15, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
steps = [
    ("POST /benchmarks/{n}/deploy", "create MCP tool + A2A agent → 201"),
    ("GET /benchmarks/{n}/status", "poll until tool_ready & agent_ready"),
    ("POST /benchmarks/{n}/runs", "precheck → 202 run_id  (409 not deployed · 424 secret missing)"),
    ("GET …/runs/{id}", "poll pending → running → succeeded / failed"),
    ("GET …/runs/{id}/report", "MLflow records + artifacts  (409 if MLflow unset)"),
    ("download from S3", "run.json · report.* · token_report.* · span_report.* · manifest.json"),
]
y = inch(1.95)
for i, (ep, desc) in enumerate(steps):
    b = box(s, inch(6.9), y, inch(5.95), inch(0.66), ep, LTBLUE, BLUE, font=12.5,
            bold=True, font_color=INK)
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(8)
    p2 = b.text_frame.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = desc; _set_font(r2, 10.5, False, RGBColor(0x3A, 0x46, 0x54))
    if i < len(steps) - 1:
        connector(s, inch(6.75), y + inch(0.33), inch(6.9), y + inch(0.33) + inch(0.7),
                  color=BORDER, width=1.0, arrow=False)
    y += inch(0.75)
# down arrow spine
connector(s, inch(6.72), inch(2.1), inch(6.72), inch(6.4), color=ACCENT, width=1.5)


# ================================== SLIDE 7b: WHAT EACH MCP IMAGE BAKES IN
# The division of labour between the image and `tool_env` is the thing people get wrong when they add
# a benchmark: they look for a config knob for the dataset/world, which is baked in, and they miss the
# two env vars that are not. Source of truth: benchmarks/registry.py (gsm8k :309, tau2 :349,
# appworld :413) -- keep this slide in step with it and with DEVELOPER_GUIDE.md 3.4.
s = prs.slides.add_slide(BLANK)
title_band(s, "6.2  What Each Benchmark Bakes In — and What It Needs From Us",
           "The MCP image carries the benchmark itself; only the credentials and quirk overrides "
           "come from tool_env")
grid(s, inch(0.45), inch(1.30), inch(12.4), inch(2.30), [
    ("", "MCP tool image", "what the image bakes in", "what tool_env must add"),
    ("gsm8k", "exgentic-mcp-gsm8k",
     "the HuggingFace dataset loader — 8.5K problems, fetched at pod startup",
     "HF_TOKEN (from hf-secret), plus EXGENTIC_SET_BENCHMARK_RUNNER=direct"),
    ("tau2", "exgentic-mcp-tau2",
     "the tau2-bench library + its retail domain (114 tasks), and a user-simulator LLM",
     "OPENAI_API_KEY (from openai-secret) + EXGENTIC_SET_BENCHMARK_ACTION_TIMEOUT=1000 — the "
     "simulator makes its own inference calls (flow 4)"),
    ("appworld", "exgentic-mcp-appworld",
     "the whole app-suite sandbox (exgentic install --benchmark appworld)",
     "nothing beyond BENCHMARK_NAME — upstream's .env.appworld is explicitly empty"),
], col_w=[inch(1.40), inch(2.75), inch(4.10), inch(4.15)], font=11.5)

box(s, inch(0.45), inch(3.90), inch(6.05), inch(1.20),
    "One agent image serves all three",
    LTTEAL, WORK, font=14, font_color=WORK,
    sub="exgentic-a2a-tool_calling:latest is the only key in every agents={...} map; per benchmark "
        "it only gains a -<benchmark> name suffix. So the benchmark lives in the MCP pod, and the "
        "subject under test is the same binary every time.",
    sub_color=INK)
box(s, inch(6.80), inch(3.90), inch(6.05), inch(1.20),
    "The LLM base is never baked in",
    LTBLUE, BLUE, font=14, font_color=BLUE,
    sub="OPENAI_API_BASE is injected per deploy from the instance's workload_llm.api_base, and "
        "tau2's simulator model from the run's model — a deploy with no gateway configured is "
        "rejected with 422 rather than defaulting.",
    sub_color=INK)

_ban = box(s, inch(0.45), inch(5.35), inch(12.4), inch(1.55),
    "Two traps in that last column:  appworld REJECTS the same action-timeout override tau2 needs "
    "and crashes at startup with \"Unknown benchmark override 'action_timeout'\" — the env is "
    "per-benchmark, not a shared default.  And hf-secret must EXIST for gsm8k even though the "
    "dataset is public: without it the MCP pod sits in CreateContainerConfigError and the agent "
    "crash-loops.  Missing secrets surface as a 424 on the run precheck, naming exactly what to "
    "provision.",
    LTGRAY, STORE, font=12.5, bold=True, font_color=INK)
_ban.text_frame.margin_left = _ban.text_frame.margin_right = Pt(18)


# ================================ SLIDES 8-10: THE THREE BENCHMARKS (from BENCHMARKS_PRIMER.md)
# Every figure is measured from our own v1.28 runs (both platforms pooled, 267 task rows, none with
# lost telemetry) -- not quoted from the benchmarks' published papers.
s = prs.slides.add_slide(BLANK)
title_band(s, "7.1  The Three Benchmarks — a Difficulty Ladder",
           "Not interchangeable suites: each costs ~an order of magnitude more than the last")
grid(s, inch(0.45), inch(1.30), inch(12.4), inch(4.35), [
    ("", "gsm8k", "tau2", "appworld"),
    ("What it tests", "multi-step arithmetic", "multi-turn dialogue + tools", "long-horizon app automation"),
    ("Task rows measured", "172 of 172", "60 of 60", "35 of 50 (15 timed out)"),
    ("Rows with lost telemetry", "0", "0", "0"),
    ("Pass rate", "0.97", "0.83", "0.00"),
    ("Input tokens / task", "341", "90,902", "269,953"),
    ("Output tokens / task", "180", "2,061", "25,140"),
    ("LLM calls / task", "1.1", "11.4", "29.2"),
    ("Tool calls / task", "1.1", "11.4", "14.6"),
    ("Median task latency", "4.9 s", "84 s", "264 s"),
    ("Slowest task seen", "39 s", "136 s", "592 s"),
    ("Model we use", "gpt-5-mini", "claude-sonnet-5", "gemini-2.5-pro"),
    ("Task pool", "8.5K (HuggingFace)", "114 (retail domain)", "grouped scenarios"),
], col_w=[inch(2.5), inch(3.3), inch(3.3), inch(3.3)], font=11)
_ban = box(s, inch(0.45), inch(6.05), inch(12.4), inch(0.95),
    "The scale gap is the headline: a tau2 task costs ~267x the input tokens of a gsm8k task, an "
    "appworld task ~792x. A 50-task gsm8k run is a minute; a 20-task appworld run is 30-45 minutes "
    "and millions of tokens. Budget by benchmark, not by task count.",
    LTGRAY, STORE, font=12.5, bold=True, font_color=INK)
_ban.text_frame.margin_left = _ban.text_frame.margin_right = Pt(18)

# ---- what each one is actually for ----
s = prs.slides.add_slide(BLANK)
title_band(s, "7.2  What Each Benchmark Stresses",
           "Why all three are in the matrix, and what a result from each does and does not tell you")
cards = [
    ("gsm8k", "the canary", WORK, LTTEAL, [
        "One prompt in, one answer out — no dialogue partner.",
        "~1 real LLM call + 1 tool call: the simplest agentic loop.",
        "Stresses almost nothing about the platform — which is the point.",
        "A failure here means INFRASTRUCTURE: deploy, auth, LLM reach, telemetry.",
        "Saturates near 1.0, so it cannot discriminate models. Never read it as one.",
        "Deterministic enough that task 0 costs 320 input tokens on every cluster —"
        " we use that to prove two environments are comparable.",
        "Cheap enough to run often: a 50-task leg is 25 K tokens — 0.4% of the whole"
        " 12-run matrix's bill, and less than a SINGLE appworld task (see 7.4).",
    ]),
    ("tau2", "the discriminator", ROSSO, LTPURPLE, [
        "Multi-turn: a server-side USER SIMULATOR LLM plays the customer.",
        "Two models talk to each other, plus ~11 tool calls per task.",
        "Domain is retail (114 tasks) — the library default, never recorded in artifacts.",
        "The only leg where model choice dominates: 0.1 with gpt-5-mini vs 0.9 with"
        " claude-sonnet-5 on the SAME 10 tasks.",
        "Episodes are nondeterministic: the same 10 tasks scored 0.90 on one cluster"
        " and 1.00 on the other, in the same matrix.",
        "At n=10 one task moves pass_rate by 0.10 — it cannot resolve less than that.",
        "27% of a task's wall time sits INSIDE tool calls — that is the simulator"
        " generating, server-side, and its tokens appear in no report of ours.",
    ]),
    ("appworld", "the stress test", KC, LTORANGE, [
        "Realistic chores across simulated apps: discover APIs, chain many calls.",
        "~29 LLM calls, ~15 tool calls, ~270k input, ~4.5 min per task.",
        "Evaluation is PROGRAMMATIC unit tests over final app state — no LLM judge.",
        "Pass rate 0.0 is honest, not broken: runs complete and tokens record —"
        " the agent just does not finish the job.",
        "34 of 35 measured tasks call finish: it believes it is done and the"
        " assertions disagree. No verification pass.",
        "Its job here is pipeline stress: long contexts, big traces, real timeouts.",
        "Two model calls per tool call — tool shortlisting, which only bites with an"
        " API surface this large. The first carries 68% of the input tokens.",
    ]),
]
x = inch(0.45)
for name, tag, col, lt, bullets in cards:
    hd = box(s, x, inch(1.30), inch(4.05), inch(0.62), f"{name}  —  {tag}", col, col,
             font=15, font_color=WHITE)
    body = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, inch(2.02), inch(4.05), inch(4.55))
    body.fill.solid(); body.fill.fore_color.rgb = lt
    body.line.color.rgb = col; body.line.width = Pt(1.0); body.shadow.inherit = False
    hd = box(s, x, inch(1.30), inch(3.95), inch(0.62), f"{name} \u2014 {tag}", col, col,
             font=15, font_color=WHITE)
    tf = body.text_frame; tf.word_wrap = True
    tf.margin_left = tf.margin_right = Pt(10); tf.margin_top = Pt(30)
    # A shape's text frame defaults to MIDDLE anchoring and its FIRST paragraph inherits centred
    # alignment, so bullets floated in the middle of the box with line 1 centred and the rest left.
    # Both have to be set explicitly, and on every paragraph.
    tf.vertical_anchor = MSO_ANCHOR.TOP
    for i, b in enumerate(bullets):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = PP_ALIGN.LEFT
        para.space_after = Pt(5)
        run = para.add_run(); run.text = "• " + b
        _set_font(run, 10, False, INK)
    x += inch(4.22)

# The "which one do I pick" strip used to live here, cramped under the cards and a row short. It is
# slide 7.4 now, with the sizing numbers next to it.

# ---- the traps ----
s = prs.slides.add_slide(BLANK)
title_band(s, "7.3  Reading the Numbers — Five Things That Mislead",
           "Every one of these cost us a wrong conclusion first")
traps = [
    ("pass_rate = evaluated_pass / total",
     "A task that ERRORS before evaluation counts as not passed, so a low rate can mean "
     "\u201cfailed the task\u201d or \u201cnever got judged\u201d. Check the error column too."),
    ("The llm column changed meaning between agent versions — never compare across it",
     "Agents up to exgentic 0.3.5.dev131 issued a max_tokens=1 capability probe counted as a chat "
     "span, so THEIR llm=2 on gsm8k means ONE real call. From dev145 the probe is an unbilled "
     "GET /v1/models that emits no span — verified absent across all 1,895 chat spans of these two "
     "matrices. Current runs count real calls 1:1, with no offset to subtract."),
    ("Implausibly small tokens = lost telemetry, and tokens == 0 will not catch it",
     "Use the structural test: llm <= 1 with tool >= 2 is impossible, since each tool call needs a "
     "preceding model turn. A zero-check misses the cases that matter — while the probe existed it "
     "was the span that SURVIVED the loss, carrying 8/1 on claude-sonnet-5 and 1/0 on gemini. The "
     "structural pair holds across agent versions; it is what the generators use."),
    # 15-of-22 is the v1.28 re-measurement (legs that ran more than one task). It replaces an
    # earlier 27-of-33 taken from v1.23+v1.24, where legs #1-#3 and #5-#8 shared prompts inside the
    # gateway's ~10 min completion TTL -- some of those output columns were replayed usage rather
    # than fresh generations, which understates output spread. Do not restore the older figure.
    ("Output varies MORE than input, in most runs",
     "Measured OUT CV > IN CV in 15 of the 22 legs that ran more than one task. On a one-call model "
     "gsm8k's prompt is near-constant while answer length swings (IN 0.06-0.09 vs OUT 0.52-0.86); "
     "gpt-4.1 needs tool round-trips and varies on the input side too (IN 0.39-0.44). Only "
     "long-horizon appworld is input-led, in all four of its legs. Read the CV, not the mechanism."),
    ("Task selection is deterministic",
     "A run takes the first max_tasks tasks, so the same task_id is the same task across runs "
     "and clusters, and a smaller run is a prefix of a larger one. That is what makes cross-platform "
     "comparison like-for-like — six legs matched to the byte."),
]
y = inch(1.32)
for i, (head, body_text) in enumerate(traps, 1):
    b = box(s, inch(0.45), y, inch(12.4), inch(1.00), f"{i}.  {head}", LTORANGE, KC,
            font=13, bold=True, font_color=INK)
    b.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(14); b.text_frame.margin_right = Pt(14)
    b.text_frame.margin_top = Pt(6)
    p2 = b.text_frame.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = body_text
    _set_font(r2, 11, False, RGBColor(0x3A, 0x46, 0x54))
    y += inch(1.16)


# ---- 7.4 which one to pick, and what that leg costs ------------------------------------------
# The right-hand table is the measured token total of each leg, not an estimate: summed over the
# mirrored report.ndjson rows of the v1.28 pair (docs/results/v1.28-2026-09-15/). It is the answer
# to "what will this cost me", which the difficulty ladder on 7.1 gives only per task.
s = prs.slides.add_slide(BLANK)
title_band(s, "7.4  Picking a Benchmark — and What That Leg Costs",
           "The actionable summary: what each benchmark is FOR, and the token bill it hands you")
grid(s, inch(0.45), inch(1.30), inch(6.55), inch(2.55), [
    ("If you want to …", "use"),
    ("check a cluster / deploy / auth / telemetry path works", "gsm8k, 1–10 tasks"),
    ("exercise concurrency and volume cheaply", "gsm8k, 50 tasks at p=4"),
    ("compare models meaningfully", "tau2 — it discriminates; gsm8k saturates at ~1.0"),
    ("stress long contexts, long tasks, timeouts", "appworld"),
    ("get a fast signal that nothing regressed", "gsm8k — if it fails, stop and fix infrastructure"),
], col_w=[inch(3.60), inch(2.95)], font=10.5, first_col_bold=False)

grid(s, inch(7.30), inch(1.30), inch(5.55), inch(2.55), [
    ("leg (v1.28)", "tokens OCP", "tokens KinD"),
    ("#1  gsm8k, 1 task", "470", "790"),
    ("#2  gsm8k, 10 tasks", "5.1 K", "5.1 K"),
    ("#3  gsm8k, 50 tasks p=4", "25 K", "24 K"),
    ("#9  tau2, 10 tasks", "1.01 M", "1.04 M"),
    ("#10  tau2, 20 tasks p=4", "1.74 M", "1.78 M"),
    ("#11  appworld, 5 tasks", "1.49 M", "0.93 M"),
    ("#12  appworld, 20 tasks p=4", "5.71 M", "2.20 M"),
    ("all 12 legs", "10.0 M", "6.0 M"),
], col_w=[inch(2.95), inch(1.30), inch(1.30)], font=10.5)

box(s, inch(0.45), inch(4.05), inch(6.55), inch(1.35),
    "Budget by benchmark, not by task count",
    LTTEAL, WORK, font=14, font_color=WORK,
    sub="The eight gsm8k legs together are 0.4% of the matrix's token bill (0.8% on KinD). "
        "appworld's two legs are 72% of it (52% on KinD). A 50-task gsm8k leg is cheaper than a "
        "SINGLE appworld task — 25 K tokens against 295 K.",
    sub_color=INK)
box(s, inch(7.30), inch(4.05), inch(5.55), inch(1.35),
    "Two legs of the same size are not the same bill",
    LTORANGE, KC, font=14, font_color=KC,
    sub="#12 cost 2.6x more on OpenShift than on KinD for the same 20 requested tasks: appworld "
        "turn counts are nondeterministic, and the slower cluster's tasks ran longer before the "
        "600 s timeout. Size appworld on YOUR cluster.",
    sub_color=INK)

_ban = box(s, inch(0.45), inch(5.60), inch(12.4), inch(1.30),
    "And the totals UNDERSTATE it three ways:  a task killed by the per-task timeout burns tokens "
    "but leaves no report row, so appworld's 15 timed-out tasks are missing from the numbers above."
    "  tau2's user simulator runs in the MCP pod, which is not instrumented — its inference is "
    "billed by the gateway and counted nowhere here.  And a leg that replays the gateway's "
    "completion cache re-reports stored usage for calls that were never made upstream.",
    LTGRAY, STORE, font=12.5, bold=True, font_color=INK)
_ban.text_frame.margin_left = _ban.text_frame.margin_right = Pt(18)

# ---- 7.5 the cost model: tokens, models, money ----------------------------------------------
# The break-even ratio is derived, not quoted: legs #4 and #5 ran the IDENTICAL five gsm8k tasks at
# p=4 differing only in model, so equating (in x P_in + out x P_out) between them solves for the
# output:input price ratio at which the two cost the same. No price list needed, nothing to go stale.
s = prs.slides.add_slide(BLANK)
title_band(s, "7.5  The Cost Model — Tokens, Models, Money",
           "What a task costs, what a model choice costs, and the one ratio that decides it")
grid(s, inch(0.45), inch(1.30), inch(6.15), inch(4.05), [
    ("per task, pooled", "gsm8k", "tau2", "appworld"),
    ("LLM calls", "1.10", "11.38", "29.20"),
    ("input tokens", "341", "90,902", "269,953"),
    ("output tokens", "180", "2,061", "25,140"),
    ("total tokens", "520", "92,963", "295,093"),
    ("x a gsm8k task", "1x", "179x", "567x"),
    ("input share of tokens", "66%", "98%", "92%"),
    ("median task latency", "4.9 s", "84 s", "264 s"),
    ("… of it inside model calls", "90%", "58%", "96%"),
    ("pass rate", "0.97", "0.83", "0.00"),
    ("tokens per PASSED task", "537", "112 K", "no finite value"),
], col_w=[inch(2.40), inch(1.25), inch(1.25), inch(1.25)], font=10.5)

grid(s, inch(6.90), inch(1.30), inch(5.95), inch(2.60), [
    ("same 5 gsm8k tasks, p=4", "gpt-4.1", "gpt-5-mini"),
    ("pass rate  (OCP / KinD)", "0.80 / 1.00", "1.00 / 1.00"),
    ("LLM calls per task", "2.8 – 3.0", "1.0"),
    ("input tokens per task", "775 – 837", "313"),
    ("output tokens per task", "57 – 63", "137 – 355"),
    ("total tokens per task", "832 – 900", "450 – 668"),
    ("median task latency", "10.4 – 10.8 s", "11.2 – 16.4 s"),
], col_w=[inch(2.75), inch(1.60), inch(1.60)], font=10.5)

box(s, inch(6.90), inch(4.10), inch(5.95), inch(1.25),
    "The token ranking and the money ranking disagree",
    LTPURPLE, ROSSO, font=13.5, font_color=ROSSO,
    sub="The reasoning model answers in ONE call; gpt-4.1 needs ~3 tool round-trips, so it sends "
        "2.6x the input but emits a quarter of the output. Equating the two bills solves for "
        "break-even at output:input ≈ 2.7x (the platforms bracket it, 1.8x–5.8x). Priced above "
        "that ratio gpt-4.1 is cheaper; below it, gpt-5-mini.",
    sub_color=INK)

_ban = box(s, inch(0.45), inch(5.55), inch(12.4), inch(1.35),
    "cost per task  =  (input tokens x P_in  +  output tokens x P_out) / 1 M     — we publish the "
    "token counts, you supply your own rates; our gateway does not bill us, so no dollar figure "
    "here would be ours to quote.  Two consequences of the input-share row:  for tau2 and appworld "
    "the bill IS the input side, so the cost driver is turn count and context compounding, not "
    "verbosity — and a cheaper-input model beats a terser one.  For gsm8k, output is a third of the "
    "tokens and reasoning effort moves it 2.6x between clusters.",
    LTGRAY, STORE, font=12, bold=True, font_color=INK)
_ban.text_frame.margin_left = _ban.text_frame.margin_right = Pt(18)


# ============================ SLIDES 12-15: THE 12-RUN MATRIX AND WHAT IT SHOWED
# Every figure is read from the v1.28 matrices' own mirrored artifacts
# (docs/results/v1.28-2026-09-15/12run-*.md).
s = prs.slides.add_slide(BLANK)
title_band(s, "8.1  The Canonical 12-Run Matrix",
           "One fixed set of 12 request bodies — the same on every platform, every version")
grid(s, inch(0.45), inch(1.30), inch(12.4), inch(3.95), [
    ("#", "benchmark", "tasks", "parallel", "what it varies"),
    ("1", "gsm8k", "1", "1", "baseline — the smoke test"),
    ("2", "gsm8k", "10", "1", "volume, still serial"),
    ("3", "gsm8k", "50", "4", "volume + concurrency"),
    ("4", "gsm8k", "5", "4", "model swap → Azure/gpt-4.1"),
    ("5", "gsm8k", "5", "4", "AuthBridge preset: auth-only"),
    ("6", "gsm8k", "5", "4", "AuthBridge preset: ibac-only"),
    ("7", "gsm8k", "5", "4", "AuthBridge preset: full (enforce)"),
    ("8", "gsm8k", "5", "4", "full + per-plugin override ibac:observe"),
    ("9", "tau2", "10", "1", "multi-turn + user simulator"),
    ("10", "tau2", "20", "4", "multi-turn under concurrency"),
    ("11", "appworld", "5", "1", "long-horizon, gemini-2.5-pro"),
    ("12", "appworld", "20", "4", "long-horizon under concurrency"),
], col_w=[inch(0.7), inch(1.7), inch(1.0), inch(1.2), inch(7.8)], font=10.5,
   first_col_bold=False)
_m = box(s, inch(0.45), inch(5.45), inch(12.4), inch(1.55),
    "Why a FIXED matrix: task selection is deterministic, so the same leg run anywhere executes the "
    "same tasks in the same order. That is what makes a difference attributable to the platform "
    "rather than to the workload. Every leg deploys fresh — originally because a warm agent lost "
    "telemetry (fixed in agent dev145), now because only a NEWLY CREATED pod re-pulls :latest. "
    "Driven by one command (reference/run-12.py): ~1h35m of leg wall time per platform, plus the "
    "deploys and ~33 min of gateway-cache gaps. The generated documents in docs/results/ derive "
    "every number from the mirrored artifacts.",
    LTGRAY, STORE, font=12, bold=False, font_color=INK)
_m.text_frame.margin_left = _m.text_frame.margin_right = Pt(18)

# ---- 7.2 what it measured ----
s = prs.slides.add_slide(BLANK)
title_band(s, "8.2  What the 12 Runs Measured",
           "v1.28 on OpenShift (Service on ykt3, workloads on ykt2) — 141 tasks, all 12 legs succeeded")
grid(s, inch(0.45), inch(1.30), inch(6.05), inch(4.15), [
    ("#", "bench", "pass", "err", "wall", "input tokens"),
    ("1", "gsm8k", "1.00", "0/1", "7 s", "320"),
    ("2", "gsm8k", "1.00", "0/10", "41 s", "3,166"),
    ("3", "gsm8k", "1.00", "0/50", "46 s", "15,684"),
    ("4", "gsm8k", "0.80", "0/5", "16 s", "3,874"),
    ("5", "gsm8k", "1.00", "0/5", "25 s", "1,564"),
    ("6", "gsm8k", "0.60", "2/5", "39 s", "1,258"),
    ("7", "gsm8k", "0.80", "1/5", "30 s", "1,564"),
    ("8", "gsm8k", "1.00", "0/5", "23 s", "1,564"),
    ("9", "tau2", "0.90", "0/10", "803 s", "988,110"),
    ("10", "tau2", "0.75", "0/20", "436 s", "1,703,460"),
    ("11", "appworld", "0.00", "2/5", "2433 s", "1,387,383"),
    ("12", "appworld", "0.00", "3/20", "1818 s", "5,211,584"),
], col_w=[inch(0.55), inch(1.4), inch(0.9), inch(0.85), inch(1.05), inch(1.3)], font=10.5,
   first_col_bold=False)
notes = [
    ("gsm8k does not quite saturate here: 5 of 8 legs at 1.00",
     "#4's gpt-4.1 swap and #7 lose one task of five, #6 two — five of 86. Read the CAUSE, not the "
     "rate: one of #6's was a dropped socket, not the model. The runner retries those now."),
    ("tau2 is the leg that moves", "0.90 and 0.75 here, 1.00 and 0.80 on KinD. Episodes are "
     "nondeterministic: at n=10 one task is worth 0.10, so it cannot resolve less."),
    ("appworld 0.00 is the honest result", "Runs complete, tokens record, evaluation says the goal "
     "was not met — 34 of 35 tasks self-declare finish. The per-task TIMEOUT dominates: 4 of 25 "
     "tasks left no report row, so err reads run.json."),
    ("0 rows lost attribution, 0 tasks lost to the health probe",
     "The dev145 agent's per-task GET /v1/models probe (10 s cap, no retry) killed 12 of 141 KinD "
     "tasks; dev146 fixed it. An earlier matrix's 15 damaged rows passed a tokens == 0 check as "
     "clean — hence the structural detector."),
    ("9.3M input / 683k output tokens", "over 5,719 s of leg wall time — appworld alone is 71% of "
     "the input, on 21 of 137 rows."),
]
y = inch(1.30)
for head, body_text in notes:
    b = box(s, inch(6.75), y, inch(6.10), inch(0.95), head, LTBLUE, BLUE, font=12,
            bold=True, font_color=INK)
    b.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(10); b.text_frame.margin_top = Pt(5)
    p2 = b.text_frame.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = body_text
    _set_font(r2, 10, False, RGBColor(0x3A, 0x46, 0x54))
    y += inch(1.01)


# ---- 8.1 cross-platform ----
s = prs.slides.add_slide(BLANK)
title_band(s, "9.1  OpenShift vs KinD — Like-for-Like",
           "Same 12 request bodies, same Service version, verified-identical instance config")
grid(s, inch(0.45), inch(1.30), inch(7.55), inch(4.20), [
    ("#", "bench", "pass OCP", "pass KinD", "in OCP", "in KinD", "input"),
    ("1", "gsm8k", "1.00", "1.00", "320", "320", "identical"),
    ("2", "gsm8k", "1.00", "1.00", "3,166", "3,166", "identical"),
    ("3", "gsm8k", "1.00", "1.00", "15,684", "15,684", "identical"),
    ("4", "gsm8k", "0.80", "1.00", "3,874", "4,183", ""),
    ("5", "gsm8k", "1.00", "1.00", "1,564", "1,564", "identical"),
    ("6", "gsm8k", "0.60", "0.80", "1,258", "1,564", ""),
    ("7", "gsm8k", "0.80", "1.00", "1,564", "1,564", "identical"),
    ("8", "gsm8k", "1.00", "1.00", "1,564", "1,564", "identical"),
    ("9", "tau2", "0.90", "1.00", "988,110", "1,019,232", ""),
    ("10", "tau2", "0.75", "0.80", "1,703,460", "1,743,312", ""),
    ("11", "appworld", "0.00", "0.00", "1,387,383", "840,030", ""),
    ("12", "appworld", "0.00", "0.00", "5,211,584", "2,009,348", ""),
], col_w=[inch(0.55), inch(1.25), inch(0.95), inch(1.0), inch(1.35), inch(1.35), inch(1.1)],
   font=10, first_col_bold=False)
find = [
    ("7 of 12 pass rates identical, and all 5 deltas favour KinD",
     "So read the per-CAUSE table instead of the rate: wrong answers split 2 (OCP) to 1 (KinD), "
     "per-task timeouts 4 to 11 — all appworld — and one dropped socket, OCP #6."),
    ("6 legs byte-identical on input tokens", "320 / 3,166 / 15,684 / 1,564 x3. Deterministic task "
     "selection plus the same model means identical work — the strongest available proof this is a "
     "like-for-like comparison, not merely a similar one."),
    ("#7 is in that set at 0.80 vs 1.00", "Identical input tokens with different pass rates is not a "
     "contradiction: same prompts, different answers. Tokens prove the WORK matched; they say "
     "nothing about whether it was right."),
    ("Wall time: 5,719 s OCP vs 5,975 s KinD", "Within 5% overall, but individual legs differ by up "
     "to 2.3x in either direction, and appworld — which dominates the total — inverts: #11 is faster "
     "on KinD, #12 is slower."),
    ("0 lost-attribution rows and 0 probe failures on both sides",
     "137 OCP rows against 130 KinD. The 7-row gap is appworld tasks that timed out before the "
     "harness wrote a row (4 OCP, 11 KinD) — not lost telemetry."),
]
y = inch(1.30)
for head, body_text in find:
    b = box(s, inch(8.25), y, inch(4.60), inch(0.98), head, LTTEAL, WORK, font=11.5,
            bold=True, font_color=INK)
    b.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(10); b.text_frame.margin_top = Pt(5)
    p2 = b.text_frame.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = body_text
    _set_font(r2, 9.5, False, RGBColor(0x3A, 0x46, 0x54))
    y += inch(1.04)

# ---- 8.2 plugin overhead ----
s = prs.slides.add_slide(BLANK)
title_band(s, "9.2  What AuthBridge Costs — and Why It Has No Single Number",
           "The designed 13-leg experiment (n=50, crossover replicates, gateway cache spaced), v1.28")
grid(s, inch(0.45), inch(1.30), inch(6.35), inch(2.20), [
    ("non-LLM s / task (steady)", "OpenShift", "KinD", "OCP step", "KinD step"),
    ("baseline (AuthBridge off)", "0.423", "0.099", "—", "—"),
    ("auth-only", "14.101", "0.152", "+13.68", "+0.05"),
    ("ibac-only", "14.148", "1.688", "+0.05", "+1.54"),
    ("full (auth + ibac)", "14.446", "1.824", "+0.30", "+0.14"),
    ("full + ibac:observe", "14.598", "1.826", "+0.15", "+0.00"),
], col_w=[inch(2.45), inch(1.05), inch(0.95), inch(0.95), inch(0.95)], font=10.5)
grid(s, inch(0.45), inch(3.78), inch(6.35), inch(2.55), [
    ("does the finding travel?", "OpenShift", "KinD", "travels?"),
    ("Which layer costs anything", "the sidecar", "the judge", "NO"),
    ("Serial tool calls judged (p=1)", "10 / 10", "10 / 10", "YES"),
    ("Judged / tool-call ratio, n=50", "0.90–0.98", "0.90–0.98", "YES"),
    ("tau2 measured / projected", "0.31x", "2.49x", "NO"),
    ("Between-deploy noise floor", "0.22 s", "0.155 s", "YES"),
    ("Sidecar CPU / memory", "no data", "no data", "—"),
], col_w=[inch(2.45), inch(1.40), inch(1.30), inch(1.20)], font=10.5)
pts = [
    ("The two clusters disagree about WHICH layer costs anything",
     "On OpenShift the whole expense is the sidecar's mere presence and the judge is noise; on KinD "
     "the sidecar is nearly free and the judge is all of it. Both are right about their own cluster, "
     "and the same condition on the same image measures 4\u201393x apart. Never quote an absolute "
     "per-task plugin figure without naming the cluster."),
    ("Not a timeout \u2014 we checked",
     "A timeout piles values on a round number. OpenShift's auth-only runs 8.05\u201317.93 s, SD 1.92, "
     "broad and unimodal: contention and queueing. On the quiet cluster the tail moves to full "
     "(median 1.82 s, max 14.00 s) because the judge is itself an LLM call \u2014 so the sidecar "
     "costs reproducibility, not only latency."),
    ("One flag decides whether the study measures anything",
     "Every gsm8k leg sends the SAME 50 prompts and the gateway replays completions for ~10 min: "
     "unspaced, later legs measure the cache, in run order \u2014 the shape of a plugin effect. "
     "BM_CACHE_GAP=900 spaces them. The check needs no statistics: the conditions NEST, so no "
     "sidecar leg can beat a sidecar-free one. Clear by 41.8x (OCP), 1.4x (KinD)."),
    ("The deploy is the unit of replication, not the task",
     "A per-task interval measures variance WITHIN one deploy. The honest floor is between two "
     "deploys of one condition, and it exceeds every step but the dominant layer \u2014 so presets "
     "cannot be RANKED at any task count; add deploys, not tasks. Cut the other way: at "
     "\u03c3 \u2248 0.23 s two deploys already resolve a 1 s effect, so the small steps are "
     "genuinely small rather than unresolved."),
]
y = inch(1.30)
for head, body_text in pts:
    b = box(s, inch(7.05), y, inch(5.80), inch(1.42), head, LTPURPLE, ROSSO, font=11.5,
            bold=True, font_color=INK)
    b.text_frame.vertical_anchor = MSO_ANCHOR.TOP
    b.text_frame.paragraphs[0].alignment = PP_ALIGN.LEFT
    b.text_frame.margin_left = Pt(10); b.text_frame.margin_top = Pt(5)
    p2 = b.text_frame.add_paragraph(); p2.alignment = PP_ALIGN.LEFT
    r2 = p2.add_run(); r2.text = body_text
    _set_font(r2, 9.5, False, RGBColor(0x3A, 0x46, 0x54))
    y += inch(1.46)

# ============================================ SLIDE 16: BOUNDARIES
s = prs.slides.add_slide(BLANK)
title_band(s, "10.  What the Service Can & Cannot Enact", "The HTTP-only boundary, made explicit")

box(s, inch(0.5), inch(1.35), inch(5.9), inch(0.5), "✓  Enacts over HTTP", CLIENT, CLIENT,
    font=15, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
# Every line here is sized to fit on ONE line at its font size. A wrapped bullet returns to the
# column margin rather than hanging under its text, which reads as a broken line, so long items are
# split explicitly into a bullet + an indented continuation instead of being left to wrap.
textbox(s, inch(0.6), inch(2.0), inch(5.8), inch(4.6),
        [("• Deploy MCP tool + A2A agent (CPU/mem, image, env)", 13, False, INK, 1),
         ("• Deploy-time model swap (per-experiment agent)", 13, False, INK, 1),
         ("• authbridge_enabled → inject the sidecar (layer-2)", 13, False, INK, 1),
         ("• plugin_preset / plugins / on_error → AuthBridge layer-3", 13, False, INK, 1),
         ("→ pluginPreset/plugins/onError; operator renders ConfigMap", 11.5, False, RGBColor(0x3A, 0x46, 0x54), 2),
         ("• Run benchmark sessions; collect pass/fail + latency", 13, False, INK, 1),
         ("• Emit + read MLflow traces; export to S3", 13, False, INK, 1),
         ("• Service-owned config: MLflow + S3 via PUT /config", 13, False, INK, 1),
         ("benchmarker only", 11.5, False, RGBColor(0x3A, 0x46, 0x54), 2)])

box(s, inch(6.9), inch(1.35), inch(5.9), inch(0.5), "✗  Out-of-band (reports, doesn't do)", KC, KC,
    font=15, font_color=WHITE, shape=MSO_SHAPE.RECTANGLE)
textbox(s, inch(7.0), inch(2.0), inch(5.8), inch(4.6),
        [("• Cluster Secrets (hf-secret, openai-secret)", 13, False, INK, 1),
         ("operator provisions; run precheck returns 424 naming it", 11.5, False, RGBColor(0x3A, 0x46, 0x54), 2),
         ("• AuthBridge CLUSTER config — ibac judgeEndpoint / judgeModel", 13, False, INK, 1),
         ("in the platform-config ConfigMap, not the agent API", 11.5, False, RGBColor(0x3A, 0x46, 0x54), 2),
         ("• Any cluster-level API call — Rossoctl does these server-side", 13, False, INK, 1),
         ("Principle:", 13.5, True, KC),
         ("• Never silently ignore an un-enactable request —", 13, False, INK, 1),
         ("precheck (424) or reject (422), with an actionable reason", 11.5, False, RGBColor(0x3A, 0x46, 0x54), 2)])

box(s, inch(1.6), inch(5.75), inch(10.1), inch(0.75),
    "Cluster-agnostic by construction: works on kind / vanilla k8s / OpenShift; "
    "cross-cluster runs use per-instance route templates + a reachable internal issuer.",
    LTGRAY, STORE, font=12.5, bold=True, font_color=INK)

# ---- page numbers ----------------------------------------------------------
# Stamped in a final pass rather than per slide, so the numbering follows the real
# order and cannot drift when slides are added or reordered.
#
# LOWER RIGHT, in the right-hand gutter. Measured first: content in the bottom strip
# reaches at most x=12.90" and y=7.28" on a 13.33 x 7.5" slide, so the 0.43" gutter to
# the right of x=12.90 is free on every slide. Starting at 12.95 clears the widest
# content by 0.05" even where that content runs down to 7.28.
#
# Colour follows the slide: the title slide has a full-bleed navy background and needs
# white, every other slide has a light body and needs ink. Detected rather than
# hardcoded so a future dark slide is handled automatically.
def _has_dark_body(slide):
    for sh in slide.shapes:
        if sh.width is None or sh.height is None:
            continue
        if sh.width >= SW * 0.98 and sh.height >= SH * 0.9:
            try:
                rgb = sh.fill.fore_color.rgb
            except Exception:
                continue
            if 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] < 128:
                return True
    return False


for _i, _s in enumerate(prs.slides, 1):
    _col = WHITE if _has_dark_body(_s) else RGBColor(0x6B, 0x6B, 0x6B)
    _n = textbox(_s, inch(12.90), inch(7.04), inch(0.35), inch(0.34),
                 [(str(_i), 12, False, _col)],
                 align=PP_ALIGN.RIGHT, anchor=MSO_ANCHOR.MIDDLE)
    # textbox() defaults to word_wrap=True and python-pptx adds 0.1" side margins, so a
    # 0.30" box left only ~0.10" of usable width -- enough for one digit, which put every
    # two-digit number on two lines. Zero the margins and forbid wrapping outright.
    _tf = _n.text_frame
    _tf.word_wrap = False
    _tf.margin_left = _tf.margin_right = 0
    _tf.margin_top = _tf.margin_bottom = 0

# ---- slide names, which become the PDF bookmarks ---------------------------
# LibreOffice builds the PDF outline from each slide's NAME (<p:cSld name="...">), not from
# its title text. python-pptx leaves that attribute unset, so the exported deck showed a
# useless "Slide 1 ... Slide 16" bookmark list. Name every slide after its own title and the
# bookmarks become the agenda.
#
# The title is the first non-empty text on the slide: title_band() adds the navy band before
# any body content, and slide 1 (no band) leads with its "AutoBench" wordmark. The page-number
# pass above appends its textbox last, so it can never be mistaken for a title -- but run this
# BEFORE that pass would also be safe, since we take the FIRST match, not the last.
def _slide_title(slide, fallback):
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        for para in sh.text_frame.paragraphs:
            text = "".join(r.text for r in para.runs).strip()
            if text:
                # Agenda-numbered titles read "1.  Overview" with a double space; collapse
                # runs of whitespace so the bookmark is tidy.
                return " ".join(text.split())
    return fallback


for _i, _s in enumerate(prs.slides, 1):
    _cSld = _s.element.find(qn("p:cSld"))
    _cSld.set("name", _slide_title(_s, f"Slide {_i}"))

out = "docs/AutoBench.pptx"
prs.save(out)
print("wrote", out, "with", len(prs.slides._sldIdLst), "slides")
