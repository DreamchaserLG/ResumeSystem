"""Render a recruiting database record as an editable resume without a model."""

import io
import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from unicodedata import east_asian_width

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor


_MISSING = {"", "null", "none", "n/a", "-", "--", "暂无", "无", "未填写", "未提供", "不详"}
_DEGREES = {
    "phd": "博士", "doctorate": "博士", "master": "硕士", "bachelor": "本科",
    "associate": "大专", "high_school": "高中",
}
_HTML = re.compile(r"</?(?:p|div|li|ol|ul|h[1-6]|br|span|table|tr|td|th|b|strong|a|font|body|script|style)\b", re.I)
_INVALID_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF\uD800-\uDFFF]")
_CONTACT = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|(?<!\d)(?:\+?86[ -]?)?1[3-9](?:[ -]?\d){9}(?!\d)"
    r"|(?<!\d)[1-9]\d{16}[\dXx](?!\d)"
    r"|https?://[^\s，。；）]+",
    re.I,
)
_LIST_MARKER = re.compile(
    r"^(?:[•●·▪*–-]\s*|[（(]\d+[）)]\s*|(?:\d{1,3}|[一二三四五六七八九十]+)[、）):：]\s*"
    r"|\d{1,3}[.．,，](?!\d)\s*)"
)
_BODY_LABEL = re.compile(r"^(?:工作|项目)(?:描述|内容|职责|概述)\s*[：:]\s*")
_SENTENCE_END = "。；！？.!?;"
_WORK_LABELS = {
    "工作概述": "overview", "工作简介": "overview", "任职概况": "overview",
    "主要工作内容": "duties", "工作内容": "duties", "工作描述": "duties",
    "主要工作职责": "duties", "主要职责": "duties", "工作职责": "duties",
    "岗位职责": "duties", "负责内容": "duties", "职责": "duties",
    "主要工作业绩": "achievements", "主要业绩": "achievements", "主要成果": "achievements",
    "工作业绩": "achievements", "工作成果": "achievements", "关键成果": "achievements",
}
_WORK_HEADING = re.compile(
    "(" + "|".join(sorted(_WORK_LABELS, key=len, reverse=True)) + r")\s*(?:[：:]\s*|$)"
)
_NON_RESUME_KEYS = {
    "id", "resumeid", "candidateid", "applicationid", "tenantid", "departmentid",
    "status", "delflag", "fileurl", "filepath", "resumeurl", "rawdata", "password",
    "token", "credential", "phone", "mobile", "email", "address", "photo", "photourl",
    "idcard", "idnumber", "身份证", "身份证号", "手机号", "联系方式", "住址", "照片",
}
_OPTIONAL_SECTIONS = (
    ("证书与资质", ("certificates", "certifications")),
    ("语言能力", ("languages", "language_skills")),
    ("获奖经历", ("awards", "honors")),
    ("论文与出版物", ("publications",)),
    ("专利", ("patents",)),
    ("志愿服务", ("volunteer_experience", "volunteer")),
)
_CHINESE_FONT = "SimSun"
_LATIN_FONT = "Times New Roman"
_BLUE = "2A5281"
_PAGE_TEXT_WIDTH = 174


class _PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "li", "p", "div", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in {"li", "p", "div", "tr"}:
            self.parts.append("\n")
        if not self.hidden and tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _decode(value):
    if isinstance(value, str) and value.lstrip().startswith(("{", "[", '"')):
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            pass
    return value


def _text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    value = _INVALID_XML.sub("", str(value)).strip()
    if _HTML.search(value):
        parser = _PlainHTML()
        parser.feed(value)
        parser.close()
        value = "".join(parser.parts).strip()
    return "" if value.casefold() in _MISSING else value


def _first(values, *keys):
    for key in keys:
        value = _text(values.get(key))
        if value:
            return value
    return ""


def _lines(value):
    value = _decode(value)
    if isinstance(value, Mapping):
        result = []
        for key, item in value.items():
            label = _text(key)
            if not label or re.sub(r"[^\w]|_", "", label.casefold()) in _NON_RESUME_KEYS:
                continue
            content = " / ".join(_lines(item))
            if content:
                result.append(f"{label}：{content}")
        return result
    if isinstance(value, list):
        return [line for item in value for line in _lines(item)]
    return [line.strip() for line in _text(value).splitlines() if line.strip()]


