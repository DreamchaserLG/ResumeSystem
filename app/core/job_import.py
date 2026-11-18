"""Rule-based extraction of one editable job draft."""
import re
import unicodedata
from datetime import date

FIELDS = {
    "岗位名称": "title", "职位名称": "title", "招聘岗位": "title", "岗位": "title", "职位": "title",
    "所属部门": "department_name", "招聘部门": "department_name", "部门": "department_name",
    "招聘人数": "planned_headcount", "计划招聘人数": "planned_headcount", "计划人数": "planned_headcount",
    "岗位属性": "recruitment_type", "招聘类型": "recruitment_type",
    "负责人": "recruiter_name", "招聘负责人": "recruiter_name",
    "目标日期": "target_date", "计划完成日期": "target_date",
    "招聘开始日期": "recruitment_start", "开始日期": "recruitment_start",
    "招聘截止日期": "recruitment_end", "截止日期": "recruitment_end", "投递截止日期": "recruitment_end",
    "必备技能": "required_skills", "必需技能": "required_skills", "必须会的技能": "required_skills",
    "工作经验": "experience_tags", "工作经验要求": "experience_tags", "经验要求": "experience_tags",
    "面试轮次": "rounds", "面试流程": "rounds", "招聘流程": "rounds",
}
JOB_TYPES = {"社会招聘": "social", "校园招聘": "campus", "实习招聘": "internship", "内部招聘": "internal",
             "社招": "social", "校招": "campus", "实习": "internship", "内部": "internal"}
JD_HEADINGS = re.compile(r"^(岗位描述|职位描述|岗位职责|工作职责)(?:\s*[:：]|$)")
QUALIFICATIONS = re.compile(r"^(任职要求|岗位要求|任职资格|职位要求)\s*(?:[:：]\s*(.*)|$)")


def job_draft(lines):
    values, description, requirements = {}, [], []
    section = description
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        qualification = QUALIFICATIONS.match(line)
        if qualification:
            section = requirements
            if qualification[2]:
                requirements.append(qualification[2])
            continue
        if JD_HEADINGS.match(line):
            section = description
            description.append(line)
            continue
        match = re.fullmatch(r"([^:：]{1,20})[:：]\s*(.*)", line)
        label = match.group(1).strip() if match else ""
        key = FIELDS.get(label)
        if key:
            value = match.group(2).strip()
            if value and value not in ("—", "-", "待定", "无", "未填写"):
                values.setdefault(key, set()).add(value)
        elif line not in (*FIELDS, "招聘启事", "岗位说明书", "招聘简章"):
            section.append(line)

    if len(values.get("title", ())) > 1:
        raise ValueError("文档含多个岗位，请拆成单份岗位文件后重新导入")

    def one(key):
        found = values.get(key, ())
        return next(iter(found)) if len(found) == 1 else ""

    count = re.fullmatch(r"([0-9]{1,6})\s*(?:人|名|位)?", unicodedata.normalize("NFKC", one("planned_headcount")))
    number = int(count.group(1)) if count else 0
    def parse_date(key):
        day = re.fullmatch(r"([0-9]{4})[-./年]([0-9]{1,2})[-./月]([0-9]{1,2})日?", one(key))
        if day:
            try:
                return date(*map(int, day.groups())).isoformat()
            except ValueError:
                pass
        return None

    def parse_tags(key, limit):
        tags = [item.strip() for item in re.split(r"[、，,；;]",one(key)) if item.strip()]
        return list(dict.fromkeys(item for item in tags if len(item) <= 60))[:limit]

    process = one("rounds")
    rounds = re.split(r"\s*(?:→|->|、|[，,；;])\s*", process) if process else []
    if not (1 <= len(rounds) <= 12 and all(0 < len(item) <= 40 for item in rounds) and len(set(rounds)) == len(rounds)):
        rounds = []
    draft = {
        "title": one("title"), "department_name": one("department_name"),
        "planned_headcount": number if 1 <= number <= 100000 else None,
        "recruitment_type": JOB_TYPES.get(one("recruitment_type")), "recruiter_name": one("recruiter_name"),
        "target_date": parse_date("target_date"), "rounds": rounds, "jd": "\n".join(description),
        "requirements": "\n".join(requirements), "required_skills":parse_tags("required_skills",20),
        "experience_tags":parse_tags("experience_tags",12),
        "recruitment_start":parse_date("recruitment_start"), "recruitment_end":parse_date("recruitment_end"),
    }
    warnings = []
    if draft["recruitment_start"] and draft["recruitment_end"] and draft["recruitment_start"] > draft["recruitment_end"]:
        warnings.append("招聘起止日期顺序有误，请手动填写")
        draft["recruitment_start"] = draft["recruitment_end"] = None
    for key, limit, label in [("title", 120, "岗位名称"), ("recruiter_name", 80, "负责人"), ("jd", 4000, "岗位描述"), ("requirements", 4000, "任职要求")]:
        if len(draft[key]) > limit:
            warnings.append(f"{label}超过 {limit} 字，已截取，请核对")
            draft[key] = draft[key][:limit]
    if len(values.get("department_name", ())) > 1:
        warnings.append("文档含多个部门，请手动选择唯一归属")
    if not any(draft.values()):
        raise ValueError("没有识别到可填入的岗位内容，请换一份文件或手动新建")
    return {**draft, "warnings": warnings}
