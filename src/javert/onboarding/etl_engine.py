# -*- coding: utf-8 -*-
"""etl_engine — manifest 驱动的 N 表 ETL 引擎 (设计 D1).

scripts/etl_import.py (CLI) 与 /onboarding 路由 (GUI) 共用本引擎.
遍历 schema_manifest 的 tabular spoke 转换, 而非硬编码 4 表;
现有 4 表输出 MUST 与旧 transform_* 逐列一致 (回归基线见 tests/test_etl_engine.py).

Layer1: 通用字段拷贝 + notes 段落拆分 + 复合/裸号键 + synth_seq + const 列.
Layer2 (本模块内扩展): 桥表键归一 (normalize_keys) + 逐列日期 ISO 归一 (normalize_dates).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from javert.onboarding.manifest_loader import Manifest, Spoke

log = logging.getLogger("javert.onboarding.etl_engine")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

_SECTION_RE = re.compile(r"【([^】]+)】")


# ────────────── 共享小工具 ──────────────

def read_source(file_path: str | Path) -> pd.DataFrame:
    """读外部源文件 (csv/xls/xlsx 按扩展名自动选), 全列 str."""
    p = Path(file_path)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype=str, low_memory=False)
    return pd.read_excel(p, dtype=str)


def _col(df: pd.DataFrame, col_map: dict, key: str) -> pd.Series:
    """按语义 key 从外部 df 取映射列; 列缺失返回等长空 Series."""
    ext_name = col_map.get(key, "")
    if ext_name and ext_name in df.columns:
        return df[ext_name].fillna("")
    return pd.Series([""] * len(df), dtype=str)


def make_compound_id(pids: pd.Series, hospital_code: str) -> pd.Series:
    """裸号 → '{hospital_code}-裸号 ' (尾部空格, 兼容 get_fees 的 str.contains)."""
    return hospital_code + "-" + pids.str.strip() + " "


def split_sections(text: str) -> list[tuple[str, str]]:
    """'【主诉】xxx【现病史】yyy' → [('主诉','xxx'), ('现病史','yyy')]; 无标记返回 []."""
    markers = list(_SECTION_RE.finditer(text))
    if not markers:
        return []
    parts: list[tuple[str, str]] = []
    for i, m in enumerate(markers):
        name = m.group(1)
        start = m.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        content = text[start:end].strip()
        if content:
            parts.append((name, content))
    return parts


# ────────────── 桥表键归一 (设计 D3/D4) ──────────────

@dataclass
class KeyNorm:
    """患者键归一策略 (来自 mapping 的 key_mode/bridge, 默认 synth = Layer1 行为).

    synth  源键是裸号 → compound 合成 {hc}-裸号 / bare 直接 strip (szx 真数据).
    asis   源键已是落盘形态 → 原样存 (song 的 fee/zd/ss bah 已含裸号).
    bridge 经桥表把源键 → canonical 裸号 (song 文书 medcasno→psn_no).
    """
    mode: str = "synth"
    bridge_cfg: dict | None = None
    miss_count: int = 0


def build_crosswalk(bridge_cfg: dict) -> dict[str, str]:
    """桥表构 源键→canonical 裸号 交叉表 (设计 D3)."""
    df = read_source(bridge_cfg["file"])
    sc, cc = bridge_cfg["source_col"], bridge_cfg["canonical_col"]
    if sc not in df.columns or cc not in df.columns:
        raise KeyError(f"桥表 {bridge_cfg['file']} 缺列 {sc}/{cc}")
    cw: dict[str, str] = {}
    for s, c in zip(df[sc].astype(str).str.strip(), df[cc].astype(str).str.strip()):
        if s and c and s.lower() != "nan":
            cw.setdefault(s, c)
    return cw


def resolve_pids(pids: pd.Series, spoke: Spoke, hospital_code: str, kn: KeyNorm) -> pd.Series:
    """把源患者键解析为该 spoke 的落盘键形态; bridge 归一失败保留原值 + 计数 (不静默并入)."""
    stripped = pids.str.strip()
    if kn.mode == "asis":
        return stripped
    if kn.mode == "bridge" and kn.bridge_cfg:
        cw = build_crosswalk(kn.bridge_cfg)
        mapped = stripped.map(lambda v: cw.get(v))
        kn.miss_count = int(mapped.isna().sum())
        bare = mapped.where(mapped.notna(), stripped)  # 归一失败保留原源键 (透明)
        if spoke.id_form == "compound":
            return make_compound_id(bare, hospital_code)
        return bare
    # synth (默认, 复现 Layer1)
    if spoke.id_form == "compound":
        return make_compound_id(pids, hospital_code)
    return stripped


# ────────────── 转换 (manifest 驱动) ──────────────

def transform_notes_rows(src: pd.DataFrame, spoke: Spoke, col_map: dict,
                         kn: KeyNorm | None = None) -> pd.DataFrame:
    """文书专属行展开转换 (内容含【段落】时按标记拆行). 复现旧 transform_notes 行为."""
    kn = kn or KeyNorm()
    pid_col = col_map.get("patient_id", "")
    section_col = col_map.get("section", "")
    content_col = col_map.get("content", "")
    doc_name_col = col_map.get("doc_name", "")
    time_col = col_map.get("time", "")
    source_const = spoke.const_columns.get("来源文件", "etl_import")

    # bridge 归一: 先构 源键→canonical 裸号 交叉表
    crosswalk: dict[str, str] | None = None
    if kn.mode == "bridge" and kn.bridge_cfg:
        crosswalk = build_crosswalk(kn.bridge_cfg)

    rows: list[dict] = []
    for _, row in src.iterrows():
        raw_pid = str(row.get(pid_col, "") or "").strip() if pid_col else ""
        if crosswalk is not None and raw_pid:
            mapped = crosswalk.get(raw_pid)
            if mapped is None:
                kn.miss_count += 1
                pid = raw_pid  # 归一失败保留原值 (透明)
            else:
                pid = mapped
        else:
            pid = raw_pid
        doc_name = (
            str(row.get(doc_name_col, "") or "").strip()
            if doc_name_col and doc_name_col in src.columns else ""
        )
        section = str(row.get(section_col, "") or "").strip() if section_col else ""
        content = str(row.get(content_col, "") or "").strip() if content_col else ""
        time_val = (
            str(row.get(time_col, "") or "").strip()
            if time_col and time_col in src.columns else ""
        )

        parts = split_sections(content)
        if parts:
            for sub_section, sub_content in parts:
                rows.append({
                    "住院号": pid, "事件时间": time_val,
                    "阶段": section or doc_name, "子阶段": sub_section,
                    "内容": sub_content, "来源文件": source_const,
                })
        else:
            rows.append({
                "住院号": pid, "事件时间": time_val,
                "阶段": doc_name, "子阶段": section,
                "内容": content, "来源文件": source_const,
            })
    return pd.DataFrame(rows, columns=spoke.output_schema)


def transform_tabular(src: pd.DataFrame, spoke: Spoke, col_map: dict, hospital_code: str,
                      kn: KeyNorm | None = None) -> pd.DataFrame:
    """通用 tabular 转换 — 复现旧 transform_fees/diagnoses/surgeries 行为.

    1. 患者键: resolve_pids (synth/asis/bridge).
    2. 每个非 patient_id 字段 → 拷到其 targets 列 (一字段可多 target, 如 diag_name).
    3. const_columns (含 $hospital_code 注入), synth_seq (1..N).
    """
    kn = kn or KeyNorm()
    n = len(src)
    out = pd.DataFrame({c: [""] * n for c in spoke.output_schema})

    # 患者键
    pids = _col(src, col_map, "patient_id")
    out[spoke.id_column] = resolve_pids(pids, spoke, hospital_code, kn)

    # 其余字段 → targets
    for f in spoke.fields:
        if f.key == "patient_id":
            continue
        val = _col(src, col_map, f.key)
        for t in f.targets:
            out[t] = val

    # const 列
    for c, v in spoke.const_columns.items():
        out[c] = hospital_code if v == "$hospital_code" else v

    # 自动序号列
    if spoke.synth_seq:
        out[spoke.synth_seq] = range(1, n + 1)

    return out


def transform_spoke(src: pd.DataFrame, spoke: Spoke, col_map: dict, hospital_code: str,
                    kn: KeyNorm | None = None) -> pd.DataFrame:
    """按 spoke 声明分派转换."""
    if spoke.split_sections:
        return transform_notes_rows(src, spoke, col_map, kn)
    return transform_tabular(src, spoke, col_map, hospital_code, kn)


def normalize_dates_in_df(df: pd.DataFrame, spoke: Spoke,
                          date_decisions: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """对 spoke 的 is_date 字段目标列逐列日期 ISO 归一 (设计 D5, ETL 2.5).

    纯时间无日期列跳过并 note; 返回 (新 df, 各列归一说明).
    date_decisions: 用户对歧义列的交互确认 {f"{spoke.key}.{col}": dayfirst} (设计 D8),
    命中时优先于探测默认, 绝不静默反转.
    """
    from javert.onboarding.profiler import detect_date_format, normalize_date_series

    decisions = date_decisions or {}
    notes: list[str] = []
    date_targets: set[str] = set()
    for f in spoke.fields:
        if f.is_date:
            date_targets.update(f.targets)
    for col in date_targets:
        if col not in df.columns:
            continue
        prof = detect_date_format(df[col].tolist())
        if not prof.is_date_column:
            continue
        if prof.is_time_only:
            notes.append(f"{spoke.key}.{col}: 纯时间无日期, 跳过归一 (不可做时间窗口)")
            continue
        override = decisions.get(f"{spoke.key}.{col}")
        df[col] = normalize_date_series(df[col], prof, dayfirst_override=override)
        if override is not None:
            notes.append(f"{spoke.key}.{col}: 归一 ISO, dayfirst={bool(override)} (用户确认), 解析率 {prof.parse_rate*100:.0f}%")
        else:
            conf = "" if prof.dayfirst_confident else " (dayfirst 歧义, 默认 D/M)"
            notes.append(f"{spoke.key}.{col}: 归一 ISO, dayfirst={prof.dayfirst}{conf}, 解析率 {prof.parse_rate*100:.0f}%")
    return df, notes


# ────────────── 校验 + 主流程 ──────────────

def validate_mapping(mapping: dict, manifest: Manifest) -> list[str]:
    """校验 mapping 中每个出现的 spoke 节: file 存在 + 必填语义键已配 (从 manifest 读必填)."""
    errors: list[str] = []
    present = [k for k in manifest.tabular_spokes() if isinstance(mapping.get(k), dict)]
    if not present:
        errors.append("mapping 未声明任何 manifest tabular spoke 节 (fees/notes/...)")
        return errors
    for key in present:
        spoke = manifest.spoke(key)
        section = mapping[key]
        if not section.get("file"):
            errors.append(f"{key}.file 未配置")
        col_map = section.get("columns", {}) or {}
        for req in spoke.required_keys:
            if not col_map.get(req):
                errors.append(f"{key}.columns.{req} 未配置 (必填, 来自 manifest)")
    return errors


def validate_columns(df: pd.DataFrame, spoke: Spoke, col_map: dict) -> list[str]:
    """逐映射列检查是否在源文件存在; 必填缺失标 [必填]."""
    warnings: list[str] = []
    req = set(spoke.required_keys)
    for key, ext_name in col_map.items():
        if ext_name and ext_name not in df.columns:
            tag = "[必填]" if key in req else "[可选]"
            warnings.append(f"  {spoke.key}: 映射列 '{ext_name}' ({key}) 在文件中不存在 {tag}")
    return warnings


@dataclass
class SpokeResult:
    key: str
    name: str
    output_file: str | None
    df: pd.DataFrame
    patient_count: int
    warnings: list[str] = field(default_factory=list)
    bridge_miss: int = 0
    date_notes: list[str] = field(default_factory=list)


@dataclass
class ETLResult:
    spokes: list[SpokeResult]
    hospital_code: str
    fatal_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fatal_errors


def _patient_count(df: pd.DataFrame, spoke: Spoke) -> int:
    col = spoke.id_column
    if col not in df.columns:
        return 0
    if spoke.id_form == "compound":
        pids = {v.split("-")[-1].strip() for v in df[col].unique() if str(v).strip()}
    else:
        pids = {str(v).strip() for v in df[col].unique() if str(v).strip()}
    return len(pids)


def run_etl(mapping: dict, manifest: Manifest) -> ETLResult:
    """遍历 manifest tabular spoke 转换 (只处理 mapping 中出现的节). 不写盘 — 返回 DataFrame.

    Layer2 行为按 mapping 键开启 (默认关 → 等价 Layer1, 回归不破):
      - 各 spoke 节 key_mode (synth/asis/bridge) + bridge {file,source_col,canonical_col}
      - 顶层 normalize_dates: true → 逐列日期 ISO 归一

    必填语义键缺失 / 源文件缺列(必填) → fatal_errors (调用方决定是否中止).
    """
    fatal = validate_mapping(mapping, manifest)
    if fatal:
        return ETLResult(spokes=[], hospital_code="", fatal_errors=fatal)

    hospital_code = mapping.get("hospital_code", "H99999999999")
    date_norm = bool(mapping.get("normalize_dates", False))
    date_decisions = mapping.get("date_decisions") or {}
    present = [k for k in manifest.tabular_spokes() if isinstance(mapping.get(k), dict)]
    results: list[SpokeResult] = []
    all_fatal: list[str] = []

    for key in present:
        spoke = manifest.spoke(key)
        section = mapping[key]
        col_map = section.get("columns", {}) or {}
        try:
            src = read_source(section["file"])
        except FileNotFoundError as e:
            all_fatal.append(str(e))
            continue

        warnings = validate_columns(src, spoke, col_map)
        if any("[必填]" in w for w in warnings):
            all_fatal.append(f"{key}: 存在必填列缺失")

        kn = KeyNorm(mode=(section.get("key_mode") or "synth").lower(),
                     bridge_cfg=section.get("bridge"))
        try:
            out_df = transform_spoke(src, spoke, col_map, hospital_code, kn)
        except (KeyError, FileNotFoundError) as e:
            all_fatal.append(f"{key}: 桥表归一失败 — {e}")
            continue

        date_notes: list[str] = []
        if date_norm:
            out_df, date_notes = normalize_dates_in_df(out_df, spoke, date_decisions)

        if kn.miss_count:
            warnings.append(
                f"  {key}: 桥表归一失败 {kn.miss_count} 行 (源键不在桥表, 保留原值不并入)"
            )

        results.append(SpokeResult(
            key=key, name=spoke.name, output_file=spoke.output_file,
            df=out_df, patient_count=_patient_count(out_df, spoke), warnings=warnings,
            bridge_miss=kn.miss_count, date_notes=date_notes,
        ))

    return ETLResult(spokes=results, hospital_code=hospital_code, fatal_errors=all_fatal)