def _entries(value):
    value = _decode(value)
    return value if isinstance(value, list) else [value]


def _date(value):
    value = _text(value)
    if value.lower() in {"present", "current", "至今"}:
        return "至今"
    if matched := re.fullmatch(r"(\d{4})[-./](0?[1-9]|1[0-2])(?:[-./]([0-3]?\d))?", value):
        year, month, day = matched.groups()
        return f"{year}年{int(month):02d}月" + (f"{int(day):02d}日" if day else "")
    return value


def _period(entry):
    explicit = _first(entry, "period", "date_range", "dateRange")
    if explicit:
        return explicit
    start = _date(_first(entry, "startDate", "start_date", "start"))
    end = _date(_first(entry, "endDate", "end_date", "end"))
    if start and end:
        return f"{start}–{end}"
    if start:
        return start + "起"
    return f"截至{end}" if end else ""


def _paragraphs(lines):
    """Remove visual numbering and join source fragments into readable paragraphs."""
    paragraphs = []
    current = ""
    for original in lines:
        body = _BODY_LABEL.sub("", original).strip()
        marker = _LIST_MARKER.match(body)
        line = body[marker.end():].strip() if marker else body
        if not line:
            continue
        if not current:
            current = line
            continue

        wraps = not marker and (
            current.endswith(("，", "、", "：", ":", "（", "(", "和", "与", "以及"))
            or line.startswith(("，", "。", "、", "）", ")", "并且", "以及"))
        )
        if wraps:
            current += line
            continue
        if not current.endswith(_SENTENCE_END):
            current += "。"
        if len(current) + len(line) > 170:
            paragraphs.append(current)
            current = line
        else:
            current += line
    if current:
        paragraphs.append(current)
    return paragraphs


def _work_groups(entry):
    groups = {"overview": [], "duties": [], "achievements": []}
    fields = (("overview", "overview"), ("work_summary", "overview"), ("description", "duties"),
              ("summary", "duties"), ("content", "duties"), ("responsibilities", "duties"),
              ("achievements", "achievements"), ("highlights", "achievements"))
    for key, group in fields:
        for line in _lines(entry.get(key)):
            start = 0
            for match in _WORK_HEADING.finditer(line):
                if piece := line[start:match.start()].strip():
                    groups[group].append(piece)
                group = _WORK_LABELS[match.group(1)]
                start = match.end()
            if piece := line[start:].strip():
                groups[group].append(piece)
    return {group: list(dict.fromkeys(lines)) for group, lines in groups.items() if lines}


def _bullet_points(lines):
    points = []
    for value in lines:
        marker = _LIST_MARKER.match(value)
        text = value[marker.end():].strip() if marker else value.strip()
        if not text:
            continue
        if not marker and points and (
            points[-1].endswith(("，", "、", "（", "(")) or text.startswith(("，", "。", "、", "）", ")"))
        ):
            points[-1] += text
        else:
            points.append(text)
    return points


def _merge_history(base, vendor, kind):
    def key(item):
        if not isinstance(item, Mapping):
            return None
        if kind == "education":
            label = _first(item, "school", "schoolName", "institution", "university", "name")
            role = _first(item, "major", "speciality", "discipline", "field")
        else:
            label = _first(item, "company", "companyName", "employer", "organization", "name")
            role = _first(item, "position", "title", "jobTitle", "role")
        if not label:
            return None
        return tuple(re.sub(r"\s+", "", value).casefold() for value in (label, role, _period(item)))

    entries = [item for item in _entries(base) if item]
    matches = {key(item): i for i, item in enumerate(entries) if key(item)}
    for item in _entries(vendor):
        if not item:
            continue
        identifier = key(item)
        if identifier is None or identifier not in matches:
            entries.append(item)
            continue
        index = matches.pop(identifier)
        primary = entries[index]
        entries[index] = {**item, **{key: value for key, value in primary.items() if value}}
    return entries


