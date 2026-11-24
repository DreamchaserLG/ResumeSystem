"""Import sourced job-description summaries into existing department jobs."""
import argparse
from datetime import date
import json
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.extraction import norm
from app.core.vector_index import EVIDENCE_KINDS, clause_key
from migrations.migrate import get_dsn


def copy_sections(job):
    keywords = {}
    sections = []
    for key in ("duties", "requirements"):
        entries = job[key]
        if not entries or any(not entry["text"].strip() for entry in entries):
            raise ValueError(f"missing {key} for {job['title']}")
        section = "\n".join(entry["text"].strip() for entry in entries)
        if len(section) > 4000:
            raise ValueError(f"{key} exceeded the field limit for {job['title']}")
        for entry in entries:
            keywords[clause_key(entry["text"])] = list(dict.fromkeys(
                norm(term) for term in entry["keywords"] if norm(term)
            ))
        sections.append(section)
    return sections[0], sections[1], keywords


def load_references(connection, path):
    data = json.loads(path.read_text())
    researched = date.fromisoformat(data["researched_on"])
    window = data.get("reference_recruitment_window")
    start, end = (date.fromisoformat(window[k]) for k in ("start", "end")) if window else (None, None)
    if start and start > end:
        raise ValueError("reference recruitment start must be on or before the end")
    seen = set()
    for job in data["jobs"]:
        title, department = job["title"], job["department"]
        key = department, title
        if key in seen:
            raise ValueError(f"duplicate import entry: {department} / {title}")
        seen.add(key)
        category, scopes, sources = job["category"], job["scopes"], job["sources"]
        if not category.strip() or not scopes or not set(scopes) <= set(EVIDENCE_KINDS):
            raise ValueError(f"invalid category or scope for {title}")
        if not sources or any(urlsplit(source["url"]).scheme != "https" or not urlsplit(source["url"]).hostname
                              or source["match"] not in ("affiliated", "nearby", "market")
                              or not source["posting"].strip() or not source["platform"].strip() for source in sources):
            raise ValueError(f"invalid source attribution for {title}")
        jd, requirements, keywords = copy_sections(job)
        skills = job.get("required_skills", {})
        experience = job.get("experience_tags", [])
        if len(skills) > 20 or len(experience) > 12 or any(not norm(tag) for tag in [*skills, *experience]):
            raise ValueError(f"invalid skill or experience tags for {title}")
        skill_terms = {norm(label): list(dict.fromkeys(norm(term) for term in terms if norm(term)))
                       for label, terms in skills.items()}
        rows = connection.execute("""
            SELECT j.id,j.jd,j.requirements,j.recruitment_start,j.recruitment_end,j.required_skills,
                   r.jd_snapshot,r.requirements_snapshot,r.reference_recruitment_start,r.reference_recruitment_end
            FROM job j JOIN department d ON d.id=j.department_id
            LEFT JOIN job_posting_reference r ON r.job_id=j.id
            WHERE d.name=%s AND j.title=%s FOR UPDATE OF j
        """, (department, title)).fetchall()
        if len(rows) != 1:
            raise ValueError(f"expected exactly one existing job: {department} / {title}")
        row = rows[0]
        already_seeded = (row["jd"], row["requirements"]) == (jd, requirements)
        previous_seed = (row["jd"], row["requirements"]) == (row["jd_snapshot"], row["requirements_snapshot"])
        empty = not (row["jd"] or "").strip() and not row["requirements"].strip()
        if not (already_seeded or previous_seed or empty):
            raise ValueError(f"existing job text differs from the import snapshot: {department} / {title}")
        if not already_seeded:
            connection.execute("UPDATE job SET jd=%s,requirements=%s,updated_at=now() WHERE id=%s",
                               (jd, requirements, row["id"]))
        connection.execute("""
            UPDATE job SET required_skills=CASE WHEN cardinality(required_skills)=0 THEN %s ELSE required_skills END,
                experience_tags=CASE WHEN cardinality(experience_tags)=0 THEN %s ELSE experience_tags END,
                recruitment_start=CASE WHEN recruitment_start IS NULL AND recruitment_end IS NULL THEN %s ELSE recruitment_start END,
                recruitment_end=CASE WHEN recruitment_start IS NULL AND recruitment_end IS NULL THEN %s ELSE recruitment_end END,
                updated_at=now()
            WHERE id=%s AND ((cardinality(required_skills)=0 AND cardinality(%s::text[])>0)
                OR (cardinality(experience_tags)=0 AND cardinality(%s::text[])>0)
                OR (recruitment_start IS NULL AND recruitment_end IS NULL AND %s::date IS NOT NULL))
        """, (list(skills), experience, start, end, row["id"], list(skills), experience, start))
        supplied_window = start and row["recruitment_start"] is None and row["recruitment_end"] is None
        reference_start = start if supplied_window else row["reference_recruitment_start"]
        reference_end = end if supplied_window else row["reference_recruitment_end"]
        connection.execute("""
            INSERT INTO job_posting_reference(job_id,category,sources,jd_snapshot,requirements_snapshot,
                                              evidence_scopes,keyword_groups,researched_on,skill_terms,
                                              reference_recruitment_start,reference_recruitment_end)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (job_id) DO UPDATE SET category=excluded.category,sources=excluded.sources,
                jd_snapshot=excluded.jd_snapshot,requirements_snapshot=excluded.requirements_snapshot,
                evidence_scopes=excluded.evidence_scopes,keyword_groups=excluded.keyword_groups,
                researched_on=excluded.researched_on,skill_terms=excluded.skill_terms,
                reference_recruitment_start=excluded.reference_recruitment_start,
                reference_recruitment_end=excluded.reference_recruitment_end
        """, (row["id"], category, Jsonb(sources), jd, requirements, scopes, Jsonb(keywords), researched,
              Jsonb(skill_terms), reference_start, reference_end))
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("references", type=Path)
    args = parser.parse_args()
    with psycopg.connect(get_dsn(), row_factory=dict_row) as connection:
        count = load_references(connection, args.references)
    print(f"job_references_imported={count}")


if __name__ == "__main__":
    main()
