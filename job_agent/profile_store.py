"""User profile: the single source of truth for every field an application might ask.

You enter your data once (``python -m job_agent setup``) and it is stored as JSON under
``JOB_AGENT_HOME``. The schema below is deliberately exhaustive, and it carries an open
``additional`` key-value map plus an ``answer_question`` helper so that *unexpected*
application questions can still be answered from what you've stored.

Serialization is tolerant: every field has a default, so a partial/hand-edited JSON file
still loads, and new fields added in future versions won't break old files.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, get_args, get_origin, get_type_hints

from . import util
from .config import get_settings

# ----------------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------------


@dataclass
class Contact:
    email: str = ""
    phone: str = ""
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""


@dataclass
class Links:
    github: str = ""
    linkedin: str = ""
    portfolio: str = ""
    website: str = ""
    twitter: str = ""
    other: Dict[str, str] = field(default_factory=dict)


@dataclass
class WorkAuthorization:
    country: str = ""
    status: str = ""  # e.g. "Citizen", "Permanent Resident", "H-1B", "Requires sponsorship"
    requires_sponsorship: bool = False
    notes: str = ""


@dataclass
class Education:
    institution: str = ""
    degree: str = ""  # e.g. "BSc", "MSc", "PhD"
    field_of_study: str = ""
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""
    location: str = ""
    honors: str = ""
    notes: str = ""


@dataclass
class Experience:
    company: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str = ""  # "" or "Present"
    location: str = ""
    employment_type: str = ""  # Full-time, Internship, Contract...
    description: str = ""
    achievements: List[str] = field(default_factory=list)
    tech: List[str] = field(default_factory=list)


@dataclass
class Project:
    name: str = ""
    description: str = ""
    tech: List[str] = field(default_factory=list)
    link: str = ""
    repo: str = ""
    role: str = ""
    highlights: List[str] = field(default_factory=list)


@dataclass
class Certification:
    name: str = ""
    issuer: str = ""
    date: str = ""
    credential_id: str = ""


@dataclass
class Language:
    language: str = ""
    proficiency: str = ""  # Native, Fluent, Professional, Conversational, Basic


@dataclass
class Preferences:
    desired_titles: List[str] = field(default_factory=list)
    desired_locations: List[str] = field(default_factory=list)
    remote_ok: bool = True
    onsite_ok: bool = True
    open_to_relocation: bool = False
    relocation_notes: str = ""
    salary_currency: str = "USD"
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_notes: str = ""
    availability: str = ""  # e.g. "Immediately", "2 weeks notice"
    earliest_start_date: str = ""
    willing_to_travel: str = ""
    desired_employment_types: List[str] = field(default_factory=list)


@dataclass
class Demographics:
    """Voluntary EEO / self-identification fields (optional; left blank by default)."""

    gender: str = ""
    pronouns: str = ""
    race_ethnicity: str = ""
    veteran_status: str = ""
    disability_status: str = ""


@dataclass
class Profile:
    full_name: str = ""
    preferred_name: str = ""
    headline: str = ""  # short professional title, e.g. "Backend Engineer"
    summary: str = ""  # professional summary / bio
    date_of_birth: str = ""
    contact: Contact = field(default_factory=Contact)
    nationalities: List[str] = field(default_factory=list)
    work_authorizations: List[WorkAuthorization] = field(default_factory=list)
    education: List[Education] = field(default_factory=list)
    experience: List[Experience] = field(default_factory=list)
    projects: List[Project] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)
    languages: List[Language] = field(default_factory=list)
    certifications: List[Certification] = field(default_factory=list)
    links: Links = field(default_factory=Links)
    preferences: Preferences = field(default_factory=Preferences)
    demographics: Demographics = field(default_factory=Demographics)
    # Open-ended overflow: answers to arbitrary application questions keyed by question text.
    additional: Dict[str, Any] = field(default_factory=dict)

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Profile":
        return _from_dict(cls, data or {})

    # -- derived views -----------------------------------------------------
    def skill_set(self) -> set[str]:
        """All skill-like tokens (skills + per-role tech + project tech), lowercased."""
        out: set[str] = set()
        for s in self.skills:
            if s.strip():
                out.add(s.strip().lower())
        for exp in self.experience:
            out.update(t.strip().lower() for t in exp.tech if t.strip())
        for proj in self.projects:
            out.update(t.strip().lower() for t in proj.tech if t.strip())
        return out

    def years_of_experience(self) -> float:
        """Rough total from experience date ranges (best-effort; used only as a heuristic)."""
        import re

        total = 0.0
        for exp in self.experience:
            start = _year(exp.start_date)
            end = _year(exp.end_date) or _this_year()
            if start:
                total += max(0.0, end - start)
        return round(total, 1)

    def to_text_block(self, max_chars: int = 6000) -> str:
        """Readable digest of the whole profile for LLM context (scoring, tailoring, Q&A)."""
        L: List[str] = []
        name = self.full_name or self.preferred_name or "(name not set)"
        L.append(f"NAME: {name}" + (f" ({self.preferred_name})" if self.preferred_name and self.preferred_name != self.full_name else ""))
        if self.headline:
            L.append(f"HEADLINE: {self.headline}")
        if self.summary:
            L.append(f"SUMMARY: {self.summary}")
        c = self.contact
        loc = ", ".join(x for x in [c.city, c.state, c.country] if x)
        contact_bits = [b for b in [c.email, c.phone, loc] if b]
        if contact_bits:
            L.append("CONTACT: " + " | ".join(contact_bits))
        if self.nationalities:
            L.append("NATIONALITIES: " + ", ".join(self.nationalities))
        if self.work_authorizations:
            auth = "; ".join(
                f"{w.country}: {w.status}" + (" (needs sponsorship)" if w.requires_sponsorship else "")
                for w in self.work_authorizations
            )
            L.append("WORK AUTHORIZATION: " + auth)
        if self.skills:
            L.append("SKILLS: " + ", ".join(self.skills))
        if self.languages:
            L.append("LANGUAGES: " + ", ".join(f"{x.language} ({x.proficiency})" for x in self.languages if x.language))
        if self.experience:
            L.append("EXPERIENCE:")
            for e in self.experience:
                dates = " - ".join(x for x in [e.start_date, e.end_date or "Present"] if x)
                L.append(f"  * {e.title} at {e.company} ({dates}) [{', '.join(e.tech)}]".rstrip())
                if e.description:
                    L.append(f"    {util.truncate(e.description, 300)}")
                for a in e.achievements:
                    L.append(f"    - {a}")
        if self.projects:
            L.append("PROJECTS:")
            for p in self.projects:
                L.append(f"  * {p.name} [{', '.join(p.tech)}]: {util.truncate(p.description, 200)}")
                for h in p.highlights:
                    L.append(f"    - {h}")
        if self.education:
            L.append("EDUCATION:")
            for ed in self.education:
                dates = " - ".join(x for x in [ed.start_date, ed.end_date] if x)
                gpa = f", GPA {ed.gpa}" if ed.gpa else ""
                L.append(f"  * {ed.degree} {ed.field_of_study}, {ed.institution} ({dates}{gpa})".replace("  ", " "))
        if self.certifications:
            L.append("CERTIFICATIONS: " + "; ".join(f"{c.name} ({c.issuer})" for c in self.certifications if c.name))
        p = self.preferences
        pref_bits = []
        if p.desired_titles:
            pref_bits.append("titles=" + "/".join(p.desired_titles))
        if p.desired_locations:
            pref_bits.append("locations=" + "/".join(p.desired_locations))
        pref_bits.append("remote_ok=" + str(p.remote_ok))
        pref_bits.append("relocation=" + str(p.open_to_relocation))
        if p.salary_min or p.salary_max:
            pref_bits.append(f"salary={p.salary_currency} {p.salary_min or '?'}-{p.salary_max or '?'}")
        if p.availability:
            pref_bits.append("availability=" + p.availability)
        L.append("PREFERENCES: " + ", ".join(pref_bits))
        links = {k: v for k, v in {"github": self.links.github, "linkedin": self.links.linkedin,
                                   "portfolio": self.links.portfolio, "website": self.links.website}.items() if v}
        if links:
            L.append("LINKS: " + ", ".join(f"{k}={v}" for k, v in links.items()))
        if self.additional:
            L.append("ADDITIONAL Q&A ON FILE:")
            for q, a in self.additional.items():
                L.append(f"  * {q}: {util.truncate(str(a), 200)}")
        return util.truncate("\n".join(L), max_chars)

    def flat_fields(self) -> Dict[str, str]:
        """Common application form fields mapped to stored values (for autofill packets)."""
        c = self.contact
        p = self.preferences
        primary_auth = self.work_authorizations[0] if self.work_authorizations else None
        fields: Dict[str, str] = {
            "Full name": self.full_name,
            "Preferred name": self.preferred_name,
            "Email": c.email,
            "Phone": c.phone,
            "Address line 1": c.address_line1,
            "Address line 2": c.address_line2,
            "City": c.city,
            "State/Region": c.state,
            "Postal code": c.postal_code,
            "Country": c.country,
            "Nationalities": ", ".join(self.nationalities),
            "Work authorization": "; ".join(f"{w.country}: {w.status}" for w in self.work_authorizations),
            "Requires visa sponsorship": ("Yes" if (primary_auth and primary_auth.requires_sponsorship) else "No")
            if primary_auth else "",
            "Open to relocation": "Yes" if p.open_to_relocation else "No",
            "Open to remote": "Yes" if p.remote_ok else "No",
            "Desired salary": (f"{p.salary_currency} {p.salary_min or ''}"
                               + (f"-{p.salary_max}" if p.salary_max else "")).strip()
            if (p.salary_min or p.salary_max) else "",
            "Availability / notice period": p.availability,
            "Earliest start date": p.earliest_start_date,
            "LinkedIn": self.links.linkedin,
            "GitHub": self.links.github,
            "Portfolio": self.links.portfolio,
            "Website": self.links.website,
            "Highest education": self._highest_education(),
        }
        return {k: v for k, v in fields.items() if v}

    def _highest_education(self) -> str:
        if not self.education:
            return ""
        ed = self.education[0]
        return f"{ed.degree} {ed.field_of_study}, {ed.institution}".strip()


# ----------------------------------------------------------------------------
# Tolerant (nested) dataclass <- dict conversion
# ----------------------------------------------------------------------------


def _from_dict(cls, data: dict):
    hints = get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _convert(hints.get(f.name, Any), data[f.name])
    return cls(**kwargs)


def _convert(tp, value):
    if value is None:
        return None
    if dataclasses.is_dataclass(tp):
        return _from_dict(tp, value if isinstance(value, dict) else {})
    origin = get_origin(tp)
    if origin is list:
        args = get_args(tp)
        item_tp = args[0] if args else Any
        return [_convert(item_tp, v) for v in (value or [])]
    if origin is dict:
        return dict(value or {})
    # typing.Optional[...] / int | None
    args = get_args(tp)
    if args and type(None) in args:  # Optional[...]
        for a in args:
            if a is not type(None):
                return _convert(a, value)
    return value


def _year(s: str) -> Optional[int]:
    import re

    m = re.search(r"(19|20)\d{2}", s or "")
    return int(m.group(0)) if m else None


def _this_year() -> int:
    from datetime import datetime

    return datetime.now().year


# ----------------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------------


def load_profile(path=None) -> Optional[Profile]:
    path = str(path or get_settings().profile_path)
    data = util.read_json(path, default=None)
    if data is None:
        return None
    return Profile.from_dict(data)


def save_profile(profile: Profile, path=None) -> str:
    s = get_settings().ensure_home()
    path = str(path or s.profile_path)
    return util.write_json(path, profile.to_dict())


def import_profile(src_path: str, dest_path=None) -> Profile:
    data = util.read_json(src_path, default=None)
    if data is None:
        raise ValueError(f"Could not read a JSON profile from {src_path}")
    profile = Profile.from_dict(data)
    save_profile(profile, dest_path)
    return profile


def default_keywords(profile: Optional[Profile]) -> List[str]:
    """Best search keywords when the user didn't type any: desired titles, else headline, else skills."""
    if not profile:
        return []
    kw = [t for t in profile.preferences.desired_titles if t.strip()]
    if not kw and profile.headline:
        kw = [profile.headline]
    if not kw:
        kw = profile.skills[:3]
    return kw


