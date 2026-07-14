# -*- coding: utf-8 -*-
"""构建肿瘤药物医保限定 + 临床指导原则知识库。

权威顺序只有一条：医保有非空限定时采用医保限定；未提取到医保限定时，
回退到《新型抗肿瘤药物临床应用指导原则》的适应证。目录属性未知时仍
保留指导原则条目，但显式标记为医保状态待人工核对。

输入：
  docs/药品限制/2025药品目录（国家）.pdf
  docs/药品限制/新型抗肿瘤药物临床应用指导原则（2025年版）.pdf
  docs/药品限制/药品总库医院_202606.xls

输出：
  configs/oncology_drug_kb.json
  output/oncology_drug_kb_review.csv

运行：uv run --extra kb python scripts/build_oncology_drug_kb.py
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "docs" / "药品限制"
NATIONAL_PDF = SOURCE_DIR / "2025药品目录（国家）.pdf"
GUIDELINE_PDF = SOURCE_DIR / "新型抗肿瘤药物临床应用指导原则（2025年版）.pdf"
HOSPITAL_XLS = SOURCE_DIR / "药品总库医院_202606.xls"
OUT_KB = ROOT / "configs" / "oncology_drug_kb.json"
OUT_REVIEW = ROOT / "output" / "oncology_drug_kb_review.csv"

KB_VERSION = "1.0"
EXPECTED_GUIDELINE_CHAPTERS = 261
EXPECTED_GUIDELINE_DRUGS = 188

# 页码是 PDF 的 1-based 物理页码，不是文内印刷页码。
NATIONAL_PAGE_RANGES = {
    "regular": (9, 82),
    "negotiated": (126, 180),
    "bid": (189, 191),
}
EXPECTED_NATIONAL_NUMBERS = {
    "regular": 1446,
    "negotiated": 399,
    "bid": 12,
}

SYSTEM_LABELS = (
    "呼吸系统肿瘤用药",
    "消化系统肿瘤用药",
    "血液肿瘤用药",
    "泌尿系统肿瘤用药",
    "乳腺癌用药",
    "皮肤肿瘤用药",
    "骨与软组织肿瘤用药",
    "头颈部肿瘤用药",
    "生殖系统肿瘤用药",
    "泛实体瘤用药",
)

SALT_PREFIXES = (
    "甲苯磺酸",
    "甲磺酸",
    "羟乙磺酸",
    "门冬氨酸",
    "枸橼酸",
    "苹果酸",
    "富马酸",
    "马来酸",
    "琥珀酸",
    "己二酸",
    "苯磺酸",
    "酒石酸",
    "氢溴酸",
    "棕榈酸",
    "盐酸",
    "磷酸",
    "硫酸",
    "醋酸",
    "乳酸",
    "草酸",
)
DOSAGE_PREFIXES = ("注射用", "吸入用")
DOSAGE_SUFFIXES = (
    "脂质体注射液",
    "聚合物胶束",
    "肠溶胶囊",
    "缓释胶囊",
    "口服混悬液",
    "口服溶液",
    "干混悬剂",
    "冻干粉针剂",
    "软胶囊",
    "肠溶片",
    "缓释片",
    "分散片",
    "咀嚼片",
    "泡腾片",
    "口崩片",
    "注射液",
    "注射剂",
    "胶囊剂",
    "颗粒剂",
    "混悬液",
    "胶囊",
    "颗粒",
    "片剂",
    "乳剂",
    "片",
)

_CHAPTER_RE = re.compile(
    r"(?m)^[ \t\u3000\f]*(?:※[ \t\u3000]*)?"
    r"(?:[一二三四五六七八九十百〇○零两][ \t\u3000]*)+"
    r"[、．.]\s*"
    r"(?P<title>[^\n]+?)\s*$"
)
_FORM_RE = re.compile(r"制\s*剂\s*与\s*规\s*格\s*[：:]")
_INDICATION_RE = re.compile(r"适\s*应\s*证\s*[：:]")
_RATIONAL_RE = re.compile(r"合\s*理\s*用\s*药\s*要\s*点\s*[：:]")
_CATEGORY_RE = re.compile(r"^X[A-Z][A-Z0-9]*$")
_NUMBER_RE = re.compile(r"^(?P<class>[甲乙])?(?P<number>★?\(?\d+\)?)$")


def _clean(value: Any) -> str:
    """把表格空值和多余空白归一为稳定文本。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\xa0", " ")
    if text.strip().lower() in {"", "nan", "none", "null"}:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _unique(values: Iterable[str]) -> list[str]:
    """去空、去重并排序，保证重复构建字节稳定。"""
    return sorted({_clean(value) for value in values if _clean(value)})


