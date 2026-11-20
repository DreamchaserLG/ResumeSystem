"""Shared vocabulary for evidence-based job ranking."""
import re

from sqlalchemy import text

from app.core.extraction import norm


def catalog_for(session):
    rows = session.execute(text("""
        SELECT s.id,s.name,coalesce(array_agg(a.alias_normalized) FILTER (WHERE a.alias_normalized IS NOT NULL),'{}') AS aliases
        FROM skill s LEFT JOIN skill_alias a ON a.skill_id=s.id GROUP BY s.id,s.name ORDER BY s.name
    """)).all()
    return {row.name:(row.id,{norm(row.name),*(norm(x) for x in row.aliases)}) for row in rows}


def appears(term, document):
    if not term:
        return False
    if term.isascii():
        return re.search(r"(?<![a-z0-9_])"+re.escape(term)+r"(?![a-z0-9_])",document) is not None
    return term in document


def recognize_job_skills(catalog, reference):
    document = norm(reference)
    return [name for name, (_,aliases) in catalog.items() if any(appears(alias,document) for alias in aliases)]


def job_skill_requirements(catalog, job):
    reference = "、".join(job["required_skills"]) or " ".join((job["requirements"], job["jd"] or ""))
    return {name: catalog[name][0] for name in recognize_job_skills(catalog, reference)[:20]}
