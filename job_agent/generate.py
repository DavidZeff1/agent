"""Tailored resume + cover letter + pre-filled application packet.

Why a "packet" and not real auto-submit: see the README. Most job sites require an account,
run bot detection, and gate submission behind CAPTCHAs, so blindly POSTing forms is fragile and
often against their terms. Instead we produce, per job, everything needed to apply in ~1 minute:
an ATS-friendly resume, a tailored cover letter, and an ``application_form.md`` that maps the
common form fields to your stored answers — you paste and click submit.

ATS-friendly design
-------------------
The resume is assembled deterministically from a fixed, single-column template with standard
section headings (Professional Summary, Skills, Experience, Projects, Education). The LLM only
supplies *content* (a tailored summary, reordered skills, rewritten-but-truthful bullets), never
layout — so output stays parseable: no tables, columns, images, headers/footers, or exotic glyphs.
A plain-text ``resume.txt`` (the most ATS-safe form) is emitted alongside the Markdown version.
"""
from __future__ import annotations

import re as _re
from typing import List, Optional

from . import util
from .profile_store import Profile
from .scraper import Job

# ----------------------------------------------------------------------------
# LLM tailoring (one call per job)
# ----------------------------------------------------------------------------

_TAILOR_SYSTEM = (
    "You tailor a candidate's resume and write a cover letter for one specific job.\n"
    "CRITICAL RULES:\n"
    "- Use ONLY facts present in the candidate profile. Never invent employers, job titles, dates, "
    "degrees, certifications, metrics, or skills the candidate does not have.\n"
    "- You may rephrase, reprioritize, and surface the most relevant true details, and mirror the "
    "posting's real terminology where it genuinely applies to the candidate.\n"
    "- Keep everything ATS-friendly: plain professional language, standard terms, and real keywords "
    "from the posting that match the candidate's actual experience.\n"
    "- LANGUAGE: write the summary, bullets, highlights, and cover letter in the language of the "
    "JOB POSTING (a Hebrew posting gets Hebrew documents, an English posting gets English ones), "
    "even if the profile is written in another language. Keep company names, technology names, "
    "and other proper nouns unchanged.\n"
    "- In Hebrew text, write abbreviations with the Hebrew gershayim character ״ (ש״ח, בע״מ), "
    "never an ASCII double-quote, so the JSON stays valid.\n"
    "Return a JSON object with keys: summary (3-4 sentence tailored professional summary), "
    "ordered_skills (the candidate's OWN skills reordered with the most job-relevant first), "
    "experience_bullets (object keyed by the integer role index shown in [brackets]; each value an "
    "array of 2-4 concise bullets starting with a strong verb, using real metrics only if present), "
    "project_highlights (object keyed by the integer project index; arrays of 1-3 bullets), "
    "cover_letter (3 short paragraphs addressed to the company, specific, no bracket placeholders), "
    "keywords_used (array of posting keywords you legitimately incorporated)."
)


def _indexed_roles(profile: Profile) -> str:
    out = []
    for i, e in enumerate(profile.experience):
        cur = " | ".join(e.achievements) or e.description
        out.append(
            f"[{i}] {e.title} at {e.company} ({e.start_date}-{e.end_date or 'Present'}); "
            f"tech: {', '.join(e.tech)}; current bullets: {cur}"
        )
    return "\n".join(out) or "(none)"


def _indexed_projects(profile: Profile) -> str:
    out = []
    for i, p in enumerate(profile.projects):
        out.append(
            f"[{i}] {p.name}; tech: {', '.join(p.tech)}; desc: {p.description}; "
            f"highlights: {' | '.join(p.highlights)}"
        )
    return "\n".join(out) or "(none)"


