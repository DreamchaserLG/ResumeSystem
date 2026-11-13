"""Deterministic field extraction from M3 spans with version-local evidence."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
import re
import unicodedata

RULE_VERSION = "rules-v3"
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
    **dict.fromkeys(("项目经历", "项目经验", "项目实践", "项目", "projects", "project experience"), "project"),
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
# Headings indicate where to look for skills; they do not assign capabilities by themselves.
SKILL_HEADERS = set(map(str.casefold, (
    "技能", "技术栈", "专业技能", "核心能力", "职业技能", "后端技能", "后端框架", "前端技能", "前端框架",
    "中间件", "数据库", "工具", "工程工具", "工程部署", "部署运维", "工具栈", "开发工具",
    "实验技术", "仪器平台", "人力资源技能", "skills", "technologies", "technical skills", "stack",
)))
LANGUAGE_HEADERS = {
    "编程语言", "主要编程语言", "主编程语言", "主开发语言", "主语言", "开发语言", "常用语言",
    "programming languages", "programming language", "primary language", "primary programming language",
}
PRIMARY_LANGUAGE = re.compile(r"主(?:要)?(?:编程|开发)?语言|主力|\b(?:primary|main)\b", re.I)

DEGREES = {
    "phd": r"博士|\bph\.?d\.?\b|doctorate", "master": r"硕士|\bmaster(?:['’]s)?\b|\b(?:mba|msc|meng|mphil|m\.s|m\.sc)\b",
    "bachelor": r"本科|学士|\bbachelor(?:['’]s)?\b|\b(?:bsc|beng)\b", "associate": r"大专|专科|\bassociate\b",
    "high_school": r"高中|high school", "other": r"^其他$|^other$",
}
DATE = r"(?:19|20)\d{2}(?:(?:[./年-](?:0?[1-9]|1[0-2]))(?:[./月-](?:0?[1-9]|[12]\d|3[01])日?|月)?)?(?!\d)"
PERIOD = re.compile(rf"({DATE})\s*(?:[-–—~～]|\bto\b|至|到)\s*({DATE}|至今|今|现在|present|current)", re.I)
PAIR = re.compile(r"^\s*([^:：|]{1,25})[:：]\s*(.+?)\s*$")
PART = re.compile(r"[^|；;\n]+")
ALLOWED_LABEL = re.compile(r"^[\w\u3400-\u9fff ._-]{1,25}$")
ORG = re.compile(r"有限公司|集团|研究院|\b(?:inc\.?|corp\.?|ltd\.?)\b", re.I)
SCHOOL = re.compile(r"大学|学院|\b(?:university|college|institute)\b", re.I)
HAN_NAME = re.compile(r"[\u3400-\u9fff](?:[^\S\n]*[\u3400-\u9fff]){1,3}")
STACKED_NAME = re.compile(r"[\u3400-\u9fff](?:[^\S\n]*\n[^\S\n]*[\u3400-\u9fff]){1,3}")
LATIN_NAME = re.compile(r"[A-Z][a-z]+(?: [A-Z][a-z]+){1,3}")


def norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def masked(value: str) -> str:
    # Preserve original Unicode offsets used by evidence links.
    return SENSITIVE.sub(lambda match: "█" * len(match[0]), value)


PRIVATE_LABELS = {
    **dict.fromkeys(("性别", "gender", "sex"), "gender"),
    **dict.fromkeys(("地址", "详细地址", "联系地址", "家庭住址", "通讯地址", "住址", "home address", "address"), "address"),
}
GENDERS = {"男": "male", "男性": "male", "女": "female", "女性": "female",
           "male": "male", "female": "female", "nonbinary": "other", "non-binary": "other", "非二元": "other"}
GENDER_LABEL = re.compile(r"(?<![\w])(?:gender|sex)\s*[:：]?\s*(male|female|non[ -]?binary)(?![a-z])|"
                          r"性别\s*[:：]?\s*(男性|女性|男|女|非二元)(?![\u3400-\u9fff])", re.I)


def private_card_fields(document, allowed):
    values = {}
    for span in document.spans:
        if span.is_sensitive or (span.ocr_confidence is not None and span.ocr_confidence < .90):
            continue
        if "gender" in allowed:
            for match in GENDER_LABEL.finditer(span.raw_text or ""):
                gender = GENDERS.get(norm(match[1] or match[2]).replace("non binary","nonbinary"))
                if gender:
                    values.setdefault("gender", set()).add(gender)
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
    heuristic: set[str] = field(default_factory=set)

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


def _education_header(value, schools):
    """Split school and degree when both are printed on one heading line."""
    if norm(value) in schools:
        return (0, len(value)), None
    for separator in re.finditer(r"[\s|·,，/—–]+|(?=[（(])", value):
        head = value[:separator.start()]
        tail_start = separator.end()
        tail = value[tail_start:]
        if not head or not tail:
            continue
        if norm(head) in schools or (SCHOOL.search(head) and re.search(r"(?:大学|学院)$", head)):
            if degree_value(tail) and len(tail) <= 50:
                return (0, len(head)), (tail_start, len(value))
    return None


def extract_document(document, aliases, skills, schools, programming_languages=frozenset()):
    """Return ordered records. Span numbering is inherited from the parser."""
    profile = Record("profile")
    records = [profile]
    current = profile
    section = "profile"
    seen_skills = {}
    # Prefer longer overlapping aliases (e.g. qPCR over PCR, C++ over C).
    skill_patterns = [
        (alias, skill_id, re.compile(r"(?<![a-z0-9_])" + re.escape(alias) + r"(?![a-z0-9_])", re.I))
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
        # Some PDF templates place one glyph per visual line in a single name heading.
        # Keep the whole source range as evidence and normalize only the stored name.
        heading_text = safe.strip()
        if (span.span_kind == "heading" and current is profile and "profile.name" not in profile.fields
                and "\n" in heading_text and span.locator.get("page", 1) == 1
                and STACKED_NAME.fullmatch(heading_text)):
            start = len(safe) - len(safe.lstrip())
            profile.add("profile.name", "".join(heading_text.split()), _source(span, start, start + len(heading_text)))
            profile.heuristic.add("profile.name")
            continue
        block_lines = list(_line_parts(span))
        # PDF text blocks can group columns of one row as separate short lines:
        # date range, project name and role. Recognize that shape before free text.
        if section == "project" and 2 <= len(block_lines) <= 3:
            date_line, name_line, *role_lines = block_lines
            date_value = parse_period(date_line.value)
            if (date_value and PERIOD.fullmatch(norm(date_line.value)) and len(name_line.value) <= 80
                    and not re.search(r"[:：。；█]", name_line.value)
                    and all(len(role.value) <= 30 and not re.search(r"[:：。；█]", role.value) for role in role_lines)):
                if current.kind != "project" or current.fields or current.sources:
                    current = Record("project")
                    records.append(current)
                current.add("project.period", date_value, date_line)
                current.add("project.project_name", name_line.value, name_line)
                if role_lines:
                    current.add("project.role", role_lines[0].value, role_lines[0])
                continue
        for line in block_lines:
            text = line.value
            normalized_heading = norm(text.rstrip("：:"))
            if normalized_heading in {"简历", "个人简历", "求职简历", "演示简历", "resume", "curriculum vitae", "cv"}:
                continue
            heading = SECTION.get(normalized_heading)
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
            heading_key = norm(pair[1]) if pair else ""
            language_line = heading_key in LANGUAGE_HEADERS
            if language_line or heading_key in SKILL_HEADERS or section == "skill" and key is None and pair:
                key = "skill.name"
            source = _segment_line(span, line, pair, 2) if pair else line
            value = source.value
            if pair and norm(pair[1]) in {"求职意向", "期望职业", "期望岗位"} and len(value) <= 50 and not re.search(r"[。!?！？]", value):
                key = "profile.target_role"
            period = parse_period(value) if section in {"education", "work", "internship", "project"} or key == "period" else None
            if section == "education" and not period and (key == "education.school_raw" or not pair):
                header = _education_header(value, schools)
                if header:
                    school_range, degree_range = header
                    school_src = _source(span, source.start + school_range[0], source.start + school_range[1])
                    if "education.school_raw" in current.fields and current.fields["education.school_raw"] != school_src.value:
                        current = Record("education")
                        records.append(current)
                    current.add("education.school_raw", school_src.value, school_src)
                    if degree_range:
                        degree_src = _source(span, source.start + degree_range[0], source.start + degree_range[1])
                        current.add("education.degree_level", degree_value(degree_src.value), degree_src)
                    continue
            if key is None and not pair and section == "education" and not period:
                if SCHOOL.search(value):
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
                        header = _education_header(tail, schools) if current.kind == "education" else None
                        if header:
                            at = text.rfind(tail)
                            school_range, degree_range = header
                            school_src = _source(span, line.start + at + school_range[0], line.start + at + school_range[1])
                            current.add("education.school_raw", school_src.value, school_src)
                            if degree_range:
                                degree_src = _source(span, line.start + at + degree_range[0], line.start + at + degree_range[1])
                                current.add("education.degree_level", degree_value(degree_src.value), degree_src)
                        else:
                            head = re.split(r"\s{2,}|\s+[·|]\s+", tail, maxsplit=1)[0]
                            at = text.rfind(head)
                            if current.kind == "work" and ORG.search(head):
                                current.add("work.company", head, _source(span, line.start + at, line.start + at + len(head)))
                            if current.kind == "education" and SCHOOL.search(head):
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
                if key == "profile.name" and profile.fields.get(key) and key in profile.heuristic:
                    profile.fields.pop(key)
                    profile.sources.pop(key, None)
                    profile.issues.pop(key, None)
                    profile.heuristic.discard(key)
                profile.add(key, "".join(value.split()) if key == "profile.name" and HAN_NAME.fullmatch(value) else value, source)
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
            elif not pair and span.span_kind == "heading" and current is profile and not profile.fields.get("profile.name") and (HAN_NAME.fullmatch(value) or LATIN_NAME.fullmatch(value)):
                profile.add("profile.name", "".join(value.split()) if HAN_NAME.fullmatch(value) else value, source)
                profile.heuristic.add("profile.name")
            elif not pair and current.kind == "other":
                current.issues["source.unassigned"] = ("pending_review", source.span)
                current.sources["source.unassigned"].append(source)
            if current.kind in {"work", "project", "skill"} or key == "skill.name":
                parent = current if current.kind in {"work", "project"} else None
                claimed = []
                matches = []
                for alias, skill_id, pattern in skill_patterns:
                    is_language = skill_id in programming_languages
                    if alias in {"c", "r"} and not (language_line or current.kind == "skill" or key == "skill.name"):
                        continue
                    if language_line and not is_language:
                        continue
                    for match in pattern.finditer(value):
                        if alias in {"c", "r"} and not match[0].isupper():
                            continue
                        if alias == "go" and match[0] != "Go" and key != "skill.name" and current.kind != "skill":
                            continue
                        if any(match.start() < end and start < match.end() for start, end in claimed):
                            continue
                        claimed.append(match.span())
                        priority = None
                        if is_language:
                            suffix = value[match.end():]
                            annotation = re.match(r"\s*[（(]([^）)]{1,45})[）)]", suffix)
                            primary = ((annotation and PRIMARY_LANGUAGE.search(annotation[1]))
                                       or heading_key in {"主语言", "主开发语言", "主编程语言", "primary language", "primary programming language"})
                            priority = 1 if primary else 2 if language_line else 3
                        matches.append((match.start(), match.end(), skill_id, priority))
                for start, end, skill_id, priority in sorted(matches):
                    identity = id(parent), skill_id
                    record = seen_skills.get(identity)
                    skill_source = _source(span, source.start + start, source.start + end)
                    if record is None:
                        record = Record("skill", parent=parent)
                        records.append(record)
                    old_priority = record.fields.get("language_priority", 4)
                    if not record.fields or (priority is not None and priority < old_priority):
                        if priority is not None and (skill_source.confidence is None or skill_source.confidence >= .90):
                            record.fields["language_priority"] = priority
                        if not record.fields or skill_source.confidence is None or skill_source.confidence >= .90:
                            record.add("skill.name", skill_id, skill_source)
                    if "skill.name" in record.fields:
                        seen_skills[identity] = record
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
    if "profile.name" not in profile.fields:
        profile.issues.setdefault("profile.name", ("not_found", None))
    return [record for record in records if record.fields or record.issues]