def normalize_generic_name(name: str) -> str:
    """保守归一通用名；匹配时只允许归一后全等，不做子串匹配。"""
    value = unicodedata.normalize("NFKC", name or "")
    value = re.sub(r"[\s\-‐‑‒–—―·]", "", value)
    value = re.sub(r"[（(](?:I{1,4}|IV|V|VI{0,3}|Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ)[）)]$", "", value)
    value = re.sub(r"[（(](?:皮下|静脉|注射|口服)[^）)]*[）)]$", "", value)
    for prefix in DOSAGE_PREFIXES:
        if value.startswith(prefix) and len(value) - len(prefix) >= 2:
            value = value[len(prefix) :]
            break
    for prefix in SALT_PREFIXES:
        if value.startswith(prefix) and len(value) - len(prefix) >= 2:
            value = value[len(prefix) :]
            break
    changed = True
    while changed:
        changed = False
        for suffix in DOSAGE_SUFFIXES:
            if value.endswith(suffix) and len(value) - len(suffix) >= 2:
                value = value[: -len(suffix)]
                changed = True
                break
    return value.strip(" ,，。;；:：*#△☼")


def names_match(left: str, right: str) -> bool:
    """药名仅按保守归一后的全等匹配，杜绝相似单抗串味。"""
    left_key = normalize_generic_name(left)
    return bool(left_key) and left_key == normalize_generic_name(right)


def _load_pdfplumber() -> Any:
    """仅在真正读 PDF 时加载可选依赖，纯函数测试不需要安装它。"""
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 pdfplumber，无法抽取药品 PDF 表格。请执行："
            "uv run --extra kb python scripts/build_oncology_drug_kb.py"
        ) from exc
    return pdfplumber


def _compact_cell(value: Any) -> str:
    """用于识别表格代码与编号，不改变最终保留的原文。"""
    return re.sub(r"\s+", "", _clean(value))


def _catalog_number(value: str) -> int | None:
    """目录编号和星号回指均还原为整数，供完整性校验。"""
    match = re.search(r"\d+", value or "")
    return int(match.group()) if match else None


def _parse_national_row(
    cells: list[Any], section: str, pdf_page: int, category_code: str
) -> dict[str, Any] | None:
    """把 pdfplumber 的单行表格单元格转成一条国家目录记录。"""
    raw = [_clean(cell) for cell in cells]
    compact = [_compact_cell(cell) for cell in cells]
    number_index: int | None = None
    number_match: re.Match[str] | None = None
    for index, value in enumerate(compact):
        match = _NUMBER_RE.fullmatch(value)
        if match:
            number_index, number_match = index, match
            break
    if number_index is None or number_match is None:
        return None

    name_index = next(
        (index for index in range(number_index + 1, len(raw)) if raw[index]), None
    )
    if name_index is None:
        return None

    insurance_class = number_match.group("class") or ""
    if not insurance_class:
        insurance_class = next(
            (
                compact[index]
                for index in range(number_index - 1, -1, -1)
                if compact[index] in {"甲", "乙"}
            ),
            "",
        )

    if section == "regular":
        restriction_index = len(raw) - 1
        dosage_form = next(
            (raw[index] for index in range(name_index + 1, restriction_index) if raw[index]),
            "",
        )
        payment_standard = ""
        validity = ""
    else:
        restriction_index = max(name_index + 1, len(raw) - 2)
        dosage_form = ""
        payment_standard = next(
            (raw[index] for index in range(name_index + 1, restriction_index) if raw[index]),
            "",
        )
        validity = raw[-1] if len(raw) > name_index + 1 else ""

    return {
        "section": section,
        "category_code": category_code,
        "insurance_class": insurance_class,
        "catalog_number": number_match.group("number"),
        "generic_name": raw[name_index],
        "dosage_form": dosage_form,
        "payment_standard": payment_standard,
        "restriction": raw[restriction_index] if restriction_index < len(raw) else "",
        "validity": validity,
        "pdf_page": pdf_page,
        "printed_page": pdf_page - 1,
    }