def _coerce_bullets(val, n: int) -> dict:
    """Normalize model output to {str(index): [bullets]}, tolerating list or dict."""
    result: dict[str, list] = {}
    if isinstance(val, dict):
        for k, v in val.items():
            key = str(k).strip().lstrip("[").rstrip("]")
            result[key] = [str(x) for x in v] if isinstance(v, list) else [str(v)]
    elif isinstance(val, list):
        for i, v in enumerate(val):
            result[str(i)] = [str(x) for x in v] if isinstance(v, list) else [str(v)]
    return result


def tailor(profile: Profile, job: Job, llm) -> Optional[dict]:
    """Return tailored content for one job, or ``None`` if the LLM call fails."""
    # Name the output language explicitly — "match the posting" alone is ignored when the
    # profile's language dominates the context.
    lang_name = "Hebrew (עברית)" if detect_lang(f"{job.title} {job.description[:600]}") == "he" else "English"
    user = (
        f"CANDIDATE PROFILE:\n{profile.to_text_block(4500)}\n\n"
        f"ROLES (index in brackets):\n{_indexed_roles(profile)}\n\n"
        f"PROJECTS (index in brackets):\n{_indexed_projects(profile)}\n\n"
        f"JOB POSTING:\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
        f"Description:\n{util.truncate(job.description, 3000)}\n\n"
        f"OUTPUT LANGUAGE: {lang_name}. Write the summary, every bullet, and the cover letter "
        f"entirely in {lang_name}; translate profile facts as needed but keep company and "
        f"technology names unchanged."
    )
    try:
        data = llm.complete_json(_TAILOR_SYSTEM, user, temperature=0.35, max_tokens=2000)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] tailoring failed, using untailored resume: {e}")
        return None
    data["experience_bullets"] = _coerce_bullets(data.get("experience_bullets"), len(profile.experience))
    data["project_highlights"] = _coerce_bullets(data.get("project_highlights"), len(profile.projects))
    if isinstance(data.get("ordered_skills"), list):
        data["ordered_skills"] = [str(s) for s in data["ordered_skills"] if str(s).strip()]
    if data.get("cover_letter"):
        data["cover_letter"] = _finish_letter(str(data["cover_letter"]), profile)
    return data


# ----------------------------------------------------------------------------
# Reviewer agent: a strict second pass that fact-checks the writer's output
# against the profile before anything reaches the user (writer -> critic -> fix).
# ----------------------------------------------------------------------------

_REVIEW_SYSTEM = (
    "You are a strict reviewer of AI-tailored job application documents. You get the candidate's "
    "profile (the only source of truth), the job posting details, and a tailored draft. Flag ONLY "
    "real problems, each as one issue:\n"
    "- UNSUPPORTED: a specific claim, metric, skill, employer, degree, or certification in the "
    "draft that does not appear in the profile.\n"
    "- WRONG_COMPANY: the cover letter names a different company or role than the posting.\n"
    "- PLACEHOLDER: leftover template text like [Company] or [Your Name].\n"
    "- WRONG_LANGUAGE: the draft is not written in the required language.\n"
    "Do not flag rephrasing, reasonable soft skills, or stylistic choices. Return JSON: "
    '{"ok": true} when the draft is clean, else {"ok": false, "issues": [{"type": "...", '
    '"where": "summary|bullets|cover_letter", "detail": "short description of the problem"}]}.'
)


