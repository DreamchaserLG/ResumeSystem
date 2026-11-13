"""Generate synthetic resumes for exercising the real import flow. No database writes."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document
from PIL import Image, ImageDraw, ImageFont
import pymupdf


@dataclass(frozen=True)
class DemoResume:
    key: str
    name: str
    job: str
    fmt: str
    education: tuple[tuple[str, str, str], ...]
    skills: tuple[str, ...]
    experience: str
    avatar: bool = False
    stage: str = "review"


SAMPLES = (
    DemoResume("hr-recruit", "陆知远", "recruit", "pdf", (("安阳工学院", "本科", "人力资源管理"),),
               ("校园招聘、人才甄选、结构化面试、人才画像", "招聘渠道、员工关系、劳动合同"),
               "负责校园招聘和简历筛选，组织结构化面试并跟踪录用。", True),
    DemoResume("hr-pay", "陈语桐", "people", "docx", (("浙江大学", "硕士", "劳动经济学"), ("安阳工学院", "本科", "管理学")),
               ("薪酬体系、绩效考核、岗位定级", "组织发展、人才盘点、继任计划、人力资源系统"),
               "为人力资源团队建设薪酬体系和绩效管理流程。", stage="progress"),
    DemoResume("hr-global", "许静宜", "people", "docx", (("利兹大学", "硕士", "国际人力资源管理"),),
               ("劳动争议、员工沟通、员工入离职", "培训体系、统计分析、Power BI"),
               "开展人力资源数据分析、员工关系与培训体系建设。"),
    DemoResume("tech-backend", "沈嘉禾", "backend", "pdf", (("安阳工学院", "本科", "软件工程"),),
               ("编程语言：Go（主开发语言）、Python", "后端技能：Gin、REST API、微服务", "数据库：PostgreSQL、MySQL、Redis",
                "工具栈：Docker、Linux、Kubernetes、Nginx"),
               "使用 Go 建设订单服务，完成数据库设计和服务器部署。", True, "interview"),
    DemoResume("tech-front", "林星河", "frontend", "docx", (("南京大学", "硕士", "计算机科学"),),
               ("编程语言：TypeScript（主语言）、JavaScript", "前端技能：Vue、React、Vite、HTML、CSS", "技术栈：Docker、CI/CD"),
               "负责前端开发、组件库设计与自动化测试。", stage="hired"),
    DemoResume("bio-discovery", "周清越", "discovery", "pdf", (("斯坦福大学", "博士", "生物化学"), ("浙江大学", "硕士", "药学")),
               ("实验技术：ELISA、qPCR、流式细胞术、细胞培养", "药物筛选、靶点验证、药效评价、药代动力学"),
               "承担生物医药的靶点验证和药效评价，搭建实验方案。", True, "interview"),
    DemoResume("bio-clinical", "宋沐言", "clinical", "docx", (("南京信息工程大学", "硕士", "应用统计"),),
               ("临床研究、临床监查、受试者招募、临床数据管理", "GCP、质量审计、药品注册"),
               "负责临床试验的数据管理和质量审计。", stage="rejected"),
    DemoResume("bio-process", "季闻溪", "process", "png", (("安阳工学院", "本科", "生物工程"),),
               ("发酵工艺、工艺放大、工艺验证、样本管理、HPLC", "GMP、偏差调查、质量管理"),
               "完成生物工艺的工艺验证和样本管理。"),
)
JOBS = {
    "recruit": ("人力资源部", "招聘专员（演示）", ("业务初面", "终面")),
    "people": ("人力资源部", "人力资源伙伴（演示）", ("人事初面", "案例面", "终面")),
    "backend": ("技术研发部", "后端工程师（演示）", ("技术一面", "系统设计", "终面")),
    "frontend": ("技术研发部", "前端工程师（演示）", ("技术面", "终面")),
    "discovery": ("生物医药部", "药物研发科学家（演示）", ("专业一面", "研究报告", "终面")),
    "clinical": ("生物医药部", "临床研究专员（演示）", ("合规面", "终面")),
    "process": ("生物医药部", "生物工艺工程师（演示）", ("工艺面", "终面")),
}


def _lines(sample):
    yield "基本信息"
    yield f"姓名：{sample.name}"
    yield "求职岗位：" + JOBS[sample.job][1].replace("（演示）", "")
    yield "城市：杭州"
    yield "教育经历"
    for school, degree, major in sample.education:
        yield f"学校：{school}"
        yield f"学历：{degree}"
        yield f"专业：{major}"
    yield "工作经历"
    yield "公司：示例企业有限公司"
    yield "时间：2021.09 - 2025.06"
    yield sample.experience
    yield "专业技能"
    yield from sample.skills


def _illustrated_avatar():
    """A fixture illustration with no real person's likeness."""
    img = Image.new("RGB", (144, 180), "#d8e9ed")
    d = ImageDraw.Draw(img)
    d.ellipse((14, 110, 130, 222), fill="#335874")
    d.rounded_rectangle((58, 89, 86, 127), radius=10, fill="#d79b78")
    d.ellipse((36, 27, 108, 115), fill="#edb691")
    d.pieslice((33, 18, 111, 87), 175, 358, fill="#253752")
    d.ellipse((55, 70, 61, 76), fill="#253752")
    d.ellipse((83, 70, 89, 76), fill="#253752")
    d.arc((63, 76, 82, 94), 15, 158, fill="#9a5351", width=2)
    data = BytesIO()
    img.save(data, format="PNG")
    return data.getvalue()


def render(sample, font=None):
    lines = list(_lines(sample))
    if sample.fmt == "docx":
        doc = Document()
        doc.add_heading("演示简历", 0)
        for line in lines:
            doc.add_paragraph(line)
        output = BytesIO()
        doc.save(output)
        return output.getvalue()
    if sample.fmt == "pdf":
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((48, 51), "演示简历", fontname="china-s", fontsize=22, color=(.13,.25,.39))
            if sample.avatar:
                page.insert_image(pymupdf.Rect(442, 72, 514, 162), stream=_illustrated_avatar())
            for number, line in enumerate(lines):
                page.insert_text((48, 81 + 23 * number), line, fontname="china-s", fontsize=13)
            return pdf.tobytes()
    if font is None:
        raise ValueError("Provide a Chinese font for the PNG fixture")
    image = Image.new("RGB", (1300, 1420), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 47), "演示简历", font=ImageFont.truetype(font, 54), fill="#21384e")
    body = ImageFont.truetype(font, 31)
    for number, line in enumerate(lines):
        draw.text((70, 130 + 59 * number), line, font=body, fill="#17283a")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", help="Chinese TTF/TTC font for the PNG fixture")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for sample in SAMPLES:
        (args.output / f"{sample.key}.{sample.fmt}").write_bytes(render(sample, args.font))
