"""Build the Job Agent class deck as a native, editable PowerPoint (16:9)."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

INK = RGBColor(0x16, 0x20, 0x3A)
ACCENT = RGBColor(0x3B, 0x5B, 0xDB)
SOFT = RGBColor(0xEE, 0xF1, 0xFD)
MUTED = RGBColor(0x5A, 0x64, 0x78)
GOOD = RGBColor(0x14, 0x80, 0x4A)
GOODSOFT = RGBColor(0xE3, 0xF2, 0xEA)
BAD = RGBColor(0xB3, 0x25, 0x1E)
LINE = RGBColor(0xD8, 0xDD, 0xE8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TERMTXT = RGBColor(0xE8, 0xEC, 0xF7)
TERMDIM = RGBColor(0x8F, 0xA0, 0xC7)
TERMBLUE = RGBColor(0x7E, 0xA8, 0xFF)
TERMGREEN = RGBColor(0x5E, 0xD5, 0x98)

SW, SH = Inches(13.333), Inches(7.5)
F = "Arial"
FMONO = "Courier New"
import pathlib
S = str(pathlib.Path(__file__).resolve().parent / "img").replace("/img", "") + "/img"

prs = Presentation()
prs.slide_width, prs.slide_height = SW, SH
BLANK = prs.slide_layouts[6]


def slide(notes=""):
    s = prs.slides.add_slide(BLANK)
    if notes:
        s.notes_slide.notes_text_frame.text = notes
    return s


def textbox(s, x, y, w, h):
    tb = s.shapes.add_textbox(x, y, w, h)
    tb.text_frame.word_wrap = True
    return tb


def para(tf, first=False):
    return tf.paragraphs[0] if first and not tf.paragraphs[0].runs else tf.add_paragraph()


def run(p, text, size, color=INK, bold=False, font=F, italic=False):
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.color.rgb = color
    r.font.bold = bold
    r.font.name = font
    r.font.italic = italic
    return r


def eyebrow(s, text, y=Inches(0.45)):
    tb = textbox(s, Inches(0.75), y, Inches(11.8), Inches(0.4))
    p = para(tb.text_frame, True)
    run(p, text.upper(), 15, ACCENT, True, FMONO)


def title(s, parts, y=Inches(0.9), size=42):
    """parts = [(text, is_accent), ...]"""
    tb = textbox(s, Inches(0.75), y, Inches(11.9), Inches(1.5))
    p = para(tb.text_frame, True)
    for text, acc in parts:
        run(p, text, size, ACCENT if acc else INK, True)


def bullets(s, items, y, size=24, x=Inches(0.85), w=Inches(11.6), gap=10):
    """items = [(marker, marker_color, text_runs)] where text_runs=[(text,bold)]"""
    tb = textbox(s, x, y, w, SH - y - Inches(0.5))
    tf = tb.text_frame
    for i, (marker, mcolor, runs_) in enumerate(items):
        p = para(tf, i == 0)
        p.space_after = Pt(gap)
        if marker:
            run(p, marker + "  ", size, mcolor, True)
        for text, bold in runs_:
            run(p, text, size, INK, bold)
    return tb


def box(s, x, y, w, h, title_txt, sub="", style="soft", tsize=16, ssize=12):
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.adjustments[0] = 0.12
    fill, line, tcol, scol = SOFT, ACCENT, INK, MUTED
    if style == "plain":
        fill, line = WHITE, LINE
    elif style == "dark":
        fill, line, tcol, scol = INK, INK, WHITE, RGBColor(0xB9, 0xC2, 0xD8)
    elif style == "good":
        fill, line = GOODSOFT, GOOD
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    sh.line.color.rgb = line
    sh.line.width = Pt(2.25)
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.08)
    tf.margin_top = tf.margin_bottom = Inches(0.06)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run(p, title_txt, tsize, tcol, True)
    if sub:
        p2 = tf.add_paragraph()
        p2.alignment = PP_ALIGN.CENTER
        run(p2, sub, ssize, scol)
    return sh


def arrow_right(s, x, y, w=Inches(0.32), h=Inches(0.26)):
    a = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, x, y, w, h)
    a.fill.solid()
    a.fill.fore_color.rgb = ACCENT
    a.line.fill.background()
    a.shadow.inherit = False
    return a


def arrow_down(s, x, y, w=Inches(0.26), h=Inches(0.3)):
    a = s.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, x, y, w, h)
    a.fill.solid()
    a.fill.fore_color.rgb = ACCENT
    a.line.fill.background()
    a.shadow.inherit = False
    return a


def flow(s, y, items, h=Inches(1.35), x0=Inches(0.75), x1=Inches(12.58), tsize=16, ssize=12):
    """items = [(title, sub, style)] laid out in a row with arrows between."""
    n = len(items)
    aw = Inches(0.42)
    bw = Emu(int((x1 - x0 - aw * (n - 1)) / n))
    x = x0
    for i, (t, sub, st) in enumerate(items):
        box(s, x, y, bw, h, t, sub, st, tsize, ssize)
        x += bw
        if i < n - 1:
            arrow_right(s, x + Emu(int((aw - Inches(0.32)) / 2)), y + h / 2 - Inches(0.13))
            x += aw


def term(s, x, y, w, h, lines, size=13):
    """lines = [[(text, color), ...], ...] monospace on dark panel."""
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    sh.adjustments[0] = 0.06
    sh.fill.solid()
    sh.fill.fore_color.rgb = INK
    sh.line.fill.background()
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = False
    tf.margin_left = tf.margin_right = Inches(0.22)
    tf.margin_top = tf.margin_bottom = Inches(0.16)
    for i, line_runs in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        for text, color in line_runs:
            run(p, text, size, color, font=FMONO)
    return sh


def caption(s, text_runs, y):
    tb = textbox(s, Inches(0.75), y, Inches(11.9), Inches(0.6))
    p = para(tb.text_frame, True)
    p.alignment = PP_ALIGN.CENTER
    for text, bold in text_runs:
        run(p, text, 16, MUTED, bold)


def picture(s, path, y, max_h, border=True):
    from PIL import Image  # pillow ships with python-pptx? no — use pptx native sizing
    pass


def add_shot(s, path, top, max_h_in):
    pic = s.shapes.add_picture(path, 0, 0)
    ratio = pic.width / pic.height
    h = Inches(max_h_in)
    w = Emu(int(h * ratio))
    if w > Inches(11.8):
        w = Inches(11.8)
        h = Emu(int(w / ratio))
    pic.width, pic.height = w, h
    pic.left = Emu(int((SW - w) / 2))
    pic.top = top
    pic.line.color.rgb = LINE
    pic.line.width = Pt(1.5)
    pic.shadow.inherit = False
    return pic


# ============================== 1 · TITLE ==============================
s = slide("Introduce yourself. One sentence: this is an AI agent that automates the repetitive 95% of job applications, and the demo is live.")
eyebrow(s, "Final Project · AI Agents", Inches(1.5))
tb = textbox(s, Inches(0.75), Inches(2.0), Inches(11.9), Inches(1.8))
p = para(tb.text_frame, True)
run(p, "Job ", 80, INK, True)
run(p, "Agent", 80, ACCENT, True)
tb = textbox(s, Inches(0.75), Inches(3.9), Inches(10.5), Inches(1.2))
p = para(tb.text_frame, True)
run(p, "An AI agent that does the boring 95% of job hunting —\nand leaves the final ", 26, INK)
run(p, "Submit", 26, INK, True)
run(p, " click to you.", 26, INK)
tb = textbox(s, Inches(0.75), Inches(6.3), Inches(11), Inches(0.5))
p = para(tb.text_frame, True)
run(p, "David Zeff  ·  Python + Flask + Groq LLM  ·  live demo included", 17, MUTED)

# ============================== 2 · PROBLEM ==============================
s = slide("Everyone in the room has felt this. The point: none of these steps require human judgment except deciding to apply.")
eyebrow(s, "01 · The problem")
title(s, [("Applying to jobs is the same hour,\n", False), ("again and again", True)], size=40)
bullets(s, [
    ("✕", BAD, [("Search five job boards, one by one", False)]),
    ("✕", BAD, [("Re-type your details into every form", False)]),
    ("✕", BAD, [("Tailor the resume — for every single job", False)]),
    ("✕", BAD, [("Write yet another cover letter", False)]),
    ("✕", BAD, [("Answer the same 20 questions every time", False)]),
], Inches(3.1), size=26, gap=14)

# ============================== 3 · WHAT IT DOES ==============================
s = slide("The pipeline in one line. Emphasize the last box: the human only does the final click — by design, not as a limitation.")
eyebrow(s, "02 · What it does")
title(s, [("Tell it about yourself ", False), ("once", True)])
flow(s, Inches(2.5), [
    ("Profile", "entered once, stored locally", "soft"),
    ("Search", "10 job sources at once", "soft"),
    ("Rank", "scored against your profile", "soft"),
    ("Write", "tailored resume + letter, fact-checked", "soft"),
    ("You submit", "the only manual step", "dark"),
], h=Inches(1.5))
tb = textbox(s, Inches(0.75), Inches(4.6), Inches(11.5), Inches(1))
p = para(tb.text_frame, True)
run(p, "It even fills the employer's real application form — then stops and waits for you.", 24, INK)

# ============================== 4 · DEMO ==============================
s = slide("Switch to the app now: Job Agent.command must be running. Show these four things in order, then come back to the slides.")
eyebrow(s, "03 · Demo")
tb = textbox(s, Inches(0.75), Inches(1.4), Inches(11.9), Inches(1.6))
p = para(tb.text_frame, True)
run(p, "Live ", 76, INK, True)
run(p, "demo", 76, ACCENT, True)
bullets(s, [
    ("1", GOOD, [("  Profile — dropdowns, GitHub import", False)]),
    ("2", GOOD, [("  Find jobs — ranked, with reasons", False)]),
    ("3", GOOD, [("  Prepare — watch the AI work", False)]),
    ("4", GOOD, [("  Auto-fill a real application form", False)]),
], Inches(3.4), size=27, gap=12)

# ============================== 5 · SHOT FIND ==============================
s = slide("Ranked results. Point at the match score, the plain-language reasons, and Israeli company jobs (Cato, Via, Similarweb) pulled directly from career pages.")
eyebrow(s, "03 · Demo — finding jobs")
add_shot(s, f"{S}/pres-find.jpg", Inches(1.0), 5.4)
caption(s, [("Every job gets a match score and a plain-language ", False), ("why", True),
            (" — including jobs straight from Israeli company career pages.", False)], Inches(6.65))

# ============================== 6 · SHOT DETAIL ==============================
s = slide("A prepared application: tailored PDF resume, cover letter, and copy buttons for every form answer. Note the fact-checked line in the header.")
eyebrow(s, "03 · Demo — a prepared application")
add_shot(s, f"{S}/pres-detail.jpg", Inches(1.0), 5.4)
caption(s, [("Tailored resume (PDF), cover letter, and every form answer with a ", False), ("Copy", True),
            (" button — fact-checked before you see it.", False)], Inches(6.65))

# ============================== 7 · ARCHITECTURE ==============================
s = slide("Three ways in, one engine. The tool registry is the key design move: every capability is defined once and callable by code or by the LLM.")
eyebrow(s, "04 · Architecture")
title(s, [("Three interfaces, ", False), ("one engine", True)], size=36, y=Inches(0.82))
rowy = Inches(1.75)
rh = Inches(0.92)
flow_x0, flow_x1 = Inches(0.75), Inches(12.58)
w3 = Emu(int((flow_x1 - flow_x0 - Inches(0.3) * 2) / 3))
x = flow_x0
for t, sub in [("Web app", "for humans"), ("CLI", "for scripts"), ('"Find me python jobs"', "natural-language agent")]:
    box(s, x, rowy, w3, rh, t, sub, "plain", 15, 11)
    x += w3 + Inches(0.3)
arrow_down(s, SW / 2 - Inches(0.13), rowy + rh + Inches(0.06))
rowy2 = rowy + rh + Inches(0.42)
box(s, flow_x0, rowy2, flow_x1 - flow_x0, Inches(0.85),
    "Flask API + Tool Registry", "every capability defined once, callable by code or by the LLM", "dark", 16, 12)
arrow_down(s, SW / 2 - Inches(0.13), rowy2 + Inches(0.91))
rowy3 = rowy2 + Inches(1.27)
w5 = Emu(int((flow_x1 - flow_x0 - Inches(0.22) * 4) / 5))
x = flow_x0
for t, sub in [("scraper", "10 sources"), ("matching", "score + rank"), ("generate", "resume · letter · PDF"),
               ("autofill", "real forms"), ("tracker", "what you've seen")]:
    box(s, x, rowy3, w5, Inches(0.85), t, sub, "soft", 14, 10)
    x += w5 + Inches(0.22)
arrow_down(s, SW / 2 - Inches(0.13), rowy3 + Inches(0.91))
rowy4 = rowy3 + Inches(1.27)
w4 = Emu(int((flow_x1 - flow_x0 - Inches(0.25) * 3) / 4))
x = flow_x0
for t, sub in [("Job board APIs", ""), ("Company career APIs", "Greenhouse · Lever · Ashby · SmartRecruiters"),
               ("Groq LLM", "llama-3.3-70b"), ("Your Chrome", "PDFs + autofill")]:
    box(s, x, rowy4, w4, Inches(0.95), t, sub, "plain", 14, 10)
    x += w4 + Inches(0.25)

# ============================== 8 · WHERE THE AI IS ==============================
s = slide("Architecture principle: no orchestrator agent. The flow is plain code; the LLM is used only at the four points that need judgment. Each AI step can fail and the pipeline still completes.")
eyebrow(s, "04 · Architecture principle")
title(s, [("Deterministic pipeline.\n", False), ("AI only where judgment is needed.", True)], size=34)
flow(s, Inches(2.9), [
    ("Search", "plain code", "plain"),
    ("Rank", "AI JUDGE", "soft"),
    ("Write", "AI WRITER", "soft"),
    ("Review", "AI CRITIC", "soft"),
    ("Files & tracking", "plain code", "plain"),
], h=Inches(1.15))
tb = textbox(s, Inches(0.75), Inches(4.7), Inches(11.8), Inches(2))
p = para(tb.text_frame, True)
run(p, "No “orchestrator agent” deciding what to do next: the flow is code, so it never gets lost and never burns tokens on planning — and every AI step can fail ", 21, INK)
run(p, "without breaking the pipeline", 21, INK, True)
run(p, ".", 21, INK)

# ============================== 9 · TOOLS ==============================
s = slide("What a tool literally is: a JSON schema plus the Python function. Same registry serves the fixed pipeline and the LLM. Tools return summaries, not dumps.")
eyebrow(s, "05 · Agents & tools")
title(s, [("A tool = a function ", False), ("the model may call", True)], size=34)
term(s, Inches(0.75), Inches(2.2), Inches(6.4), Inches(4.4), [
    [("{ ", TERMTXT), ('"name"', TERMBLUE), (": ", TERMTXT), ('"search_jobs"', TERMGREEN), (",", TERMTXT)],
    [('  "description"', TERMBLUE), (": ", TERMTXT), ('"Search job', TERMGREEN)],
    [('     boards. Returns count', TERMGREEN)],
    [('     and sample titles."', TERMGREEN), (",", TERMTXT)],
    [('  "parameters"', TERMBLUE), (": {", TERMTXT)],
    [('    "keywords"', TERMBLUE), (": ", TERMTXT), ("array of string", TERMGREEN), (",", TERMTXT)],
    [('    "remote"', TERMBLUE), (":   ", TERMTXT), ("boolean", TERMGREEN), (",", TERMTXT)],
    [('    "limit"', TERMBLUE), (":    ", TERMTXT), ("integer", TERMGREEN), (" } }", TERMTXT)],
    [("", TERMTXT)],
    [("# + the Python function", TERMDIM)],
    [("#   that actually runs it", TERMDIM)],
], size=15)
bullets(s, [
    ("✓", GOOD, [("6 tools: search, rank, list, generate, answer, profile", False)]),
    ("✓", GOOD, [("One registry, ", False), ("two callers", True), (": the fixed pipeline — and the LLM itself", False)]),
    ("✓", GOOD, [("Tools return short summaries, not raw data — keeps the model focused", False)]),
], Inches(2.5), size=20, x=Inches(7.5), w=Inches(5.2), gap=16)

# ============================== 10 · AGENT LOOP ==============================
s = slide("The classic tool-calling loop with a real trace from this system: three tool calls, then a plain answer. This is the 'agent' in Job Agent.")
eyebrow(s, "05 · Agents & tools — the loop")
title(s, [("The agent loop, ", False), ("for real", True)], size=36)
ly = Inches(2.15)
for i, (t, sub) in enumerate([
    ("1 · User asks in plain language", ""),
    ("2 · LLM sees the tool schemas", "picks a tool + arguments"),
    ("3 · We run the real function", "result goes back as JSON"),
    ("4 · Repeat until it can answer", ""),
]):
    box(s, Inches(0.75), ly, Inches(5.3), Inches(0.95), t, sub, "soft" if i % 2 else "plain", 16, 12)
    ly += Inches(1.13)
term(s, Inches(6.5), Inches(2.15), Inches(6.1), Inches(4.5), [
    [("# actual run:", TERMDIM)],
    [("$ agent ", TERMTXT), ('"find remote python jobs,', TERMGREEN)],
    [('         show top 3, don\'t apply"', TERMGREEN)],
    [("", TERMTXT)],
    [("→ tool: ", TERMTXT), ("search_jobs", TERMBLUE), ('({"keywords":', TERMTXT)],
    [('        ["python"],"remote":true})', TERMTXT)],
    [('   {"found": 14, ...}', TERMDIM)],
    [("→ tool: ", TERMTXT), ("rank_jobs", TERMBLUE), ('({"top": 3})', TERMTXT)],
    [('   {"ranked": 14, ...}', TERMDIM)],
    [("→ tool: ", TERMTXT), ("list_ranked", TERMBLUE), ('({"top": 3})', TERMTXT)],
    [("", TERMTXT)],
    [('"Your top 3 matches are: …"', TERMGREEN)],
], size=14)

# ============================== 11 · SPECIALISTS ==============================
s = slide("Six narrow prompts instead of one giant one. Each has one job and its own guardrail. Green = the quality-control pair.")
eyebrow(s, "06 · The AI cast")
title(s, [("Six specialists, ", False), ("not one genius", True)], size=36)
cards = [
    ("Ranker", "judges fit 0–100 with strengths & gaps", "soft"),
    ("Tailor", "rewrites resume + letter for one job, in the posting's language", "soft"),
    ("Reviewer", "hunts invented facts, wrong company, wrong language", "good"),
    ("Repairer", "rewrites only what the Reviewer flagged", "good"),
    ("Answerer", "answers any form question — or says \"not in profile\"", "soft"),
    ("Company Scout", "proposes local employers, verified against real APIs", "soft"),
]
cw, ch = Inches(3.85), Inches(1.5)
for i, (t, sub, st) in enumerate(cards):
    x = Inches(0.75) + (cw + Inches(0.25)) * (i % 3)
    y = Inches(2.3) + (ch + Inches(0.3)) * (i // 3)
    box(s, x, y, cw, ch, t, sub, st, 18, 13)
tb = textbox(s, Inches(0.75), Inches(6.1), Inches(11.8), Inches(0.7))
p = para(tb.text_frame, True)
run(p, "Each has one narrow prompt, one job, and its own guardrail.", 22, INK)

# ============================== 12 · WRITER→CRITIC→FIX ==============================
s = slide("The multi-agent quality gate. Tell the story: we planted a fake PhD and the wrong company — the reviewer caught all five issues and the repair pass came back clean.")
eyebrow(s, "06 · Multi-agent quality gate")
title(s, [("Writer → Critic → Fix: ", False), ("catching AI lies", True)], size=34)
flow(s, Inches(2.3), [
    ("Tailor writes", "draft resume + letter", "soft"),
    ("Reviewer checks every claim", "profile is the only source of truth", "good"),
    ("Repairer fixes", "then it ships", "soft"),
], h=Inches(1.3))
tb = textbox(s, Inches(0.75), Inches(4.3), Inches(11.9), Inches(2.4))
tf = tb.text_frame
p = para(tf, True)
run(p, " CAUGHT IN TESTING ", 16, WHITE, True).font.highlight_color = None
p.runs[0].font.color.rgb = WHITE
# simple badge look: colored text prefix instead of shape
p = tf.paragraphs[0]
p.clear()
run(p, "CAUGHT IN TESTING:  ", 18, BAD, True)
run(p, "draft claimed a ", 20, INK)
run(p, "PhD in Physics", 20, INK, True)
run(p, ", ", 20, INK)
run(p, "“10 years of experience”", 20, INK, True)
run(p, ", and addressed the ", 20, INK)
run(p, "wrong company", 20, INK, True)
p2 = tf.add_paragraph()
p2.space_before = Pt(14)
run(p2, "AFTER REPAIR:  ", 18, GOOD, True)
run(p2, "5 of 5 issues removed — re-review came back clean", 20, INK)

# ============================== 13 · GUARDRAILS ==============================
s = slide("Safety by construction: grounded generation, a second AI pass on everything, verified discovery, and a hard rule — the software cannot click Submit.")
eyebrow(s, "07 · Guardrails")
title(s, [("Autonomous, ", False), ("not reckless", True)])
bullets(s, [
    ("✓", GOOD, [("Documents may only use facts ", False), ("from your profile", True)]),
    ("✓", GOOD, [("Every packet is fact-checked by a second AI pass", False)]),
    ("✓", GOOD, [("AI-suggested companies are ", False), ("verified against real APIs", True), (" before use", False)]),
    ("✕", BAD, [("It never clicks Submit — ", False), ("ever", True)]),
    ("✓", GOOD, [("Your data and API key stay on ", False), ("your", True), (" machine or browser", False)]),
], Inches(2.6), size=25, gap=14)

# ============================== 14 · AUTOFILL ==============================
s = slide("The form-filler agent drives the user's own Chrome via Playwright. Deterministic code fills standard fields; the LLM answers open questions; it always stops before Submit.")
eyebrow(s, "08 · The form-filler agent")
title(s, [("It fills the ", False), ("real", True), (" application form", False)], size=34)
flow(s, Inches(2.1), [
    ("Detect the form system", "Greenhouse / Lever", "plain"),
    ("Open your Chrome", "scan every field", "soft"),
    ("Fill + upload resume", "code for standard fields, AI for open questions", "soft"),
    ("STOP", "gaps highlighted — you review and submit", "dark"),
], h=Inches(1.25))
add_shot(s, f"{S}/pres-autofill.jpg", Inches(3.65), 3.6)

# ============================== 15 · SOURCES ==============================
s = slide("Coverage: 9 keyless boards plus the country layer — 109 verified company boards shipped, and AI discovery (verified, cached) for any other country.")
eyebrow(s, "09 · Where the jobs come from")
title(s, [("10 boards + your country's ", False), ("actual companies", True)], size=32)
box(s, Inches(0.75), Inches(2.25), Inches(5.7), Inches(1.9), "9 public job boards",
    "Remotive · RemoteOK · Arbeitnow · Jobicy · Himalayas · WeWorkRemotely · Hacker News · The Muse · Working Nomads", "plain", 17, 13)
box(s, Inches(0.75), Inches(4.45), Inches(5.7), Inches(1.5), "Aggregators (optional)",
    "Jooble ~69 countries incl. Israel · Adzuna 20 countries", "plain", 17, 13)
box(s, Inches(6.85), Inches(2.25), Inches(5.7), Inches(1.9), "Company career pages — automatic",
    "109 verified employers shipped for 9 countries. Israel: NICE · Via · Cato · Gong · Similarweb · Taboola · JFrog …", "soft", 17, 13)
box(s, Inches(6.85), Inches(4.45), Inches(5.7), Inches(1.5), "Unknown country?",
    "AI proposes employers → each verified live → cached. Spain test: 3 companies, 83 real openings", "good", 17, 13)

# ============================== 16 · BILINGUAL ==============================
s = slide("Language follows the posting, not the profile. Hebrew needed real bidi work — and Hebrew quote marks even broke the model's JSON until we added a repair layer.")
eyebrow(s, "10 · Bilingual by design")
title(s, [("Hebrew posting? ", False), ("Hebrew resume.", True)])
bullets(s, [
    ("✓", GOOD, [("The ", False), ("posting's language", True), (" decides the documents' language — not the profile's", False)]),
    ("✓", GOOD, [("Full right-to-left layout: headings, bullets, dates — ", False), ("ניהלתי תקציב של 250,000 ש״ח", True)]),
    ("✓", GOOD, [("Hebrew quotes broke the model's JSON (ש\"ח) — fixed with an automatic repair layer", False)]),
], Inches(2.7), size=24, gap=16)

# ============================== 17 · DEPLOYMENT ==============================
s = slide("Same codebase, one flag. Desktop has the machine-bound powers; the hosted version is stateless — each visitor's data lives in their own browser.")
eyebrow(s, "11 · Runs anywhere")
title(s, [("Desktop app ", False), ("and", True), (" a website", False)])
box(s, Inches(0.75), Inches(2.4), Inches(5.7), Inches(2.6), "Desktop (double-click)",
    "full powers: form autofill · background auto-search · auto-prepared applications · PDF files", "soft", 20, 15)
box(s, Inches(6.85), Inches(2.4), Inches(5.7), Inches(2.6), "Vercel (hosted)",
    "stateless server — your profile, key, history and applications live in your own browser; nothing stored server-side", "plain", 20, 15)
tb = textbox(s, Inches(0.75), Inches(5.4), Inches(11.8), Inches(0.9))
p = para(tb.text_frame, True)
run(p, "Same code, one flag. The app hides what each home can't do — with an explanation.", 22, INK)

# ============================== 18 · NUMBERS ==============================
s = slide("Close the technical story with scale and rigor: parallel preparation, five AI calls per application including the fact-check, and a 21-check automated E2E in both modes.")
eyebrow(s, "12 · By the numbers")
title(s, [("Small system, ", False), ("real work", True)])
stats = [
    ("10", "job sources searched in parallel"),
    ("109", "verified company boards · 9 countries"),
    ("~15s", "per tailored application (2 in parallel)"),
    ("5", "AI calls per application — incl. fact-check"),
    ("21×2", "automated end-to-end checks, desktop + hosted"),
    ("0", "frameworks: Flask + vanilla JS + Playwright"),
]
cw, ch = Inches(3.85), Inches(1.85)
for i, (n, l) in enumerate(stats):
    x = Inches(0.75) + (cw + Inches(0.25)) * (i % 3)
    y = Inches(2.3) + (ch + Inches(0.3)) * (i // 3)
    sh = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, cw, ch)
    sh.adjustments[0] = 0.1
    sh.fill.solid()
    sh.fill.fore_color.rgb = SOFT
    sh.line.fill.background()
    sh.shadow.inherit = False
    tf = sh.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run(p, n, 40, ACCENT, True)
    p2 = tf.add_paragraph()
    p2.alignment = PP_ALIGN.CENTER
    run(p2, l, 14, INK)

# ============================== 19 · CLOSE ==============================
s = slide("Q&A. If asked about code: github.com/DavidZeff1/agent — the README documents the architecture and design decisions.")
eyebrow(s, "Thanks", Inches(1.6))
tb = textbox(s, Inches(0.75), Inches(2.2), Inches(11.9), Inches(1.8))
p = para(tb.text_frame, True)
run(p, "Questions", 80, INK, True)
run(p, "?", 80, ACCENT, True)
tb = textbox(s, Inches(0.75), Inches(4.3), Inches(11), Inches(0.7))
p = para(tb.text_frame, True)
run(p, "github.com/DavidZeff1/agent", 28, INK)
tb = textbox(s, Inches(0.75), Inches(5.2), Inches(11.8), Inches(0.6))
p = para(tb.text_frame, True)
run(p, "Python · Flask · Groq (llama-3.3-70b) · Playwright · your own Chrome", 18, MUTED)

OUT = "/Users/davidzeff/Desktop/Job Agent Presentation.pptx"
prs.save(OUT)
print("saved:", OUT, "| slides:", len(prs.slides.__iter__.__self__._sldIdLst))