def review_tailoring(profile: Profile, job: Job, tailoring: dict, llm) -> dict:
    """One critic call. Returns {'ok': bool, 'issues': [...]}; failures count as clean."""
    lang_name = "Hebrew" if detect_lang(f"{job.title} {job.description[:600]}") == "he" else "English"
    bullets = "\n".join(
        f"[role {k}] " + " | ".join(v) for k, v in (tailoring.get("experience_bullets") or {}).items()
    )
    draft = (
        f"SUMMARY:\n{tailoring.get('summary', '')}\n\nBULLETS:\n{bullets}\n\n"
        f"COVER LETTER:\n{tailoring.get('cover_letter', '')}"
    )
    user = (
        f"CANDIDATE PROFILE (source of truth):\n{profile.to_text_block(2500)}\n\n"
        f"POSTING: {job.title} at {job.company}. REQUIRED LANGUAGE: {lang_name}.\n\n"
        f"TAILORED DRAFT TO REVIEW:\n{draft}"
    )
    try:
        data = llm.complete_json(_REVIEW_SYSTEM, user, temperature=0.1, max_tokens=700)
    except Exception as e:  # noqa: BLE001 - a broken reviewer must not block the packet
        print(f"  [warn] review pass failed, keeping the draft as written: {e}")
        return {"ok": True, "issues": [], "checked": False}
    issues = [i for i in (data.get("issues") or []) if isinstance(i, dict) and i.get("detail")]
    return {"ok": bool(data.get("ok", not issues)) and not issues, "issues": issues, "checked": True}


def repair_tailoring(profile: Profile, job: Job, tailoring: dict, issues: list, llm) -> Optional[dict]:
    """One fix call: rewrite the draft with the reviewer's findings resolved."""
    problems = "\n".join(f"- [{i.get('type', '?')} in {i.get('where', '?')}] {i['detail']}" for i in issues)
    system = _TAILOR_SYSTEM + (
        "\n\nA strict reviewer found these problems in a previous draft. Produce a corrected "
        "version that resolves EVERY listed problem (remove or replace unsupported claims with "
        "facts from the profile) while keeping everything that was fine:\n" + problems
    )
    user = (
        f"CANDIDATE PROFILE:\n{profile.to_text_block(3000)}\n\n"
        f"ROLES (index in brackets):\n{_indexed_roles(profile)}\n\n"
        f"JOB POSTING:\nTitle: {job.title}\nCompany: {job.company}\n\n"
        f"PREVIOUS DRAFT (fix the listed problems):\n"
        f"summary: {tailoring.get('summary', '')}\n"
        f"experience_bullets: {tailoring.get('experience_bullets', {})}\n"
        f"cover_letter: {tailoring.get('cover_letter', '')}"
    )
    try:
        data = llm.complete_json(system, user, temperature=0.3, max_tokens=2000)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] repair pass failed, keeping the original draft: {e}")
        return None
    fixed = dict(tailoring)
    for key in ("summary", "cover_letter"):
        if data.get(key):
            fixed[key] = str(data[key])
    if data.get("experience_bullets"):
        fixed["experience_bullets"] = _coerce_bullets(data["experience_bullets"], len(profile.experience))
    if data.get("project_highlights"):
        fixed["project_highlights"] = _coerce_bullets(data["project_highlights"], len(profile.projects))
    if fixed.get("cover_letter"):
        fixed["cover_letter"] = _finish_letter(str(fixed["cover_letter"]), profile)
    return fixed


def _finish_letter(letter: str, profile: Profile) -> str:
    """Normalize paragraph breaks (models often emit single newlines) and ensure a sign-off."""
    letter = letter.strip()
    if "\n\n" not in letter and "\n" in letter:
        letter = "\n\n".join(p.strip() for p in letter.split("\n") if p.strip())
    name = profile.full_name or profile.preferred_name
    if name and name not in letter.rsplit("\n\n", 1)[-1]:
        closing = "בברכה," if detect_lang(letter) == "he" else "Sincerely,"
        letter += f"\n\n{closing}\n{name}"
    return letter


# ----------------------------------------------------------------------------
# Resume rendering (deterministic, ATS-friendly)
# ----------------------------------------------------------------------------


def _doc_lang(profile: Profile, tailoring: Optional[dict]) -> str:
    """Language of the document being rendered: tailored content decides, else the profile."""
    t = tailoring or {}
    sample = t.get("summary") or profile.summary or profile.headline or ""
    if not sample and profile.experience:
        sample = " ".join(profile.experience[0].achievements[:2]) or profile.experience[0].title
    return detect_lang(sample)


