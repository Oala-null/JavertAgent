# -*- coding: utf-8 -*-
"""search_notes — 文书检索 (目录 / 子阶段详情 / 关键词搜索).

Source: zadig_agent/src/skills/search_notes.py (snapshot @ 2026-05-08)
改动:
  - notes_df 来源改为 DataLoader.all_notes() 而非 closure 传 DataFrame.
  - v0.3 边界修复 (fix-search-notes-boundary):
    * keyword 模式默认排除告知 / 风险 / 选项框噪音段, 减少假指征命中.
    * keyword 模式逐 hit 检测 "否认 / 未见 / 排除" 前导 → 标注 [否认段].
    * keyword 模式逐 hit 检测周围 "□" → 标注 [选项框].
    * section 模式命中 0 条时探测 "见 X / 详见 X" 交叉引用 → 输出 ETL 缺失警告.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from javert.audit.runner import RETAIN_HEAD_MARKER  # 分段截断标记 (必留头部/可截明细)
from javert.data.loader import DataLoader

REQUIRES_PATIENT_ID = True

DESCRIPTION = (
    "检索患者全量文书. 无 section/keyword 时返回子阶段索引目录; "
    "传 section 返回该子阶段原文 (限 5 条, 若 section 不存在但有交叉引用则返回 ETL 缺失警告); "
    "传 keyword 全文搜索 (限 10 条, 默认排除告知/风险噪音段, 标注否认/选项框上下文)."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_id": {"type": "string", "description": "患者住院号"},
        "section": {"type": "string", "description": "子阶段名称 (从目录中选)"},
        "keyword": {"type": "string", "description": "全文搜索关键词"},
        "include_noise": {
            "type": "boolean",
            "description": "keyword 模式: 设 true 时不过滤告知/风险段 (默认 false)",
        },
    },
    "required": ["patient_id"],
}


# 默认排除的噪音 section 模式 — keyword 模式下 hit 落在这些段视为假指征
# 用 substring 包含匹配, 容纳 "手术中或手术后可能发生的并发症：" 之类带标点变体.
DEFAULT_NOISE_PATTERNS: tuple[str, ...] = (
    "可能发生的并发症",      # 手术中或手术后可能发生的并发症： (2293)
    "可能出现的意外",        # 可能出现的意外及防范措施 (2628)
    "特此告知",              # 特此告知 (2680)
    "告知内容",              # 告知内容 (1997)
    "医方告知",              # 医方告知 / 输血医方告知 (195)
    "告知同意",              # 告知同意 (55)
    "潜在风险和对策",        # (6)
    "特殊风险",              # 特殊风险和主要高位因素 (5)
    "风险及并发症",          # 含 "手术风险及并发症"
)

# 否认前导词 — 出现在 keyword 之前 N 字符内, 视为否认/反向语义
# 故意排除 "无" 因为它太歧义 (无创/无名指/无效 等)
DENIAL_HEAD_PATTERNS: tuple[str, ...] = ("否认", "未见", "排除", "未发现", "无明显")
DENIAL_WINDOW = 40  # 前导窗口字符数

# 选项框检测 — keyword 附近 30 字符内出现 □ 即视为选项框噪音
OPTION_BOX_CHARS: tuple[str, ...] = ("□", "☐", "[ ]")
OPTION_BOX_WINDOW = 30

# ETL 引用模式 — section 模式命中 0 条时, 探测这些 needle 是否在其他段出现
ETL_REF_PREFIXES: tuple[str, ...] = ("见", "详见", "参见", "见前述")


def _filter_patient(df: pd.DataFrame, patient_id: str) -> pd.DataFrame:
    if "住院号" in df.columns:
        return df[df["住院号"].astype(str).str.strip() == patient_id]
    if "bah" in df.columns:
        return df[df["bah"].astype(str).str.contains(patient_id, na=False)]
    return df[df.iloc[:, 0].astype(str).str.contains(patient_id, na=False)]


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _is_noise_section(section_name: str, patterns: tuple[str, ...]) -> bool:
    for pat in patterns:
        if pat in section_name:
            return True
    return False


def _annotate_keyword_hit(content: str, keyword: str) -> tuple[str, str]:
    """返回 (annotation, excerpt). annotation ∈ {"", "[否认段]", "[选项框]", "[否认/选项框]"}.

    annotation 标注 hit 周围上下文异常: 否认前导或选项框邻近. excerpt 取 keyword 周围 ±80 字符.
    """
    pos = content.find(keyword)
    if pos < 0:
        return "", content[:500]

    flags: list[str] = []
    # 否认前导
    head_start = max(0, pos - DENIAL_WINDOW)
    head_window = content[head_start:pos]
    if any(p in head_window for p in DENIAL_HEAD_PATTERNS):
        flags.append("否认段")

    # 选项框邻近
    box_start = max(0, pos - OPTION_BOX_WINDOW)
    box_end = min(len(content), pos + OPTION_BOX_WINDOW)
    box_window = content[box_start:box_end]
    if any(c in box_window for c in OPTION_BOX_CHARS):
        flags.append("选项框")

    annotation = f"[{'/'.join(flags)}]" if flags else ""

    # excerpt 取 keyword 周围 ±80 字符
    ex_start = max(0, pos - 80)
    ex_end = min(len(content), pos + len(keyword) + 80)
    excerpt = content[ex_start:ex_end]
    if ex_start > 0:
        excerpt = "..." + excerpt
    if ex_end < len(content):
        excerpt = excerpt + "..."
    return annotation, excerpt


def _probe_etl_references(
    patient_notes: pd.DataFrame, section_name: str, content_col: str, section_col: str | None
) -> list[dict[str, str]]:
    """探测 ETL 缺失 — 检查患者全量文书是否包含 '见 {section_name}' / '详见 {section_name}' 引用.

    返回每个引用的 (来源子阶段, snippet ±60 char) 列表; 空表示无交叉引用 (真不存在).
    """
    needles = [f"{prefix}{section_name}" for prefix in ETL_REF_PREFIXES]
    refs: list[dict[str, str]] = []
    for _, row in patient_notes.iterrows():
        content = str(row[content_col])
        for needle in needles:
            pos = content.find(needle)
            if pos < 0:
                continue
            src_sec = str(row[section_col]).strip() if section_col else "未知"
            snip_start = max(0, pos - 60)
            snip_end = min(len(content), pos + len(needle) + 60)
            snippet = content[snip_start:snip_end]
            if snip_start > 0:
                snippet = "..." + snippet
            if snip_end < len(content):
                snippet = snippet + "..."
            refs.append({"section": src_sec, "snippet": snippet, "needle": needle})
            break  # 每条 row 只记一次, 避免同 row 多 needle 重复
    return refs


def create_executor(loader: DataLoader) -> Callable[..., str]:
    """绑定 DataLoader, 返回 search_notes(patient_id, section?, keyword?, include_noise?) 函数."""

    def execute(
        patient_id: str,
        section: str | None = None,
        keyword: str | None = None,
        include_noise: bool = False,
        **_kwargs,
    ) -> str:
        notes_df = loader.all_notes()
        patient_notes = _filter_patient(notes_df, patient_id)
        if patient_notes.empty:
            return "该患者无文书记录"

        section_col = _find_col(patient_notes, ["子阶段", "sub_stage", "section"])
        content_col = _find_col(patient_notes, ["内容", "content", "text"])
        if content_col is None:
            return "文书数据缺少内容列"

        if keyword:
            mask = patient_notes[content_col].astype(str).str.contains(keyword, na=False)
            matches = patient_notes[mask]
            if matches.empty:
                return "未找到包含该关键词的文书记录"

            # 按 section 拆: 噪音段 vs 真段
            filtered_count = 0
            noise_section_set: set[str] = set()
            real_hits: list[tuple[str, str]] = []  # (section_name, content)
            for _, row in matches.iterrows():
                sec_name = str(row[section_col]).strip() if section_col else "未分类"
                content = str(row[content_col])
                if (not include_noise) and _is_noise_section(sec_name, DEFAULT_NOISE_PATTERNS):
                    filtered_count += 1
                    noise_section_set.add(sec_name)
                    continue
                real_hits.append((sec_name, content))

            if not real_hits:
                # 全部 hit 都在噪音段
                lines = [
                    f"关键词'{keyword}'仅在告知/风险噪音段命中 {filtered_count} 次, 无真实指征 hit.",
                    f"命中段: {', '.join(sorted(noise_section_set))}",
                    "提示: 这些段通常是手术风险告知/输血告知/选项框, 不构成临床指征.",
                    "若需查看原始命中, 重试时传 include_noise=true.",
                ]
                return "\n".join(lines)

            # 先算每条 hit 的标注/摘录/定位 — 需在组装前得知有无否认/选项框,
            # 以把反向语义告警挪进必留头部 (fix-drug-audit-precision: 明细被截时告警不丢).
            hit_rows: list[tuple[int, str, str, str, str]] = []  # (idx, sec, annotation, loc, excerpt)
            has_reverse_flag = False
            for i, (sec, content) in enumerate(real_hits, 1):
                annotation, excerpt = _annotate_keyword_hit(content, keyword)
                if annotation:
                    has_reverse_flag = True
                # v0.9 (前向 locator): 关键词在该段内的 char 偏移 — 让新审计的锚点精确到字符.
                # 附为机器可读 ⟨...⟩ 标记, 不改既有行结构 (纯文本契约不破坏).
                pos = content.find(keyword)
                loc = f" ⟨子阶段={sec} char={pos}⟩" if pos >= 0 else ""
                hit_rows.append((i, sec, annotation, loc, excerpt))
                if i >= 10:
                    break

            # 必留头部: 汇总行 + 反向语义告警 (告警自身带说明, 不依赖出现在明细末尾)
            head = [
                f"关键词'{keyword}'搜索结果 (共{len(matches)}条 hit; "
                f"过滤噪音段 {filtered_count} 条, 保留 {len(real_hits)} 条):",
            ]
            if has_reverse_flag:
                head.append(
                    "⚠️ 部分命中标注 [否认段] (关键词前导有 '否认/未见/排除', 反向语义) "
                    "或 [选项框] (关键词附近有 □ 选项框, 未必激活); 见下方各条标注, 都需谨慎对待."
                )
            head.append(RETAIN_HEAD_MARKER)
            head.append("")

            detail: list[str] = []
            for i, sec, annotation, loc, excerpt in hit_rows:
                detail.append(f"[{i}] 子阶段: {sec}{(' ' + annotation) if annotation else ''}{loc}")
                detail.append(f"    {excerpt}")
                detail.append("")
            if len(real_hits) >= 10:
                detail.append(f"... 共{len(real_hits)}条真段, 已显示前 10 条. 可缩小关键词")
            if filtered_count > 0:
                detail.append(
                    f"⚠️ 已过滤 {filtered_count} 条噪音段 hit ({', '.join(sorted(noise_section_set))}). "
                    "这些段是手术风险告知/输血告知等, 通常不构成真实临床指征."
                )
            return "\n".join(head + detail)

        if section:
            if section_col is None:
                return "文书数据缺少子阶段列, 无法按子阶段筛选"
            sec_notes = patient_notes[patient_notes[section_col].astype(str).str.strip() == section]
            if sec_notes.empty:
                # ETL 缺失探测 — 检查其他段是否引用此 section
                refs = _probe_etl_references(patient_notes, section, content_col, section_col)
                if refs:
                    lines = [
                        f"⚠️ ETL 数据完整性警告: 该患者无 '{section}' 子阶段, "
                        f"但其他文书有 {len(refs)} 处引用该记录:",
                        "",
                    ]
                    for i, ref in enumerate(refs, 1):
                        lines.append(f"[{i}] 来源子阶段: {ref['section']} (引用 '{ref['needle']}')")
                        lines.append(f"    {ref['snippet']}")
                        lines.append("")
                        if i >= 5:
                            lines.append(f"... 共{len(refs)}处引用, 已显示前 5 条")
                            break
                    lines.append(
                        "提示: 引用存在但 section 本身未入索引, 通常是 ETL 数据缺失 "
                        "(纸质记录单未数字化), 而非医院虚构. "
                        "建议输出 INCONCLUSIVE, 在 evidence 中标注 source='etl_warning', "
                        "locator='ETL_GAP: {0}', 不宜直接判 VIOLATION.".format(section)
                    )
                    return "\n".join(lines)
                # v0.8 — 自动 keyword fallback (section 不存在但全文搜可能有 hit).
                # 防止 LLM 看 "section 0 命中" 就停的"懒"模式, 该规则尤其针对 R203 / R015 / R105 类 ETL 场景.
                kw_mask = patient_notes[content_col].astype(str).str.contains(section, na=False, regex=False)
                kw_matches = patient_notes[kw_mask]
                if not kw_matches.empty:
                    lines = [
                        f"⚠️ section='{section}' 不存在 (未作为独立子阶段索引), 但全文有 {len(kw_matches)} 条 hit (auto keyword fallback):",
                        "",
                    ]
                    for i, (_, row) in enumerate(kw_matches.iterrows(), 1):
                        sec = str(row[section_col]).strip() if section_col else "未分类"
                        content = str(row[content_col])
                        pos = content.find(section)
                        if pos >= 0:
                            ex_start = max(0, pos - 60)
                            ex_end = min(len(content), pos + len(section) + 120)
                            excerpt = "..." + content[ex_start:ex_end] + ("..." if ex_end < len(content) else "")
                        else:
                            excerpt = content[:200]
                        lines.append(f"[{i}] 子阶段: {sec}")
                        lines.append(f"    {excerpt}")
                        lines.append("")
                        if i >= 5:
                            lines.append(f"... 共{len(kw_matches)}条 hit, 已显示前 5 条")
                            break
                    lines.append(
                        f"提示: 该 section 没有作为独立子阶段索引, 但 '{section}' 关键词在其他段出现, "
                        "通常意味 ETL 缺失或语义嵌入其他段. 若证据已足够 → 走 INCONCLUSIVE + etl_warning, "
                        f"不应 VIOLATION; 若需更多文本, 直接调 keyword='{section}'."
                    )
                    return "\n".join(lines)
                return (
                    f"未找到该子阶段 (section 不存在 + 全文也无 '{section}' 关键词 hit), "
                    "请先用无参数的方式查看目录, 或换其他关键词重试 (不要凭此直接 V — 文书不全应 INCONCLUSIVE)"
                )
            total = len(sec_notes)
            lines = [f"子阶段'{section}' (共{total}条):", ""]
            for i, (_, row) in enumerate(sec_notes.iterrows(), 1):
                text = str(row[content_col])
                lines.append(f"[{i}] {text}")
                lines.append("")
                if i >= 5:
                    if total > 5:
                        lines.append(f"共{total}条, 已显示前 5 条. 可用 keyword 进一步筛选")
                    break
            return "\n".join(lines)

        # 目录模式
        if section_col is None:
            total_chars = patient_notes[content_col].astype(str).str.len().sum()
            return f"该患者共{len(patient_notes)}条文书 (无子阶段分类), 总{total_chars}字符"

        grouped = patient_notes.groupby(patient_notes[section_col].astype(str).str.strip())
        items: list[tuple[str, int, int]] = []
        for sec_name, group in grouped:
            count = len(group)
            chars = group[content_col].astype(str).str.len().sum()
            items.append((sec_name, count, chars))
        items.sort(key=lambda x: x[2], reverse=True)

        lines = [f"文书目录 (共{len(items)}个子阶段, {len(patient_notes)}条记录):", ""]
        for sec_name, count, chars in items:
            lines.append(f"  {sec_name}({count}条, {chars}字符)")
        lines.append("")
        lines.append(f"用 section 参数取详情, 例: search_notes(patient_id=\"{patient_id}\", section=\"手术信息\")")
        return "\n".join(lines)

    return execute