def _experience(value, kind):
    for entry in _entries(value):
        if isinstance(entry, Mapping):
            if kind == "education":
                title = _first(entry, "school", "schoolName", "institution", "university", "name")
                major = _first(entry, "major", "speciality", "discipline", "field")
                degree = _first(entry, "degree", "degreeLevel", "academicDegree", "education")
                subtitle = " · ".join(filter(None, (major, _DEGREES.get(degree.casefold(), degree))))
            elif kind == "work":
                title = _first(entry, "company", "companyName", "employer", "organization", "practiceName", "name")
                position = _first(entry, "position", "title", "jobTitle", "role")
                team = _first(entry, "underlingNumber", "direct_reports")
                team = f"下属人数 {team}" if re.fullmatch(r"[1-9]\d*", team) else ""
                subtitle = " · ".join(dict.fromkeys(filter(None, (
                    position, _first(entry, "department"), _first(entry, "location"), team,
                ))))
            else:
                title = _first(entry, "projectName", "project_name", "name", "title")
                subtitle = _first(entry, "role", "position", "projectRole")
            period = _period(entry)
            if kind == "work":
                details = _work_groups(entry)
            else:
                details = list(dict.fromkeys(
                    line for key in ("description", "summary", "projectDescription", "content", "responsibilities", "achievements", "highlights", "technologies")
                    for line in _lines(entry.get(key))
                ))
            if title or subtitle or period or details:
                yield title, subtitle, period, details
        elif lines := _lines(entry):
            yield "", "", "", _work_groups({"description": lines}) if kind == "work" else lines


def _source(record, extras, *keys):
    for key in keys:
        for fields in (record, extras):
            value = _decode(fields.get(key))
            if value and (not isinstance(value, str) or _text(value)):
                return value
    return None


def _formatting(document):
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.left_margin = section.right_margin = Mm(18)
    section.top_margin, section.bottom_margin = Mm(14), Mm(14)
    section.footer_distance = Mm(7)
    styles = (
        ("Normal", 10.5, "1B2433", False, 1.24, 4),
        ("Resume Name", 25, "17273F", True, 1.12, 6),
        ("Resume Meta", 10, "526477", False, 1.16, 8),
        ("Resume Section", 11, "FFFFFF", True, 1.12, 0),
        ("Resume Entry", 11.5, "17273F", True, 1.17, 2),
        ("Resume Role", 10.5, "364864", False, 1.16, 3),
        ("Resume Entry Detail", 10, "364864", False, 1.17, 3),
        ("Resume Work Label", 10, _BLUE, True, 1.13, 2),
        ("Resume Body", 10.5, "1B2433", False, 1.19, 3),
        ("Resume Foot", 8, "526477", False, 1.16, 0),
    )
    for name, size, color, bold, spacing, after in styles:
        style = document.styles[name] if name in document.styles else document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.font.name = _LATIN_FONT
        style.font.size = Pt(size)
        style.font.bold = bold
        style.font.color.rgb = RGBColor.from_string(color)
        run_properties = style.element.get_or_add_rPr()
        run_properties.rFonts.set(qn("w:eastAsia"), _CHINESE_FONT)
        language = OxmlElement("w:lang")
        language.set(qn("w:val"), "en-US")
        language.set(qn("w:eastAsia"), "zh-CN")
        run_properties.append(language)
        style.paragraph_format.line_spacing = spacing
        style.paragraph_format.space_after = Pt(after)
        if name in {"Resume Name", "Resume Meta", "Resume Section", "Resume Entry", "Resume Role", "Resume Entry Detail", "Resume Work Label"}:
            style.paragraph_format.keep_with_next = True
    body = document.styles["Resume Body"].paragraph_format
    body.line_spacing = Pt(15)
    body.widow_control = True
    entry = document.styles["Resume Entry"].paragraph_format
    entry.tab_stops.add_tab_stop(Mm(_PAGE_TEXT_WIDTH), WD_TAB_ALIGNMENT.RIGHT)


def _heading(document, title):
    title = title[:32]
    table = document.add_table(rows=1, cols=2)
    table.autofit = False
    label_width = min(65, max(26, 5.3 * len(title)))
    row = table.rows[0]
    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    for column, cell, width in zip(table.columns, row.cells, (label_width, _PAGE_TEXT_WIDTH - label_width)):
        column.width = cell.width = Mm(width)
    label, rule = row.cells
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), _BLUE)
    label._tc.get_or_add_tcPr().append(shading)
    borders = OxmlElement("w:tcBorders")
    border = OxmlElement("w:bottom")
    for key, value in {"val": "single", "sz": "5", "color": "A7B6CA"}.items():
        border.set(qn("w:" + key), value)
    borders.append(border)
    rule._tc.get_or_add_tcPr().append(borders)
    for cell in row.cells:
        cell.paragraphs[0].style = "Resume Section"
        cell.paragraphs[0].paragraph_format.space_before = Pt(3)
        cell.paragraphs[0].paragraph_format.space_after = Pt(3)
    label.paragraphs[0].add_run(title)