def render_resume(profile: Profile, tailoring: Optional[dict] = None, markdown: bool = False) -> str:
    t = tailoring or {}
    L: List[str] = []
    H = HEADINGS[_doc_lang(profile, tailoring)]

    def heading(s: str) -> str:
        return f"\n## {s}" if markdown else f"\n{s.upper()}"

    def role_head(text: str) -> str:
        return f"**{text}**" if markdown else text

    # Header
    L.append(f"# {profile.full_name}" if markdown else (profile.full_name or "Your Name"))
    c = profile.contact
    loc = ", ".join(x for x in [c.city, c.state, c.country] if x)
    contact = " | ".join(
        x for x in [c.email, c.phone, loc, profile.links.linkedin, profile.links.github, profile.links.portfolio] if x
    )
    if contact:
        L.append(contact)
    if profile.headline:
        L.append(profile.headline)

    summary = (t.get("summary") or profile.summary or "").strip()
    if summary:
        L.append(heading(H["summary"]))
        L.append(summary)

    skills = t.get("ordered_skills") or profile.skills
    if skills:
        L.append(heading(H["skills"]))
        L.append(", ".join(skills))

    if profile.experience:
        L.append(heading(H["experience"]))
        exp_bullets = t.get("experience_bullets") or {}
        for i, e in enumerate(profile.experience):
            dates = " - ".join(x for x in [e.start_date, e.end_date or "Present"] if x)
            right = " | ".join(x for x in [e.location, dates] if x)
            L.append(role_head(f"{e.title}, {e.company}") + (f" — {right}" if right else ""))
            bullets = exp_bullets.get(str(i)) or e.achievements or ([e.description] if e.description else [])
            for b in bullets:
                L.append(f"- {b}")

    if profile.projects:
        L.append(heading(H["projects"]))
        proj_bullets = t.get("project_highlights") or {}
        for i, p in enumerate(profile.projects):
            techs = f" ({', '.join(p.tech)})" if p.tech else ""
            link = f" — {p.link or p.repo}" if (p.link or p.repo) else ""
            L.append(role_head(f"{p.name}{techs}") + link)
            bullets = proj_bullets.get(str(i)) or p.highlights or ([p.description] if p.description else [])
            for b in bullets:
                L.append(f"- {b}")

    if profile.education:
        L.append(heading(H["education"]))
        for ed in profile.education:
            degree = " ".join(x for x in [ed.degree, ed.field_of_study] if x)
            dates = " - ".join(x for x in [ed.start_date, ed.end_date] if x)
            L.append(role_head(f"{degree}, {ed.institution}".strip(", ")))
            meta = " | ".join(x for x in [ed.location, dates] if x)
            if ed.gpa:
                meta = (meta + " | " if meta else "") + f"GPA: {ed.gpa}"
            if meta:
                L.append(meta)
            if ed.honors:
                L.append(ed.honors)

    if profile.certifications:
        L.append(heading(H["certifications"]))
        for cert in profile.certifications:
            if cert.name:
                L.append(f"- {cert.name}, {cert.issuer}" + (f" ({cert.date})" if cert.date else ""))

    if profile.languages:
        L.append(heading(H["languages"]))
        L.append(", ".join(f"{l.language} ({l.proficiency})" for l in profile.languages if l.language))

    return "\n".join(L).strip() + "\n"


# ----------------------------------------------------------------------------
# HTML rendering (for PDF export via the local browser)
# ----------------------------------------------------------------------------