def parse_national_catalog_pdf(path: Path) -> list[dict[str, Any]]:
    """按指定页段用 pdfplumber 表格抽取国家西药目录。"""
    pdfplumber = _load_pdfplumber()
    records: list[dict[str, Any]] = []
    observed: dict[str, set[int]] = defaultdict(set)
    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
    }

    with pdfplumber.open(path) as pdf:
        for section, (first_page, last_page) in NATIONAL_PAGE_RANGES.items():
            current_category = ""
            for pdf_page in range(first_page, last_page + 1):
                page = pdf.pages[pdf_page - 1]
                tables = page.extract_tables(table_settings=table_settings)
                if not tables:
                    raise ValueError(f"国家目录 PDF 第 {pdf_page} 页未抽到表格")
                for table in tables:
                    for cells in table:
                        compact = [_compact_cell(cell) for cell in cells]
                        category_candidates = [
                            value for value in compact if _CATEGORY_RE.fullmatch(value)
                        ]
                        if category_candidates:
                            current_category = max(category_candidates, key=len)
                        record = _parse_national_row(
                            cells, section, pdf_page, current_category
                        )
                        if not record:
                            continue
                        records.append(record)
                        number = _catalog_number(record["catalog_number"])
                        if number is not None:
                            observed[section].add(number)

    for section, maximum in EXPECTED_NATIONAL_NUMBERS.items():
        missing = sorted(set(range(1, maximum + 1)) - observed[section])
        if missing:
            preview = ", ".join(str(value) for value in missing[:10])
            raise ValueError(
                f"国家目录 {section} 缺 {len(missing)} 个主编号（前 10 个：{preview}），"
                "请核对 pdfplumber 表格抽取结果"
            )
    return records


def _clean_pdf_prose(value: str) -> str:
    """去掉独立页码并合并 PDF 换行，正文措辞保持不改写。"""
    lines: list[str] = []
    for raw_line in value.replace("\r", "\n").splitlines():
        line = _clean(raw_line)
        if not line or re.fullmatch(r"(?:第\s*)?\d{1,3}(?:\s*页)?", line):
            continue
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _chapter_generic_name(title: str) -> str:
    """从“中文通用名 EnglishName”章节标题中取中文通用名。"""
    value = _clean(title)
    value = re.split(r"\s+(?=[A-Za-z])", value, maxsplit=1)[0].strip()
    value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)
    return value.strip(" *#△☼")


def _special_consensus(rational_use: str) -> list[str]:
    """抽出 ※ 标记后的专家共识条目；完整原文仍保留在 rational_use。"""
    items: list[str] = []
    for match in re.finditer(
        r"※\s*((?:\d+[.．])?.*?)(?=(?:\s+\d+[.．])|(?:\s+※)|$)",
        rational_use,
        flags=re.S,
    ):
        text = _clean_pdf_prose(match.group(1))
        if text and not text.startswith("本指导原则"):
            items.append(text)
    return _unique(items)


def _page_for_offset(page_starts: list[int], page_numbers: list[int], offset: int) -> int:
    """把合并文本偏移还原到 PDF 物理页码。"""
    index = bisect.bisect_right(page_starts, offset) - 1
    return page_numbers[max(index, 0)]


