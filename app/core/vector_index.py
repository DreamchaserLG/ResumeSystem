"""Versioned inputs shared by the local encoder and the recruitment API."""
from dataclasses import dataclass
import hashlib
import re

from app.core.extraction import masked, norm

MODEL_VERSION = "bge-m3-b5e0ce3470ab-21106b6d7dab-cls-l2-512"
SLICE_VERSION = "experience-v2"
DEFINITION_VERSION = "jd-v4"
MAX_TOKENS = 512
OVERLAP_TOKENS = 51
EVIDENCE_KINDS = ("work", "internship", "project", "campus_project")
DEGREES = ("high_school", "associate", "bachelor", "master", "phd")
FIELD_LABELS = {
    "work.title": "岗位", "work.description": "职责与成果",
    "project.project_name": "项目", "project.role": "角色", "project.description": "项目说明",
}
KIND_LABELS = dict(zip(EVIDENCE_KINDS, ("工作经历", "实习经历", "项目经历", "校园项目")))
CAMPUS = re.compile(r"校园项目|毕业设计|毕业论文|课程设计|课程项目|校内项目|\bcapstone\b|\bcourse project\b", re.I)
BOUNDARY = re.compile(r"[^\n。！？；]+[。！？；]*")
JD_BREAK = re.compile(r"[\n；;。]+|\s*(?<=\s)[0-9]{1,2}[、.)）]\s*")
HEADING = re.compile(r"^(岗位职责|主要职责|工作职责|职责描述|任职要求|任职资格|岗位要求|招聘要求)\s*[：:]?\s*", re.I)
UNCONFIRMED_GATE = re.compile(r"(?:(?:大专|专科|本科|学士|硕士|博士|学历).{0,8}(?:以上|及以上|学历)|"
                              r"(?:至少|不低于)\s*\d+\s*年|\d+\s*年(?:以上|及以上|工作经验)|"
                              r"\b(?:bachelor|master|phd)\b.{0,12}\b(?:degree|required)\b)", re.I)
ALTERNATIVE = re.compile(r"\s*(?:或者|或|\bor\b)\s*|\s+[／/]\s+", re.I)
OPTIONAL_SKILL = re.compile(r"^(?:加分项|优先项|加分)\s*[：:]\s*|\s*[（(](?:加分|优先)[)）]$")
NUMBERING = re.compile(r"^\s*(?:[•●▪·*-]|\(?[0-9一二三四五六七八九十]{1,3}[、.)）])\s*")


@dataclass(frozen=True)
class Fragment:
    part_no: int
    span_no: int
    field_key: str
    start: int
    value: str
    label: str = ""

    def cut(self, start, end):
        return Fragment(self.part_no, self.span_no, self.field_key,
                        self.start + start, self.value[start:end], self.label)

    @property
    def rendered(self):
        return self.label + self.value


@dataclass(frozen=True)
class Chunk:
    kind: str
    fragments: tuple[Fragment, ...]
    body: str
    token_count: int


@dataclass(frozen=True)
class Requirement:
    kind: str
    original: str
    mandatory: bool = False
    weight: float = 1
    scopes: tuple[str, ...] = EVIDENCE_KINDS
    terms: tuple[str, ...] = ()
    minimum: int | None = None
    query: str | None = None


def clean_source(value, names):
    value = masked(value)
    for name in names:
        if len(name) >= 2:
            value = re.sub(re.escape(name), "█" * len(name), value, flags=re.I)
    return value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def evidence_kind(rows):
    first = rows[0]
    if first["kind"] == "work":
        return "internship" if first["employment_type"] == "internship" else "work"
    return "campus_project" if any(CAMPUS.search(row["value"]) for row in rows) else "project"


def _split_long(tokenizer, fragment, limit):
    cursor = 0
    while cursor < len(fragment.value):
        suffix = fragment.value[cursor:]
        offsets = tokenizer(suffix, add_special_tokens=False, return_offsets_mapping=True,
                            verbose=False)["offset_mapping"]
        if not offsets:
            break
        end = len(suffix) if len(offsets) <= limit else offsets[limit - 1][1]
        piece = fragment.cut(cursor, cursor + end)
        yield piece
        if len(offsets) <= limit:
            break
        cursor += max(1, offsets[limit - OVERLAP_TOKENS][0])