_DOC_CSS = """
@page { size: A4; margin: 17mm 17mm 19mm; }
body { font: 10.5pt/1.42 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #1a1a1a;
       margin: 0; -webkit-print-color-adjust: exact; }
/* Every text block resolves its own direction (unicode-bidi: plaintext), so Hebrew lines
   run right-to-left while English lines, numbers, dates and inline Latin terms stay
   left-to-right — punctuation and commas land on the correct side of each line. */
h1, h2, p, li, .contact, .headline, .meta, .rt { unicode-bidi: plaintext; text-align: start; }
h1 { font-size: 20pt; font-weight: 700; margin: 0 0 3pt; }
.contact { font-size: 9pt; color: #444; margin: 0 0 1pt; }
.headline { font-size: 11pt; color: #444; margin: 0 0 4pt; }
h2 { font-size: 9.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: .09em;
     color: #333; border-bottom: .75pt solid #c5c5c5; padding-bottom: 2.5pt; margin: 13pt 0 6pt; }
p { margin: 0 0 4pt; }
ul { margin: 3pt 0 2pt; padding-inline-start: 14pt; }
li { margin: 2pt 0; }
.rolehead { display: flex; justify-content: space-between; align-items: baseline;
            gap: 10pt; margin-top: 8pt; }
.rt { font-weight: 700; }
.meta { color: #555; font-size: 9pt; flex-shrink: 0; }
.letter { font-size: 11pt; line-height: 1.55; }
.letter p { margin: 0 0 10pt; }
.letter .ldate { color: #555; font-size: 10pt; margin-bottom: 16pt; }
"""


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_RTL_RE = _re.compile(r"[֐-ࣿ]")  # Hebrew + Arabic blocks


def _dir(s) -> str:
    """Explicit direction for a block: Chrome mirrors list markers and flex order reliably
    only with a real dir attribute, not with unicode-bidi/auto resolution."""
    return "rtl" if _RTL_RE.search(str(s or "")) else "ltr"


def detect_lang(text: str) -> str:
    """'he' when a meaningful share of the letters are Hebrew, else 'en'."""
    text = str(text or "")
    hebrew = len(_re.findall(r"[א-ת]", text))
    latin = len(_re.findall(r"[A-Za-z]", text))
    return "he" if hebrew > 0 and hebrew >= 0.25 * max(latin, 1) else "en"


# Section headings per document language. The document language follows the *content*:
# tailored documents come back in the posting's language, untailored ones in the profile's.
HEADINGS = {
    "en": {"summary": "Professional Summary", "skills": "Skills", "experience": "Experience",
           "projects": "Projects", "education": "Education", "certifications": "Certifications",
           "languages": "Languages"},
    "he": {"summary": "תקציר מקצועי", "skills": "כישורים", "experience": "ניסיון תעסוקתי",
           "projects": "פרויקטים", "education": "השכלה", "certifications": "הסמכות",
           "languages": "שפות"},
}


def _bdi(s) -> str:
    """Isolate one mixed-language segment so neighbours can't reorder it (RTL safety)."""
    return f"<bdi>{_esc(s)}</bdi>"


def _display_url(u: str) -> str:
    return (u or "").replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")