# ----------------------------------------------------------------------------
# Arbitrary application-question answering (the "answer anything" feature)
# ----------------------------------------------------------------------------

_ANSWER_SYSTEM = (
    "You help a candidate fill out job applications using ONLY the facts in their profile. "
    "Answer the question concisely and in the first person, as it should appear in the "
    "application field. Never invent facts, employers, degrees, or numbers that are not in "
    "the profile. If the profile genuinely does not contain the answer, reply exactly with "
    "'NOT IN PROFILE' so the user knows to add it."
)


def answer_question(profile: Profile, question: str, llm=None, save: bool = True) -> str:
    """Answer an arbitrary application question from stored data.

    Uses the LLM when available (reads the whole profile as context); otherwise falls back
    to a simple lookup over the ``additional`` map. Confident answers are cached back into
    ``additional`` so the profile grows richer over time.
    """
    question = (question or "").strip()
    if not question:
        return ""
    # Fast path: exact/looser match already stored.
    for q, a in profile.additional.items():
        if q.strip().lower() == question.lower():
            return str(a)

    def _offline() -> str:
        # Offline fallback: loose keyword lookup over stored Q&A.
        ql = question.lower()
        for q, a in profile.additional.items():
            if any(w in q.lower() for w in ql.split() if len(w) > 3):
                return str(a)
        return "NOT IN PROFILE"

    if llm is None:
        return _offline()

    user = f"PROFILE:\n{profile.to_text_block()}\n\nAPPLICATION QUESTION:\n{question}"
    try:
        answer = llm.complete(_ANSWER_SYSTEM, user, temperature=0.2, max_tokens=400).strip()
    except Exception as e:  # noqa: BLE001 - degrade to stored data instead of crashing
        print(f"  [warn] LLM unavailable ({e}); answering from stored data only.")
        return _offline()
    if save and answer and answer.upper() != "NOT IN PROFILE":
        profile.additional[question] = answer
        save_profile(profile)
    return answer