def _title_too_wide(name, period):
    title_units = sum(1 if east_asian_width(char) in {"W", "F"} else 0.6 for char in name)
    date_units = sum(1 if east_asian_width(char) in {"W", "F"} else 0.6 for char in period)
    return 11.5 * title_units + 9 * date_units + 12 > Mm(_PAGE_TEXT_WIDTH).pt


def _education_line(document, school, degree, period):
    table = document.add_table(rows=1, cols=3)
    table.autofit = False
    row = table.rows[0]
    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    for column, cell, width, value, alignment, style in zip(
        table.columns, row.cells, (54, 66, 54), (school, degree, period),
        (WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.RIGHT),
        ("Resume Entry", "Resume Entry Detail", "Resume Entry Detail"),
    ):
        column.width = cell.width = Mm(width)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.style = style
        paragraph.alignment = alignment
        paragraph.paragraph_format.space_before = Pt(4)
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(value)
        if alignment == WD_ALIGN_PARAGRAPH.RIGHT:
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor.from_string("526477")


def render_resume_docx(record: Mapping, *, approved_sections: Mapping | None = None, hide_identity: bool = False) -> bytes:
    """Return a DOCX from a single display-approved SQL row.

    Experiences accept JSON strings, lists, objects or plain text. Pass other
    HR-approved sections as {Chinese heading: values}. Do not pass raw vendor
    records as approved sections. hide_identity omits header identifiers and
    masks common contact patterns in body text. It retains career history.
    """
    document = Document()
    _formatting(document)
    document.core_properties.title = "简历 · 结构化记录重建"
    document.core_properties.author = ""
    identity = "候选人样例 A" if hide_identity else _first(record, "name", "full_name") or "候选人"
    document.add_paragraph(identity, "Resume Name").alignment = WD_ALIGN_PARAGRAPH.CENTER

    def safe(text):
        return _CONTACT.sub("[已隐藏]", text) if hide_identity else text

    current = _first(record, "current_position", "currentPosition")
    target = _first(record, "target_role", "desired_position", "desiredPosition")
    applied = _first(record, "position_applied", "applied_position")
    roles = [f"{label}：{value}" for label, value in (("投递岗位", applied), ("求职意向", target), ("当前职位", current)) if value]
    if roles:
        document.add_paragraph(safe("  ·  ".join(roles)), "Resume Role").alignment = WD_ALIGN_PARAGRAPH.CENTER
    location = _first(record, "expected_location", "expected_work_location")
    years = _first(record, "work_years", "declared_work_years")
    details = []
    if years:
        details.append(f"申报工作年限：{years} 年" if re.fullmatch(r"\d+(?:\.\d+)?", years) else f"申报工作年限：{years}")
    if location:
        details.append(f"意向地点：{location}")
    if not hide_identity:
        details.extend(f"{label}：{value}" for label, value in (
            ("手机", _first(record, "phone", "mobile")), ("邮箱", _first(record, "email")), ("主页", _first(record, "website")),
        ) if value)
    if details:
        document.add_paragraph(safe("  ·  ".join(details)), "Resume Meta").alignment = WD_ALIGN_PARAGRAPH.CENTER

    extras = _decode(record.get("extras"))
    extras = extras if isinstance(extras, Mapping) else {}
    education = list(_experience(_merge_history(
        _source(record, extras, "education_experience", "educationExperiences", "education_history"),
        extras.get("vendor_education"), "education",
    ), "education"))
    if not education and any(_first(record, key) for key in ("school", "major", "education")):
        education = list(_experience({"school": record.get("school"), "major": record.get("major"), "degree": record.get("education")}, "education"))
    work = list(_experience(_merge_history(
        _source(record, extras, "work_experience", "workExperiences", "employment_history"),
        extras.get("vendor_work"), "work",
    ), "work"))
    if not work and (company := _first(record, "current_company", "currentCompany")):
        work = [(company, current, "", {})]
    projects = list(_experience(_source(record, extras, "project_experience", "projectExperiences", "projects", "projectInfo"), "project"))
    internships = list(_experience(_source(record, extras, "internship_experience", "practiceInfo"), "work"))

    def work_details(groups):
        overview = groups.get("overview", [])
        duties = groups.get("duties", [])
        if overview or duties:
            document.add_paragraph("工作内容", "Resume Work Label")
        for line in _paragraphs(overview):
            document.add_paragraph(safe(line), "Resume Body")
        if overview and duties:
            document.add_paragraph("主要职责", "Resume Work Label")
        for label, lines in ((None, duties), ("工作业绩", groups.get("achievements", []))):
            points = _bullet_points(lines)
            if points and label:
                document.add_paragraph(label, "Resume Work Label")
            for point in points:
                paragraph = document.add_paragraph(style="Resume Body")
                paragraph.add_run("•\u2002").font.color.rgb = RGBColor.from_string(_BLUE)
                paragraph.add_run(safe(point))
                paragraph.paragraph_format.line_spacing = Pt(15)
                paragraph.paragraph_format.space_after = Pt(1)
                paragraph.paragraph_format.keep_together = len(point) <= 170

    def section(title, entries, *, prose=False, kind=None):
        if not entries:
            return
        _heading(document, safe(title))
        for index, (name, subtitle, period, lines) in enumerate(entries):
            if kind == "education":
                _education_line(document, safe(name), safe(subtitle), safe(period))
                for line in lines:
                    document.add_paragraph(safe(line), "Resume Body")
                continue
            if name or period:
                entry = document.add_paragraph(style="Resume Entry")
                entry.paragraph_format.space_before = Pt(7 if index else 5)
                entry.add_run(safe(name))
                if period:
                    spacer = "\n\t" if _title_too_wide(name, period) else "\t"
                    date = entry.add_run(spacer + safe(period))
                    date.bold = False
                    date.font.size = Pt(9)
                    date.font.color.rgb = RGBColor.from_string("526477")
                if not subtitle and not lines:
                    entry.paragraph_format.keep_with_next = False
            if subtitle:
                sub = document.add_paragraph(safe(subtitle), "Resume Entry Detail")
                if not lines:
                    sub.paragraph_format.keep_with_next = False
            if kind == "work":
                work_details(lines)
                continue
            for index, line in enumerate(_paragraphs(lines) if prose else lines):
                paragraph = document.add_paragraph(safe(line), "Resume Body")
                if prose:
                    paragraph.paragraph_format.keep_together = len(line) <= 170
                if index == 0 and not (name or subtitle or period):
                    paragraph.paragraph_format.space_before = Pt(4)
        spacer = document.add_paragraph().paragraph_format
        spacer.space_after = Pt(0)
        spacer.line_spacing = Pt(4)

    section("教育经历", education, kind="education")
    section("工作经历", work, kind="work")
    section("实习经历", internships, kind="work")
    section("项目经历", projects, prose=True)
    section("专业技能", [("", "", "", lines)] if (lines := _lines(_source(record, extras, "skills", "skill_list"))) else [])
    for title, keys in _OPTIONAL_SECTIONS:
        if lines := _lines(_source(record, extras, *keys)):
            section(title, [("", "", "", lines)])
    for title, content in (approved_sections or {}).items():
        if (label := _text(title)) and (lines := _lines(content)):
            section(label, [("", "", "", lines)])
    if lines := _lines(_source(record, extras, "self_evaluation", "selfEvaluation", "summary")):
        section("自我评价", [("", "", "", lines)], prose=True)

    footer = document.sections[0].footer.paragraphs[0]
    footer.style = "Resume Foot"
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run("结构化记录重建  ·  脱敏样稿" if hide_identity else "由结构化记录重建")
    footer.add_run("  ·  第 ")
    page_number = OxmlElement("w:fldSimple")
    page_number.set(qn("w:instr"), "PAGE")
    footer._p.append(page_number)
    footer.add_run(" 页")
    result = io.BytesIO()
    document.save(result)
    return result.getvalue()