def _html_doc(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{_DOC_CSS}</style></head><body>{body}</body></html>"


def _rolehead(left: str, right: str) -> str:
    """The classic resume line: bold role on one side, muted dates/location on the other.
    The row direction follows the role text, so Hebrew roles sit on the right with dates
    on the left; each side is a <bdi> so mixed content can't reorder across sides."""
    r = f"<bdi class='meta'>{_esc(right)}</bdi>" if right else ""
    return f"<div class='rolehead' dir='{_dir(left)}'><bdi class='rt'>{_esc(left)}</bdi>{r}</div>"


def render_resume_html(profile: Profile, tailoring: Optional[dict] = None) -> str:
    """Same content as render_resume, laid out as printable HTML (single column, ATS-safe)."""
    t = tailoring or {}
    H = HEADINGS[_doc_lang(profile, tailoring)]
    B: List[str] = []
    B.append(f"<h1>{_esc(profile.full_name or 'Your Name')}</h1>")
    c = profile.contact
    loc = ", ".join(x for x in [c.city, c.state, c.country] if x)
    parts = [c.email, c.phone, loc] + [_display_url(u) for u in
             (profile.links.linkedin, profile.links.github, profile.links.portfolio) if u]
    contact = " · ".join(_bdi(x) for x in parts if x)
    if contact:
        B.append(f"<div class='contact'>{contact}</div>")
    if profile.headline:
        B.append(f"<div class='headline'>{_esc(profile.headline)}</div>")

    summary = (t.get("summary") or profile.summary or "").strip()
    if summary:
        B.append(f"<h2>{H['summary']}</h2>")
        B.append(f"<p>{_esc(summary)}</p>")

    skills = t.get("ordered_skills") or profile.skills
    if skills:
        B.append(f"<h2>{H['skills']}</h2>")
        B.append("<p>" + " · ".join(_bdi(s) for s in skills) + "</p>")

    def bullets(items) -> str:
        # The whole list follows the direction of its first line, so every marker sits on
        # one consistent side; unicode-bidi still orders each line's text correctly, so an
        # English line inside a Hebrew list stays readable.
        if not items:
            return ""
        d = _dir(items[0])
        return f"<ul dir='{d}'>" + "".join(f"<li>{_esc(b)}</li>" for b in items) + "</ul>"

    if profile.experience:
        B.append(f"<h2>{H['experience']}</h2>")
        exp_bullets = t.get("experience_bullets") or {}
        for i, e in enumerate(profile.experience):
            dates = " – ".join(x for x in [e.start_date, e.end_date or "Present"] if x)
            right = "  |  ".join(x for x in [e.location, dates] if x)
            B.append(_rolehead(f"{e.title}, {e.company}".strip(", "), right))
            bl = exp_bullets.get(str(i)) or e.achievements or ([e.description] if e.description else [])
            B.append(bullets(bl))

    if profile.projects:
        B.append(f"<h2>{H['projects']}</h2>")
        proj_bullets = t.get("project_highlights") or {}
        for i, p in enumerate(profile.projects):
            techs = f" ({', '.join(p.tech)})" if p.tech else ""
            B.append(_rolehead(f"{p.name}{techs}", _display_url(p.link or p.repo)))
            bl = proj_bullets.get(str(i)) or p.highlights or ([p.description] if p.description else [])
            B.append(bullets(bl))

    if profile.education:
        B.append(f"<h2>{H['education']}</h2>")
        for ed in profile.education:
            degree = " ".join(x for x in [ed.degree, ed.field_of_study] if x)
            dates = " – ".join(x for x in [ed.start_date, ed.end_date] if x)
            meta = "  |  ".join(x for x in [ed.location, dates] if x)
            if ed.gpa:
                meta = (meta + "  |  " if meta else "") + f"GPA: {ed.gpa}"
            B.append(_rolehead(f"{degree}, {ed.institution}".strip(", "), meta))
            if ed.honors:
                B.append(f"<p class='meta'>{_esc(ed.honors)}</p>")

    if profile.certifications:
        B.append(f"<h2>{H['certifications']}</h2>")
        B.append(bullets([f"{c.name}, {c.issuer}" + (f" ({c.date})" if c.date else "")
                          for c in profile.certifications if c.name]))

    if profile.languages:
        B.append(f"<h2>{H['languages']}</h2>")
        B.append("<p>" + " · ".join(_bdi(f"{l.language} ({l.proficiency})".replace(" ()", ""))
                                    for l in profile.languages if l.language) + "</p>")

    return _html_doc("".join(B))


def render_letter_html(profile: Profile, letter: str) -> str:
    from datetime import date

    B = [f"<p class='ldate' dir='auto'>{date.today().strftime('%B %d, %Y')}</p>"]
    B += [f"<p dir='auto'>{_esc(p.strip())}</p>" for p in letter.split("\n\n") if p.strip()]
    return _html_doc(f"<div class='letter'>{''.join(B)}</div>")


# ----------------------------------------------------------------------------
# Cover letter + form packet
# ----------------------------------------------------------------------------


def _fallback_cover_letter(profile: Profile, job: Job) -> str:
    name = profile.full_name or profile.preferred_name or ""
    top_skills = ", ".join(profile.skills[:6])
    background = profile.headline or "software"
    summary = util.truncate(profile.summary, 600)
    if detect_lang(f"{job.title} {job.description[:400]}") == "he":
        return (
            f"לכבוד צוות הגיוס של {job.company},\n\n"
            f"ברצוני להגיש מועמדות למשרת {job.title}. עם רקע ב{background} וניסיון מעשי ב-"
            f"{top_skills or 'התחומים הנדרשים'}, אני מאמין שאוכל לתרום כבר מהיום הראשון.\n\n"
            f"{summary or 'הניסיון שלי תואם היטב את דרישות התפקיד.'}\n\n"
            f"אשמח לשוחח ולספר עוד. תודה על הזמן ותשומת הלב.\n\n"
            f"בברכה,\n{name}\n{profile.contact.email}\n{profile.contact.phone}"
        ).strip()
    return (
        f"Dear {job.company} Hiring Team,\n\n"
        f"I am writing to apply for the {job.title} position at {job.company}. With a background in "
        f"{background} and hands-on experience across {top_skills or 'the required areas'}, I believe "
        f"I can make an immediate contribution.\n\n"
        f"{summary or 'My experience aligns closely with what this role requires.'}\n\n"
        f"I would welcome the opportunity to discuss how my background fits {job.company}'s needs. "
        f"Thank you for your time and consideration.\n\n"
        f"Sincerely,\n{name}\n{profile.contact.email}\n{profile.contact.phone}"
    ).strip()


def build_form_packet_md(profile: Profile, job: Job, tailoring: Optional[dict]) -> str:
    L: List[str] = []
    L.append(f"# Application packet — {job.title} @ {job.company}")
    L.append("")
    L.append(f"- Posting: {job.url}")
    if job.apply_url and job.apply_url != job.url:
        L.append(f"- Apply link: {job.apply_url}")
    if job.location:
        L.append(f"- Location: {job.location}")
    if job.salary:
        L.append(f"- Listed salary: {job.salary}")
    L.append("")
    L.append("## Copy-paste form fields")
    L.append("")
    for k, v in profile.flat_fields().items():
        L.append(f"- **{k}:** {v}")
    L.append("")
    if tailoring and tailoring.get("keywords_used"):
        L.append("## Keywords woven into resume/letter (ATS)")
        L.append(", ".join(str(x) for x in tailoring["keywords_used"]))
        L.append("")
    L.append("## Common free-text answers")
    L.append("")
    L.append(f"**Why do you want to work at {job.company}?**")
    L.append(_why_company(profile, job, tailoring))
    L.append("")
    L.append("**Why are you a good fit for this role?**")
    L.append(_why_fit(profile, job, tailoring))
    L.append("")
    L.append("## Any other question the form asks")
    L.append("Generate an answer from your stored profile with:")
    L.append("")
    L.append('    python -m job_agent answer "<paste the exact question here>"')
    L.append("")
    return "\n".join(L)


def _why_company(profile: Profile, job: Job, tailoring: Optional[dict]) -> str:
    if tailoring and tailoring.get("cover_letter"):
        # Reuse the opening of the tailored letter as a starting point.
        first = tailoring["cover_letter"].split("\n\n")
        if len(first) > 1:
            return first[1].strip()
    return (
        f"I'm drawn to {job.company} because this {job.title} role matches my experience with "
        f"{', '.join(profile.skills[:4])}. (Personalize with 1 sentence about the company.)"
    )


def _why_fit(profile: Profile, job: Job, tailoring: Optional[dict]) -> str:
    if tailoring and tailoring.get("summary"):
        return tailoring["summary"]
    return util.truncate(profile.summary, 400) or "See resume for relevant experience."


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------


def generate_application(profile: Profile, job: Job, out_dir, llm=None, progress=None) -> dict:
    """Generate all artifacts for one job into ``out_dir/<company>__<title>/`` and return paths.

    ``progress`` is an optional ``callable(str)`` that receives a short human-readable note
    at each phase (used by the web UI to show what's happening during slow steps).
    """
    import os

    def note(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001 - progress display must never break generation
                pass

    if llm is not None:
        note(f"Asking the AI to tailor your resume and cover letter for {job.company}… (~15 seconds)")
    tailoring = tailor(profile, job, llm) if llm is not None else None

    # Reviewer agent: fact-check the draft against the profile, repair once if needed.
    review = {"checked": False, "issues": [], "fixed": False}
    if tailoring is not None:
        note(f"Fact-checking the documents against your profile…")
        review = review_tailoring(profile, job, tailoring, llm)
        if review["checked"] and review["issues"]:
            note(f"Fixing {len(review['issues'])} issue(s) the reviewer found…")
            fixed = repair_tailoring(profile, job, tailoring, review["issues"], llm)
            if fixed is not None:
                tailoring = fixed
                review["fixed"] = True

    note("Writing the resume and cover letter…")
    resume_md = render_resume(profile, tailoring, markdown=True)
    resume_txt = render_resume(profile, tailoring, markdown=False)
    cover = (tailoring or {}).get("cover_letter") or _fallback_cover_letter(profile, job)
    packet = build_form_packet_md(profile, job, tailoring)

    folder = os.path.join(str(out_dir), f"{util.slugify(job.company, 32)}__{util.slugify(job.title, 40)}")
    paths = {
        "dir": folder,
        "resume_md": util.write_text(os.path.join(folder, "resume.md"), resume_md),
        "resume_txt": util.write_text(os.path.join(folder, "resume.txt"), resume_txt),
        "cover_letter": util.write_text(os.path.join(folder, "cover_letter.md"), cover + "\n"),
        "application_form": util.write_text(os.path.join(folder, "application_form.md"), packet),
        "job": util.write_text(
            os.path.join(folder, "job.md"),
            f"# {job.title} @ {job.company}\n\n{job.url}\n\nLocation: {job.location}\n"
            f"Salary: {job.salary}\nTags: {', '.join(job.tags)}\n\n---\n\n{job.description}\n",
        ),
    }

    # PDF copies (forms want file uploads); best-effort via the locally installed browser.
    from . import pdfgen

    note("Creating the PDF files…")
    resume_pdf = os.path.join(folder, "resume.pdf")
    if pdfgen.html_to_pdf(render_resume_html(profile, tailoring), resume_pdf):
        paths["resume_pdf"] = resume_pdf
    letter_pdf = os.path.join(folder, "cover_letter.pdf")
    if pdfgen.html_to_pdf(render_letter_html(profile, cover), letter_pdf):
        paths["cover_letter_pdf"] = letter_pdf
    note("Saving the application packet…")
    answers = {
        "job": {"id": job.id, "title": job.title, "company": job.company,
                "url": job.url, "apply_url": job.apply_url},
        "tailored": tailoring is not None,
        "review": review,
        "keywords_used": (tailoring or {}).get("keywords_used", []),
        "common_answers": {
            "why_company": _why_company(profile, job, tailoring),
            "why_fit": _why_fit(profile, job, tailoring),
        },
        "fields": profile.flat_fields(),
    }
    paths["answers_json"] = util.write_json(os.path.join(folder, "answers.json"), answers)
    paths["tailored"] = tailoring is not None
    # Full document text, for callers that can't read the files back (hosted mode keeps
    # packets in the browser; resume_html/letter_html power its print-to-PDF).
    paths["content"] = {
        "resume_md": resume_md,
        "resume_txt": resume_txt,
        "resume_html": render_resume_html(profile, tailoring),
        "cover_letter": cover,
        "letter_html": render_letter_html(profile, cover),
        "form_md": packet,
    }
    return paths