def parse_guideline_pages(pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """从逐页文本解析药品章节；同一通用名的多个癌种章节不合并。"""
    parts: list[str] = []
    page_starts: list[int] = []
    page_numbers: list[int] = []
    offset = 0
    for page_number, page_text in pages:
        page_starts.append(offset)
        page_numbers.append(page_number)
        part = (page_text or "") + "\n"
        parts.append(part)
        offset += len(part)
    text = "".join(parts)

    candidates: list[re.Match[str]] = []
    for match in _CHAPTER_RE.finditer(text):
        lookahead = text[match.end() : match.end() + 800]
        form = _FORM_RE.search(lookahead)
        indication = _INDICATION_RE.search(lookahead)
        rational = _RATIONAL_RE.search(lookahead)
        if form and indication and rational and form.start() < indication.start() < rational.start():
            candidates.append(match)

    chapters: list[dict[str, Any]] = []
    for index, match in enumerate(candidates):
        end = candidates[index + 1].start() if index + 1 < len(candidates) else len(text)
        appendix = text.find("\n附表", match.end(), end)
        if appendix >= 0:
            end = appendix
        chunk = text[match.end() : end]
        form = _FORM_RE.search(chunk)
        indication = _INDICATION_RE.search(chunk)
        rational = _RATIONAL_RE.search(chunk)
        if not (form and indication and rational):
            continue

        title = _clean(match.group("title"))
        generic_name = _chapter_generic_name(title)
        if not generic_name:
            continue
        system = ""
        nearest = -1
        for label in SYSTEM_LABELS:
            position = text.rfind(label, 0, match.start())
            if position > nearest:
                nearest, system = position, label.removesuffix("用药")

        start_page = _page_for_offset(page_starts, page_numbers, match.start())
        end_page = _page_for_offset(page_starts, page_numbers, max(match.start(), end - 1))
        rational_use = _clean_pdf_prose(chunk[rational.end() :])
        chapters.append(
            {
                "system": system,
                "chapter_title": title,
                "generic_name": generic_name,
                "form_and_strength": _clean_pdf_prose(
                    chunk[form.end() : indication.start()]
                ),
                "indication": _clean_pdf_prose(
                    chunk[indication.end() : rational.start()]
                ),
                "rational_use": rational_use,
                "special_consensus": _special_consensus(rational_use),
                "pdf_pages": [start_page, end_page],
                "printed_pages": [start_page - 8, end_page - 8],
            }
        )
    return chapters


def parse_guideline_pdf(path: Path) -> list[dict[str, Any]]:
    """读取指导原则 PDF，并以 261 章 / 188 唯一通用名作硬校验。"""
    pdfplumber = _load_pdfplumber()
    with pdfplumber.open(path) as pdf:
        pages = [
            (page.page_number, page.extract_text(x_tolerance=2, y_tolerance=3) or "")
            for page in pdf.pages
        ]
    chapters = parse_guideline_pages(pages)
    unique_names = {normalize_generic_name(row["generic_name"]) for row in chapters}
    if len(chapters) != EXPECTED_GUIDELINE_CHAPTERS or len(unique_names) != EXPECTED_GUIDELINE_DRUGS:
        raise ValueError(
            "指导原则章节抽取数量异常："
            f"{len(chapters)} 章 / {len(unique_names)} 个唯一通用名，"
            f"预期 {EXPECTED_GUIDELINE_CHAPTERS} / {EXPECTED_GUIDELINE_DRUGS}"
        )
    return chapters


def read_hospital_catalog(path: Path) -> list[dict[str, Any]]:
    """读取医院药品总库，保留产品码级医保属性，不按通用名提前合并。"""
    import xlrd

    sheet = xlrd.open_workbook(path).sheet_by_index(0)
    headers = {_clean(sheet.cell_value(0, col)): col for col in range(sheet.ncols)}
    required = {
        "药品编码",
        "药品通用名",
        "支付比例",
        "甲乙类标志",
        "目录顺序编码",
        "限制使用说明",
        "状态",
    }
    missing = sorted(required - headers.keys())
    if missing:
        raise ValueError(f"医院药品总库缺少字段：{', '.join(missing)}")

    records: list[dict[str, Any]] = []
    for row_index in range(1, sheet.nrows):
        get = lambda name: (
            _clean(sheet.cell_value(row_index, headers[name])) if name in headers else ""
        )
        drug_code = get("药品编码")
        generic_name = get("药品通用名")
        if not drug_code or not generic_name:
            continue
        restriction = get("限制使用说明")
        catalog_sequence_code = get("目录顺序编码")
        insurance_class = get("甲乙类标志")
        catalogued = bool(catalog_sequence_code or insurance_class or restriction)
        records.append(
            {
                "drug_code": drug_code,
                "generic_name": generic_name,
                "trade_name": get("商品名"),
                "specification": get("规格包装"),
                "package_unit": get("包装单位"),
                "manufacturer": get("生产厂家"),
                "catalog_sequence_code": catalog_sequence_code,
                "insurance_class": insurance_class,
                "payment_ratio": get("支付比例"),
                "restriction": restriction,
                "medicine_type": get("中西药标志"),
                "fee_type": get("费用类型"),
                "catalog_status": "catalogued" if catalogued else "unknown",
                "status": get("状态"),
            }
        )
    return records


def _guideline_basis(chapters: list[dict[str, Any]]) -> str:
    """按癌种章节拼接适应证，避免同药多癌种被无条件揉成一句。"""
    parts = []
    for chapter in chapters:
        indication = _clean(chapter.get("indication"))
        if not indication:
            continue
        label = chapter.get("system") or chapter.get("chapter_title") or "指导原则"
        parts.append(f"【{label}】{indication}")
    return "\n".join(dict.fromkeys(parts))


def select_effective_entry(
    insurance_records: list[dict[str, Any]],
    guideline_chapters: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """执行唯一优先级，返回医保状态和最多一条生效知识。"""
    active_records = [
        row for row in insurance_records if _clean(row.get("status", "有效")) != "无效"
    ]
    restrictions = _unique(row.get("restriction", "") for row in active_records)
    if restrictions:
        basis = "\n".join(restrictions)
        return "restricted", {
            "rule_type": "限适应症",
            "detect_logic": "患者诊断或用药场景不符合医保限定",
            "basis": basis,
            "source_type": "insurance",
            "source": "insurance",
        }

    catalogued = bool(active_records) and all(
        row.get("catalog_status") == "catalogued" for row in active_records
    )
    guideline_basis = _guideline_basis(guideline_chapters)
    if guideline_basis:
        entry = {
            "rule_type": "超说明书",
            "detect_logic": "患者诊断不符合临床应用指导原则记载的适应证",
            "basis": guideline_basis,
            "source_type": "guideline",
            "source": "guideline",
        }
        if not catalogued:
            entry["requires_insurance_review"] = True
        return ("unrestricted" if catalogued else "unknown"), entry
    return ("unrestricted" if catalogued else "unknown"), None


def _source_metadata(path: Path, title: str, **extra: Any) -> dict[str, Any]:
    """生成可移植的相对路径来源元数据。"""
    try:
        relative = str(path.relative_to(ROOT))
    except ValueError:
        relative = str(path)
    return {"title": title, "file": relative, **extra}


def _source_conflict(
    records: list[dict[str, Any]], *, reject_multiple_variants: bool = False
) -> bool:
    """检查同一运行实体内是否混有限定版本或多个国家目录剂型。"""
    active = [
        row for row in records if _clean(row.get("status", "有效")) != "无效"
    ]
    restrictions = {_clean(row.get("restriction")) for row in active}
    if len(restrictions) > 1:
        return True
    if not reject_multiple_variants:
        return False
    variants = {
        (_clean(row.get("generic_name")), _clean(row.get("dosage_form")))
        for row in active
    }
    return len(variants) > 1


def assemble_knowledge_base(
    national_rows: list[dict[str, Any]],
    hospital_rows: list[dict[str, Any]],
    guideline_chapters: list[dict[str, Any]],
    *,
    national_path: Path = NATIONAL_PDF,
    hospital_path: Path = HOSPITAL_XLS,
    guideline_path: Path = GUIDELINE_PDF,
) -> dict[str, Any]:
    """以医院原始通用名为运行实体，canonical 归一只用于挂接来源。"""
    guidelines_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chapter in guideline_chapters:
        key = normalize_generic_name(chapter.get("generic_name", ""))
        if key:
            guidelines_by_key[key].append(chapter)

    scoped_national = []
    for row in national_rows:
        key = normalize_generic_name(row.get("generic_name", ""))
        category = _clean(row.get("category_code"))
        if key and (category.startswith(("XL01", "XL02")) or key in guidelines_by_key):
            copy = dict(row)
            copy["catalog_status"] = "catalogued"
            copy["status"] = "有效"
            scoped_national.append(copy)

    # 医院实体只认本行 XL01/XL02 产品码或指导原则通用名反查，不按 canonical 扩码。
    scoped_hospital = [
        row
        for row in hospital_rows
        if _clean(row.get("drug_code")).startswith(("XL01", "XL02"))
        or normalize_generic_name(row.get("generic_name", "")) in guidelines_by_key
    ]

    national_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hospital_by_raw_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scoped_national:
        national_by_key[normalize_generic_name(row["generic_name"])].append(row)
    for row in scoped_hospital:
        hospital_by_raw_name[_clean(row["generic_name"])].append(row)

    drugs: dict[str, dict[str, Any]] = {}

    def add_entity(
        entity_name: str,
        canonical_key: str,
        hospital: list[dict[str, Any]],
        national: list[dict[str, Any]],
        guideline: list[dict[str, Any]],
        *,
        entity_type: str,
        conflict: bool,
    ) -> None:
        """添加一个已隔离的运行实体；来源列表不参与隐式裁决。"""
        decision_records = hospital if hospital else national
        insurance_source = "hospital" if hospital else "national"
        if hospital:
            active_hospital = [
                row
                for row in hospital
                if _clean(row.get("status", "有效")) != "无效"
            ]
            hospital_restrictions = _unique(
                row.get("restriction", "") for row in active_hospital
            )
            # 医院 2026 产品级字段优先；其为空时，只接受原始通用名全等的
            # 国家目录限定，不能把同 canonical 的其他剂型限定扩散过来。
            if not hospital_restrictions:
                exact_national = [
                    row
                    for row in national
                    if _clean(row.get("generic_name", "")) == entity_name
                ]
                if _unique(row.get("restriction", "") for row in exact_national):
                    decision_records = exact_national
                    insurance_source = "national"
        if conflict:
            insurance_status, entry = "conflict", None
        else:
            insurance_status, entry = select_effective_entry(
                decision_records, guideline
            )
        if entry:
            entry = dict(entry)
            if entry["source_type"] == "insurance" and insurance_source == "hospital":
                entry["source_label"] = "医院药品总库 2026-06（医保限定，优先）"
                entry["source_refs"] = [
                    f"hospital:{row['drug_code']}" for row in hospital
                ]
            elif entry["source_type"] == "insurance":
                entry["source_label"] = "国家医保药品目录 2025（医保限定，优先）"
                entry["source_refs"] = [
                    "national:"
                    f"{row.get('section', '')}:"
                    f"{row.get('catalog_number', '')}:"
                    f"pdf_page_{row.get('pdf_page', '')}"
                    for row in decision_records
                ]
            else:
                if insurance_status == "unknown":
                    entry["source_label"] = (
                        "临床应用指导原则 2025 适应证"
                        "（未检出医保限定；医保状态待核对）"
                    )
                else:
                    entry["source_label"] = (
                        "临床应用指导原则 2025 适应证"
                        "（医保无限定时兜底）"
                    )
                entry["source_refs"] = [
                    "guideline:pdf_pages_"
                    f"{chapter.get('pdf_pages', ['', ''])[0]}-"
                    f"{chapter.get('pdf_pages', ['', ''])[-1]}:"
                    f"{chapter.get('chapter_title', '')}"
                    for chapter in guideline
                ]
        canonical_name = (
            (guideline[0].get("generic_name") if guideline else "")
            or (national[0].get("generic_name") if national else "")
            or (hospital[0].get("generic_name") if hospital else "")
            or canonical_key
        )
        aliases = _unique(
            [row.get("generic_name", "") for row in national]
            + [row.get("generic_name", "") for row in hospital]
            + [row.get("generic_name", "") for row in guideline]
        )
        aliases = [alias for alias in aliases if alias != canonical_name]
        drugs[entity_name] = {
            "canonical_name": canonical_name,
            "canonical_match_name": canonical_key,
            "aliases": aliases,
            # 医院药品编码与 shi_fee.med_list_codg 同语义，目录序号不混入此列表。
            "codes": _unique(row.get("drug_code", "") for row in hospital),
            "insurance_status": insurance_status,
            "entries": [entry] if entry else [],
            "effective": dict(entry) if entry else None,
            "entity_type": entity_type,
            "sources": {
                "national_catalog": national,
                "hospital_catalog": hospital,
                "guideline": guideline,
            },
        }

    # 医院原始药品通用名全等分组：同 canonical 的剂型/写法也绝不合并 codes。
    for raw_name in sorted(hospital_by_raw_name):
        canonical_key = normalize_generic_name(raw_name)
        national = sorted(
            national_by_key.get(canonical_key, []),
            key=lambda row: (
                row.get("section", ""),
                row.get("pdf_page", 0),
                row.get("catalog_number", ""),
            ),
        )
        hospital = sorted(
            hospital_by_raw_name[raw_name], key=lambda row: row.get("drug_code", "")
        )
        guideline = sorted(
            guidelines_by_key.get(canonical_key, []),
            key=lambda row: (row.get("pdf_pages", [0])[0], row.get("system", "")),
        )
        add_entity(
            raw_name,
            canonical_key,
            hospital,
            national,
            guideline,
            entity_type="hospital_raw_name",
            conflict=_source_conflict(hospital),
        )

    # 没有任何医院产品的 canonical 才建兜底实体；多剂型或限定冲突时只留证据。
    hospital_canonical_keys = {
        normalize_generic_name(raw_name) for raw_name in hospital_by_raw_name
    }
    canonical_only_keys = (
        set(national_by_key) | set(guidelines_by_key)
    ) - hospital_canonical_keys
    national_only_conflicts = 0
    for canonical_key in sorted(canonical_only_keys):
        national = sorted(
            national_by_key.get(canonical_key, []),
            key=lambda row: (
                row.get("section", ""),
                row.get("pdf_page", 0),
                row.get("catalog_number", ""),
            ),
        )
        guideline = sorted(
            guidelines_by_key.get(canonical_key, []),
            key=lambda row: (row.get("pdf_pages", [0])[0], row.get("system", "")),
        )
        conflict = _source_conflict(national, reject_multiple_variants=True)
        national_only_conflicts += int(conflict)
        entity_name = (
            (guideline[0].get("generic_name") if guideline else "")
            or (national[0].get("generic_name") if national else "")
            or canonical_key
        )
        add_entity(
            entity_name,
            canonical_key,
            [],
            national,
            guideline,
            entity_type="canonical_only",
            conflict=conflict,
        )

    source_counts = defaultdict(int)
    for drug in drugs.values():
        source = (drug.get("effective") or {}).get("source_type", "none")
        source_counts[source] += 1
    stats = {
        "drug_count": len(drugs),
        "canonical_drug_count": len(
            set(guidelines_by_key) | set(national_by_key) | hospital_canonical_keys
        ),
        "hospital_raw_name_group_count": len(hospital_by_raw_name),
        "canonical_only_drug_count": len(canonical_only_keys),
        "hospital_group_conflict_count": sum(
            _source_conflict(rows) for rows in hospital_by_raw_name.values()
        ),
        "national_only_conflict_drug_count": national_only_conflicts,
        "guideline_chapter_count": len(guideline_chapters),
        "guideline_unique_drug_count": len(guidelines_by_key),
        "national_catalog_row_count": len(scoped_national),
        "hospital_product_count": len(scoped_hospital),
        "insurance_effective_drug_count": source_counts["insurance"],
        "guideline_fallback_drug_count": source_counts["guideline"],
        "no_effective_entry_drug_count": source_counts["none"],
        "unknown_insurance_status_drug_count": sum(
            drug["insurance_status"] == "unknown" for drug in drugs.values()
        ),
        "conflict_insurance_status_drug_count": sum(
            drug["insurance_status"] == "conflict" for drug in drugs.values()
        ),
    }
    return {
        "version": KB_VERSION,
        "policy": {
            "precedence": ["insurance", "guideline"],
            "runtime_entity": "医院原始药品通用名全等组；无医院产品时才建 canonical-only 实体",
            "hospital_decision_scope": "医院 2026 产品级限定优先，只读取本原始通用名组",
            "national_catalog_role": "医院限定为空时，仅原始通用名全等的国家目录限定可参与 effective 裁决",
            "insurance_rule_type": "限适应症",
            "guideline_fallback_rule_type": "超说明书",
            "guideline_fallback_condition": "同一运行实体未提取到非空医保限定；目录属性未知时同时标记人工核对",
            "guideline_authority": "临床应用指导原则，不替代具体厂家法定药品说明书",
            "unknown_or_non_catalog_action": "有指导原则适应证则生成兜底条目并转人工核对；无指导原则则不生成生效条目",
            "canonical_only_conflict_action": "国家目录多剂型或限定冲突时不生成生效条目",
            "name_matching": "医院原始通用名全等分组；canonical 归一仅挂来源，禁止合并产品码",
        },
        "sources": {
            "national_catalog": _source_metadata(
                national_path,
                "2025 年国家基本医疗保险、生育保险和工伤保险药品目录",
                page_ranges=NATIONAL_PAGE_RANGES,
            ),
            "hospital_catalog": _source_metadata(
                hospital_path, "医院药品总库（2026-06）"
            ),
            "guideline": _source_metadata(
                guideline_path, "新型抗肿瘤药物临床应用指导原则（2025 年版）"
            ),
        },
        "stats": stats,
        "drugs": drugs,
    }


def build_review_rows(kb: dict[str, Any]) -> list[dict[str, Any]]:
    """生成全量人工核对表，风险信号集中在最后一列。"""
    rows: list[dict[str, Any]] = []
    for match_name, drug in kb["drugs"].items():
        sources = drug["sources"]
        hospital = sources["hospital_catalog"]
        decision_records = hospital or sources["national_catalog"]
        restrictions = _unique(
            row.get("restriction", "") for row in decision_records
        )
        flags = []
        if drug["insurance_status"] == "unknown":
            flags.append("医保目录属性未知/非目录")
        if drug["insurance_status"] == "conflict":
            if hospital:
                flags.append("医院原始通用名组内医保限定冲突")
            else:
                flags.append("无医院产品且国家目录存在多剂型/限定冲突")
        if not drug["entries"]:
            flags.append("无生效条目")
        if sources["guideline"] and not hospital:
            flags.append("指导原则药名未匹配医院产品")
        rows.append(
            {
                "匹配名": match_name,
                "规范通用名": drug["canonical_name"],
                "别名": " | ".join(drug["aliases"]),
                "实体类型": drug.get("entity_type", ""),
                "医保状态": drug["insurance_status"],
                "生效来源": (drug.get("effective") or {}).get("source_type", ""),
                "规则类型": (drug.get("effective") or {}).get("rule_type", ""),
                "国家目录行数": len(sources["national_catalog"]),
                "医院产品码数": len(drug["codes"]),
                "指导原则章节数": len(sources["guideline"]),
                "医保限定版本数": len(restrictions),
                "人工核对项": "；".join(flags),
            }
        )
    return rows


def write_outputs(kb: dict[str, Any], kb_path: Path, review_path: Path) -> None:
    """确定性写 JSON 和 UTF-8 BOM CSV。"""
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    kb_path.write_text(
        json.dumps(kb, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review_rows = build_review_rows(kb)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(review_rows[0]) if review_rows else ["匹配名"]
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_rows)


def build(
    national_pdf: Path = NATIONAL_PDF,
    guideline_pdf: Path = GUIDELINE_PDF,
    hospital_xls: Path = HOSPITAL_XLS,
) -> dict[str, Any]:
    """读取三份源文件并返回完整知识库，不隐式写文件。"""
    guideline = parse_guideline_pdf(guideline_pdf)
    national = parse_national_catalog_pdf(national_pdf)
    hospital = read_hospital_catalog(hospital_xls)
    return assemble_knowledge_base(
        national,
        hospital,
        guideline,
        national_path=national_pdf,
        hospital_path=hospital_xls,
        guideline_path=guideline_pdf,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--national-pdf", type=Path, default=NATIONAL_PDF)
    parser.add_argument("--guideline-pdf", type=Path, default=GUIDELINE_PDF)
    parser.add_argument("--hospital-xls", type=Path, default=HOSPITAL_XLS)
    parser.add_argument("--output", type=Path, default=OUT_KB)
    parser.add_argument("--review-output", type=Path, default=OUT_REVIEW)
    args = parser.parse_args()

    kb = build(args.national_pdf, args.guideline_pdf, args.hospital_xls)
    write_outputs(kb, args.output, args.review_output)
    stats = kb["stats"]
    print(
        f"肿瘤药知识库：{stats['drug_count']} 药，"
        f"医保限定 {stats['insurance_effective_drug_count']}，"
        f"指导原则回退 {stats['guideline_fallback_drug_count']}，"
        f"待核对 {stats['no_effective_entry_drug_count']}"
    )
    print(f"JSON: {args.output}")
    print(f"核对表: {args.review_output}")


if __name__ == "__main__":
    main()
