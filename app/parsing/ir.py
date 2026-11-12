"""统一中间表示（IR）—— 03 §1。

所有格式（文本 PDF / 扫描 PDF / 图片 / DOCX）解析为同一 IR 后，
由 pipeline 落库为 resume_source_span，下游抽取与索引只感知 IR。
"""
from dataclasses import dataclass, field


@dataclass
class Page:
    page_no: int          # 1-based
    width: int
    height: int


@dataclass
class Span:
    span_kind: str            # text | heading | table | photo
    raw_text: str | None      # photo 区域为 None
    locator: dict             # {"page":1,"bbox":[x0,y0,x1,y1]} / {"docx_para":3} / {"docx_table":[r,c]}
    extraction_method: str    # pdf_text | ocr | docx_paragraph | docx_table | layout_detection | manual
    is_sensitive: bool = False
    ocr_confidence: float | None = None
    reading_order: int = 0
    span_no: int = 0          # pipeline 落库前统一编号


@dataclass
class ParsedDocument:
    pages: list[Page] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def add_span(self, span: Span) -> None:
        self.spans.append(span)

    def renumber(self) -> None:
        for i, s in enumerate(self.spans, start=1):
            s.span_no = i

    def finalize(self) -> "ParsedDocument":
        """按阅读顺序重排并编号；photo 恒定 raw_text=None（01 约束）。"""
        self.spans.sort(key=lambda s: (s.locator.get("page", 0), s.reading_order))
        self.renumber()
        for s in self.spans:
            if s.span_kind == "photo":
                s.raw_text = None
        self.meta["span_count"] = len(self.spans)
        self.meta["photo_span_count"] = sum(1 for s in self.spans if s.span_kind == "photo")
        return self

    def needs_ocr_pages(self) -> list[int]:
        """混合 PDF：标记为扫描的页码（meta.ocr_pages）。"""
        return self.meta.get("ocr_pages", [])