# ----------------------------------------------------------------------------
# Interactive intake (one-time data entry)
# ----------------------------------------------------------------------------


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return val or default


def _ask_bool(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    val = _ask(f"{prompt} ({d})").lower()
    if not val:
        return default
    return val.startswith("y")


def _ask_list(prompt: str, default: List[str] | None = None) -> List[str]:
    default = default or []
    raw = _ask(prompt + " (comma-separated)", ", ".join(default))
    return [x.strip() for x in raw.split(",") if x.strip()]


def _ask_int(prompt: str, default: Optional[int]) -> Optional[int]:
    raw = _ask(prompt, "" if default is None else str(default))
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return default


def run_intake(existing: Optional[Profile] = None) -> Profile:
    """Interactive, resumable data entry. Blank answers keep existing values."""
    p = existing or Profile()
    print("\n=== Job Agent profile setup ===")
    print("Enter your details once. Press Enter to keep the shown value / skip a field.\n")

    p.full_name = _ask("Full legal name", p.full_name)
    p.preferred_name = _ask("Preferred name", p.preferred_name)
    p.headline = _ask("Professional headline (e.g. 'Backend Engineer')", p.headline)
    p.summary = _ask("Professional summary / bio", p.summary)
    p.date_of_birth = _ask("Date of birth (optional, YYYY-MM-DD)", p.date_of_birth)

    print("\n-- Contact --")
    c = p.contact
    c.email = _ask("Email", c.email)
    c.phone = _ask("Phone", c.phone)
    c.address_line1 = _ask("Address line 1", c.address_line1)
    c.address_line2 = _ask("Address line 2", c.address_line2)
    c.city = _ask("City", c.city)
    c.state = _ask("State/Region", c.state)
    c.postal_code = _ask("Postal code", c.postal_code)
    c.country = _ask("Country", c.country)

    print("\n-- Citizenship & work authorization --")
    p.nationalities = _ask_list("Nationalities", p.nationalities)
    if _ask_bool("Add/replace work authorization entries?", not p.work_authorizations):
        p.work_authorizations = []
        while True:
            country = _ask("  Country for this authorization (blank to stop)")
            if not country:
                break
            wa = WorkAuthorization(country=country)
            wa.status = _ask("  Status (Citizen / Permanent Resident / visa type / Requires sponsorship)")
            wa.requires_sponsorship = _ask_bool("  Requires sponsorship in this country?", False)
            wa.notes = _ask("  Notes")
            p.work_authorizations.append(wa)

    print("\n-- Skills & languages --")
    p.skills = _ask_list("Skills", p.skills)
    if _ask_bool("Add/replace languages?", not p.languages):
        p.languages = []
        while True:
            lang = _ask("  Language (blank to stop)")
            if not lang:
                break
            prof = _ask("  Proficiency (Native/Fluent/Professional/Conversational/Basic)")
            p.languages.append(Language(language=lang, proficiency=prof))

    print("\n-- Education --")
    if _ask_bool("Add/replace education?", not p.education):
        p.education = []
        while True:
            inst = _ask("  Institution (blank to stop)")
            if not inst:
                break
            ed = Education(institution=inst)
            ed.degree = _ask("  Degree (e.g. BSc, MSc)")
            ed.field_of_study = _ask("  Field of study")
            ed.start_date = _ask("  Start (YYYY or YYYY-MM)")
            ed.end_date = _ask("  End (YYYY or 'Present')")
            ed.gpa = _ask("  GPA")
            ed.location = _ask("  Location")
            ed.honors = _ask("  Honors/awards")
            p.education.append(ed)

    print("\n-- Work experience --")
    if _ask_bool("Add/replace experience?", not p.experience):
        p.experience = []
        while True:
            company = _ask("  Company (blank to stop)")
            if not company:
                break
            ex = Experience(company=company)
            ex.title = _ask("  Title")
            ex.start_date = _ask("  Start (YYYY-MM)")
            ex.end_date = _ask("  End (YYYY-MM or 'Present')")
            ex.location = _ask("  Location")
            ex.employment_type = _ask("  Type (Full-time/Internship/Contract)")
            ex.description = _ask("  One-line description")
            ex.achievements = _ask_list("  Key achievements", [])
            ex.tech = _ask_list("  Technologies used", [])
            p.experience.append(ex)

    print("\n-- Projects --")
    if _ask_bool("Add/replace projects?", not p.projects):
        p.projects = []
        while True:
            name = _ask("  Project name (blank to stop)")
            if not name:
                break
            pr = Project(name=name)
            pr.description = _ask("  Description")
            pr.tech = _ask_list("  Tech", [])
            pr.link = _ask("  Live link")
            pr.repo = _ask("  Repo link")
            pr.role = _ask("  Your role")
            pr.highlights = _ask_list("  Highlights", [])
            p.projects.append(pr)

    print("\n-- Links --")
    p.links.github = _ask("GitHub", p.links.github)
    p.links.linkedin = _ask("LinkedIn", p.links.linkedin)
    p.links.portfolio = _ask("Portfolio", p.links.portfolio)
    p.links.website = _ask("Website", p.links.website)

    print("\n-- Preferences --")
    pr = p.preferences
    pr.desired_titles = _ask_list("Desired job titles", pr.desired_titles)
    pr.desired_locations = _ask_list("Desired locations", pr.desired_locations)
    pr.remote_ok = _ask_bool("Open to remote?", pr.remote_ok)
    pr.onsite_ok = _ask_bool("Open to onsite?", pr.onsite_ok)
    pr.open_to_relocation = _ask_bool("Open to relocation?", pr.open_to_relocation)
    pr.salary_currency = _ask("Salary currency", pr.salary_currency)
    pr.salary_min = _ask_int("Minimum salary", pr.salary_min)
    pr.salary_max = _ask_int("Maximum/target salary", pr.salary_max)
    pr.availability = _ask("Availability / notice period", pr.availability)
    pr.earliest_start_date = _ask("Earliest start date", pr.earliest_start_date)
    pr.desired_employment_types = _ask_list("Desired employment types", pr.desired_employment_types)

    if _ask_bool("\nAdd voluntary EEO / self-identification info (optional)?", False):
        d = p.demographics
        d.gender = _ask("  Gender", d.gender)
        d.pronouns = _ask("  Pronouns", d.pronouns)
        d.race_ethnicity = _ask("  Race/ethnicity", d.race_ethnicity)
        d.veteran_status = _ask("  Veteran status", d.veteran_status)
        d.disability_status = _ask("  Disability status", d.disability_status)

    if _ask_bool("\nAdd any custom question/answer pairs (for unusual application fields)?", False):
        while True:
            q = _ask("  Question (blank to stop)")
            if not q:
                break
            a = _ask("  Answer")
            p.additional[q] = a

    return p
