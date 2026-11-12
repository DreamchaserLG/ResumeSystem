"""Deterministic field extraction from M3 spans with version-local evidence."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
import re
import unicodedata

RULE_VERSION = "rules-v1"
SENSITIVE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)|"
    r"(?:身份证(?:号|号码)?|证件号码|婚姻状况|婚育(?:情况|状况)?|生育情况|性别|民族|户籍|出生日期|联系电话|联系地址|详细地址|家庭住址|通讯地址|住址|地址|电话|手机|邮箱|"
    r"marital status|passport(?: no\.?)?|gender|date of birth|home address|address|phone|email|sex)\s*[:：][^|；;\n]*",
    re.I,
)
CONTACT = {
    "email": re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I),
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
}
SECTION = {
    **dict.fromkeys(("基本信息", "个人信息", "profile", "personal information", "contact"), "profile"),
    **dict.fromkeys(("教育经历", "教育背景", "教育", "education", "academic background"), "education"),
    **dict.fromkeys(("工作经历", "工作经验", "职业经历", "work experience", "experience", "employment", "professional experience"), "work"),
    **dict.fromkeys(("实习经历", "实习经验", "internships", "internship"), "internship"),
    **dict.fromkeys(("项目经历", "项目经验", "项目", "projects", "project experience"), "project"),
    **dict.fromkeys(("技能", "专业技能", "技术栈", "skills", "technical skills"), "skill"),
    **dict.fromkeys(("求职意向", "个人总结", "自我评价", "summary", "objective"), "summary"),
    **dict.fromkeys(("补充信息", "其他", "证书", "additional information", "certifications"), "other"),
}
LABELS = {
    "求职岗位": "profile.target_role", "应聘岗位": "profile.target_role", "期望职位": "profile.target_role", "应聘职位": "profile.target_role",
    "目标岗位": "profile.target_role", "desired position": "profile.target_role",
    "target role": "profile.target_role", "姓名": "profile.name", "name": "profile.name", "现居地": "profile.city", "城市": "profile.city",
    "location": "profile.city", "city": "profile.city", "学校": "education.school_raw",
    "院校": "education.school_raw", "school": "education.school_raw", "institution": "education.school_raw",
    "学历": "education.degree_level", "学位": "education.degree_level", "degree": "education.degree_level",
    "专业": "education.major", "major": "education.major", "公司": "work.company", "company": "work.company",
    "雇主": "work.company", "岗位": "work.title", "职位": "work.title", "position": "work.title", "title": "work.title",
    "工作性质": "work.employment_type", "项目名称": "project.project_name", "项目": "project.project_name",
    "project": "project.project_name", "角色": "project.role", "担任角色": "project.role", "role": "project.role",
    "个人总结": "profile.summary", "职责": "work.description", "描述": "work.description", "成果": "work.description",
    "description": "work.description", "时间": "period", "起止时间": "period", "dates": "period",
    "技能": "skill.name", "skills": "skill.name", "技术栈": "skill.name",
}
DEGREES = {
    "phd": r"博士|\bph\.?d\b|doctorate", "master": r"硕士|\bmaster\b|\bmba\b",
    "bachelor": r"本科|学士|\bbachelor\b", "associate": r"大专|专科|\bassociate\b",
    "high_school": r"高中|high school", "other": r"^其他$|^other$",
}
DATE = r"(?:19|20)\d{2}(?:(?:[./年-](?:0?[1-9]|1[0-2]))(?:[./月-](?:0?[1-9]|[12]\d|3[01])日?|月)?)?(?!\d)"
PERIOD = re.compile(rf"({DATE})\s*(?:[-–—~～]|\bto\b|至|到)\s*({DATE}|至今|今|现在|present|current)", re.I)
PAIR = re.compile(r"^\s*([^:：|]{1,25})[:：]\s*(.+?)\s*$")
PART = re.compile(r"[^|；;\n]+")
ALLOWED_LABEL = re.compile(r"^[\w\u3400-\u9fff ._-]{1,25}$")
ORG = re.compile(r"有限公司|集团|研究院|\b(?:inc\.?|corp\.?|ltd\.?)\b", re.I)
SCHOOL = re.compile(r"大学|学院|\b(?:university|college|institute)\b", re.I)


def norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def masked(value: str) -> str:
    # Preserve original Unicode offsets used by evidence links.
    return SENSITIVE.sub(lambda match: "█" * len(match[0]), value)


PRIVATE_LABELS = {
    **dict.fromkeys(("性别", "gender", "sex"), "gender"),
    **dict.fromkeys(("地址", "详细地址", "联系地址", "家庭住址", "通讯地址", "住址", "home address", "address"), "address"),
}
GENDERS = {"男": "male", "女": "female", "male": "male", "female": "female", "nonbinary": "other", "非二元": "other"}


def private_card_fields(document, allowed):
    values = {}
    for span in document.spans:
        if span.is_sensitive or (span.ocr_confidence is not None and span.ocr_confidence < .90):
            continue
        for fragment in PART.findall(span.raw_text or ""):
            match = PAIR.fullmatch(fragment)
            if not match:
                continue
            kind = PRIVATE_LABELS.get(norm(match[1]))
            if kind not in allowed:
                continue
            raw = match[2].strip()
            value = GENDERS.get(norm(raw)) if kind == "gender" else raw if len(raw) <= 250 and masked(raw) == raw else None
            if value:
                values.setdefault(kind, set()).add(value)
    return {kind: next(iter(matches)) for kind, matches in values.items() if len(matches) == 1}


def parse_period(value: str):
    hit = PERIOD.search(norm(value))
    if not hit:
        return None
    start, end = hit.groups()
    current = end in {"至今", "今", "现在", "present", "current"}

    def calendar(raw):
        parts = [int(item) for item in re.findall(r"\d+", raw)]
        if len(parts) < 2:
            return None
        return date(parts[0], parts[1], parts[2] if len(parts) == 3 else 1)

    try:
        first, last = calendar(start), None if current else calendar(end)
    except ValueError:
        return None
    if first and last and first > last or not current and int(start[:4]) > int(end[:4]):
        return None
    return {"start_period_raw": start, "end_period_raw": end, "start_date": first, "end_date": last, "is_current": current}


def degree_value(value: str) -> str | None:
    value = norm(value)
    return next((key for key, pattern in DEGREES.items() if key == value or re.search(pattern, value)), None)


@dataclass(frozen=True)
class Source:
    span: int
    start: int
    end: int
    value: str
    confidence: float | None


@dataclass
class Record:
    kind: str
    fields: dict = field(default_factory=dict)
    sources: dict[str, list[Source]] = field(default_factory=lambda: defaultdict(list))
    issues: dict[str, tuple[str, int | None]] = field(default_factory=dict)
    parent: "Record | None" = None

    def add(self, key, value, source):
        if source.confidence is not None and source.confidence < .90:
            self.issues[key] = ("low_confidence", source.span)
            if source.confidence < .75:
                return
        if key in self.fields and self.fields[key] != value and key not in {"work.description", "project.description", "profile.summary"}:
            self.issues[key] = ("conflict", source.span)
            self.sources[key].append(source)
            return
        self.fields[key] = value
        self.sources[key].append(source)


def _source(span, start, end):
    return Source(span.span_no, start, end, span.raw_text[start:end], span.ocr_confidence)


def _line_parts(span):
    for line in PART.finditer(span.raw_text or ""):
        raw = line[0].strip()
        if raw and raw.strip("█ "):
            start = line.start() + len(line[0]) - len(line[0].lstrip())
            yield _source(span, start, start + len(raw))


def _segment_line(span, line, match, group):
    return _source(span, line.start + match.start(group), line.start + match.end(group))


def extract_document(document, aliases, skills, schools):
    """Return ordered records. Span numbering is inherited from the parser."""
    profile = Record("profile")
    records = [profile]
    current = profile
    section = "profile"
    seen_skills = set()
    skill_patterns = [
        (skill_id, re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", re.I))
        for alias, skill_id in sorted(skills.items(), key=lambda entry: -len(entry[0])) if alias
    ]
    for span in document.spans:
        if span.span_kind == "photo":
            profile.add("profile.photo_present", True, Source(span.span_no, 0, 0, "", None))
            continue
        raw = span.raw_text or ""
        for key, pattern in CONTACT.items():
            if span.is_sensitive:
                continue
            for hit in pattern.finditer(raw):
                source = _source(span, hit.start(), hit.end())
                contact = Record("contact")
                contact.add(f"contact.{key}", hit[0], source)
                records.append(contact)
        safe = masked(raw)
        span.raw_text = safe if span.raw_text is not None else None
        if span.is_sensitive or not safe.strip("█ \n\t"):
            span.is_sensitive = True
            continue
        for line in _line_parts(span):
            text = line.value
            heading = SECTION.get(norm(text.rstrip("：:")))
            if heading:
                section = heading
                current = profile if section == "profile" else Record("work" if section == "internship" else section)
                if current is not profile:
                    records.append(current)
                continue
            if "█" in text:
                continue
            pair = PAIR.fullmatch(text)
            key = (aliases.get(norm(pair[1])) or LABELS.get(norm(pair[1]))) if pair else None
            source = _segment_line(span, line, pair, 2) if pair else line
            value = source.value
            if pair and norm(pair[1]) in {"求职意向", "期望职业", "期望岗位"} and len(value) <= 50 and not re.search(r"[。!?！？]", value):
                key = "profile.target_role"
            period = parse_period(value) if section in {"education", "work", "internship", "project"} or key == "period" else None
            if key is None and not pair and section == "education":
                if norm(value) in schools or SCHOOL.search(value):
                    key = "education.school_raw"
                elif degree_value(value):
                    key = "education.degree_level"
            if key is None and not pair and section in {"work", "internship"} and not period and ORG.search(value):
                key = "work.company"
            if key is None and not pair and section == "project" and not current.fields and not period:
                key = "project.project_name"
            if key and key.startswith(("work.", "education.", "project.")):
                expected = key.split(".")[0]
                if current.kind != expected:
                    current = Record(expected)
                    records.append(current)
                    section = expected
                if key in {"work.company", "education.school_raw", "project.project_name"} and key in current.fields and current.fields[key] != value:
                    current = Record(expected)
                    records.append(current)
            if period and current.kind in {"work", "project", "education"}:
                period_key = f"{current.kind}.period"
                if period_key in current.fields and current.fields[period_key] != period:
                    current = Record(current.kind)
                    records.append(current)
                hit = PERIOD.search(text)
                evidence = _segment_line(span, line, hit, 0) if hit else source
                current.add(period_key, period, evidence)
                if section == "internship":
                    current.add("work.employment_type", "internship", evidence)
                if key in (None, "period"):
                    tail = text[hit.end():].strip(" –—-|") if hit else ""
                    if tail and current.kind in {"work", "education"}:
                        head = re.split(r"\s{2,}|\s+[·|]\s+", tail, maxsplit=1)[0]
                        at = text.rfind(head)
                        if current.kind == "work" and ORG.search(head):
                            current.add("work.company", head, _source(span, line.start + at, line.start + at + len(head)))
                        if current.kind == "education" and (SCHOOL.search(head) or norm(head) in schools):
                            current.add("education.school_raw", head, _source(span, line.start + at, line.start + at + len(head)))
                    continue
            if key == "period":
                current.issues[f"{current.kind}.period"] = ("pending_review", source.span)
                continue
            if key == "education.degree_level":
                value = degree_value(value)
                if value is None:
                    current.issues[key] = ("pending_review", source.span)
                    continue
            if key == "work.employment_type":
                value = next((target for raw_type, target in {
                    "实习": "internship", "全职": "full_time", "兼职": "part_time", "合同": "contract",
                    "intern": "internship", "full-time": "full_time", "part-time": "part_time",
                }.items() if raw_type in norm(value)), None)
                if value is None:
                    current.issues[key] = ("pending_review", source.span)
                    continue
            if key and key.startswith("profile."):
                profile.add(key, value, source)
            elif key and key in LABELS.values() and key not in {"skill.name", "period"}:
                current.add(key, value, source)
            elif pair and key not in LABELS.values() and ALLOWED_LABEL.fullmatch(pair[1].strip()):
                extra = Record("extension")
                label = pair[1].strip()
                inferred = "quantity" if re.fullmatch(r"\d+(?:\.\d+)?\s*[\w¥￥]+", value) else "unknown"
                extra.fields["extension"] = (key, label, {"raw": value, "type": inferred, "confidence": source.confidence, "extractor": RULE_VERSION})
                extra.sources["extension"].append(source)
                records.append(extra)
                continue
            elif not pair and current.kind in {"work", "project", "summary"}:
                key = "profile.summary" if current.kind == "summary" else f"{current.kind}.description"
                current.add(key, value, source)
            elif not pair and span.span_kind == "heading" and current is profile and not profile.fields.get("profile.name") and re.fullmatch(r"[\u3400-\u9fff]{2,4}|[A-Z][a-z]+(?: [A-Z][a-z]+){1,3}", value):
                profile.add("profile.name", value, source)
            elif not pair and current.kind == "other":
                current.issues["source.unassigned"] = ("pending_review", source.span)
                current.sources["source.unassigned"].append(source)
            if current.kind in {"work", "project", "skill"} or key == "skill.name":
                parent = current if current.kind in {"work", "project"} else None
                for skill_id, pattern in skill_patterns:
                    match = pattern.search(value)
                    identity = id(parent), skill_id
                    if not match or identity in seen_skills:
                        continue
                    skill_source = _source(span, source.start + match.start(), source.start + match.end())
                    skill = Record("skill", parent=parent)
                    skill.add("skill.name", skill_id, skill_source)
                    if "skill.name" in skill.fields:
                        seen_skills.add(identity)
                    records.append(skill)
    for record in records:
        if "education.school_raw" in record.fields:
            matches = schools.get(norm(record.fields["education.school_raw"]), ())
            if len(matches) == 1:
                record.fields["school_id"] = next(iter(matches))
            elif len(matches) > 1:
                record.issues["education.school_raw"] = ("ambiguous_school", record.sources["education.school_raw"][0].span)
        if record.kind in {"work", "project", "education"}:
            required = {"work": ("work.company", "work.period"), "project": ("project.project_name",),
                        "education": ("education.school_raw", "education.degree_level")}[record.kind]
            if record.fields or record.issues:
                for key in required:
                    if key not in record.fields and key not in record.issues:
                        record.issues[key] = ("not_found", None)
    return [record for record in records if record.fields or record.issues]