def make_chunks(tokenizer, rows):
    kind = evidence_kind(rows)
    prefix = KIND_LABELS[kind] + "\n"

    def body(parts):
        return prefix + "\n".join(part.rendered for part in parts)

    def size(value):
        return len(tokenizer(value, add_special_tokens=True, verbose=False)["input_ids"])

    def chunk(parts):
        rendered = body(parts)
        count = size(rendered)
        if count > MAX_TOKENS:
            raise ValueError(f"chunk exceeded token budget ({count})")
        return Chunk(kind, tuple(parts), rendered, count)

    def units():
        for row in rows:
            fragment = Fragment(row["part_no"], row["span_no"], row["field_key"],
                                row["start_offset"], row["value"],
                                FIELD_LABELS[row["field_key"]] + "：" if row["span_kind"] == "table" else "")
            if size(body([fragment])) <= MAX_TOKENS:
                yield fragment, False
                continue
            sentences = list(BOUNDARY.finditer(fragment.value))
            for match in sentences or [re.fullmatch(r"[\s\S]+", fragment.value)]:
                if match is None:
                    continue
                piece = fragment.cut(match.start(), match.end())
                if size(body([piece])) <= MAX_TOKENS:
                    yield piece, False
                    continue
                # A small margin covers tokenization changes at a sliced edge.
                budget = MAX_TOKENS - size(prefix + fragment.label) - 8
                for window in _split_long(tokenizer, piece, budget):
                    yield window, True

    pending = []
    for part, forced in units():
        if pending and (forced or size(body([*pending, part])) > MAX_TOKENS):
            yield chunk(pending)
            pending = []
        if forced:
            yield chunk([part])
        else:
            pending.append(part)
    if pending:
        yield chunk(pending)


def clause_key(value):
    return norm(value.strip().rstrip("。；;"))


def role_terms(title):
    title = re.split(r"[（(]", title, maxsplit=1)[0]
    return tuple(dict.fromkeys(norm(part) for part in re.split(r"[／/]", title) if norm(part)))


def job_requirements(snapshot, rules):
    if rules:
        return "reviewed", [
            Requirement(item["kind"], item["text"], item["mandatory"], item["weight"],
                        tuple(item["scopes"]), tuple(item["terms"]), item["minimum"],
                        item["text"] if item["kind"] == "duty" else None)
            for item in rules["items"]
        ]

    reference = snapshot.get("reference")
    scopes = tuple(reference["scopes"]) if reference else EVIDENCE_KINDS
    keywords = reference["keywords"] if reference else {}
    classified = []
    seen = set()
    for kind, block in (("duty", snapshot["jd"]), ("qualification", snapshot["requirements"])):
        clauses = (block or "").splitlines() if reference else JD_BREAK.split(block or "")
        for clause in clauses:
            source = NUMBERING.sub("", clause.strip()).strip()
            content = HEADING.sub("", source).strip()
            key = clause_key(content)
            if not key or (kind, key) in seen:
                continue
            seen.add((kind, key))
            if UNCONFIRMED_GATE.search(content) or re.search(r"由.{0,10}核验", content):
                classified.append(Requirement("qualification_check", source, scopes=()))
                continue
            terms = tuple(keywords.get(key) or [key])
            classified.append(Requirement(kind, source, scopes=scopes, terms=terms, query=content))
    classified = classified[:40]
    skills = []
    for source in dict.fromkeys(snapshot["required_skills"]):
        content, markers = OPTIONAL_SKILL.subn("", source)
        label = norm(content)
        aliases = reference.get("skill_terms", {}).get(label, []) if reference else []
        terms = tuple(dict.fromkeys(norm(term) for term in [*ALTERNATIVE.split(content), *aliases] if norm(term)))
        if terms:
            skill_scopes = scopes if aliases else ()
            skills.append(Requirement("skill", source, not markers, 1 if markers else 2, skill_scopes, terms))
    title = snapshot["title"].strip()
    role = Requirement("role", title, terms=role_terms(title), query=title)
    if classified:
        if not any(item.kind == "duty" for item in classified):
            classified.insert(0, role)
        return ("reference" if reference else "jd"), classified + skills
    return ("jd" if snapshot["jd"] or snapshot["requirements"] or skills else "title"), [role, *skills]
