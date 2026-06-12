# -*- coding: utf-8 -*-
"""profiler — 按需逐列剖析 + 日期格式逐列探测 (设计 D5/D6, 纯计算不调 LLM).

GUI (/onboarding) 与 CLI (etl_import 校验/日期归一) 共用本模块.

核心:
- detect_date_format(values) -> DateProfile  逐列探测, 不设全局 dayfirst (设计 D5):
    采样整列 → 按 shape 聚类 → 找 day>12 定 dayfirst → 容忍小数秒/缺秒/纯日期
    → 识别"纯时间无日期"列 → 少数派异常 shape 标红 (表头泄漏).
- normalize_date_series(series, profile) -> Series  归一为 ISO (ETL 2.5 复用).
- profile_column(values) -> ColumnProfile  数值范围/计数/空值 + 键唯一值/重复度.
- profile_file_column(path, col, mode) 大文件 chunk 流式 + 采样近似/全量精确两档.
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

SAMPLE_ROWS = 5000  # 采样近似默认行数

# digit run → 'N', 把 30/12/2025 与 1/5/2025 归到同一 family
_DIGIT_RUN = re.compile(r"\d+")
_DATE_SLASH = re.compile(r"^\s*(\d{1,4})[/](\d{1,2})[/](\d{1,4})")
_DATE_DASH = re.compile(r"^\s*(\d{1,4})-(\d{1,2})-(\d{1,4})")


def _nshape(s: str) -> str:
    """把每段连续数字归一为 'N': '30/12/2025 11:05:44' → 'N/N/N N:N:N'."""
    return _DIGIT_RUN.sub("N", s.strip())


# ────────────── 日期探测 ──────────────

@dataclass
class DateProfile:
    is_date_column: bool          # 是否判定为日期列 (主 shape 含日期或时间)
    has_date: bool                # 含日期分量
    has_time: bool                # 含时间分量
    is_time_only: bool            # 纯时间无日期 → 不可做时间窗口分析 (设计 D5)
    dayfirst: bool                # 日在前 (D/M); ISO 年在前时为 False
    dayfirst_confident: bool      # 是否扫到 day>12 样本确证 (否则用默认猜测)
    dominant_shape: str           # 主 N-shape
    shapes: dict[str, int]        # 全部 shape 计数
    sample_size: int
    parse_rate: float             # 可解析比例 (排除空值)
    anomalies: list[str]          # 少数派无法解析的值 (表头泄漏等, UI 标红)

    @property
    def can_time_window(self) -> bool:
        return self.has_date and not self.is_time_only

    @property
    def is_ambiguous_dayfirst(self) -> bool:
        """整列扫完仍无 day>12 / 非 ISO 年在前 → dayfirst 无法判定 (设计 D8).

        True 时 MUST NOT 静默套默认 dayfirst, 交由一次性交互确认 (D/M vs M/D).
        纯时间列 / 非日期列不在此列 (它们无 D/M 歧义).
        """
        return self.has_date and not self.is_time_only and not self.dayfirst_confident


def _looks_dateish(shape: str) -> bool:
    return ("N-N-N" in shape) or ("N/N/N" in shape) or (":" in shape and "N:N" in shape)


def detect_date_format(values: Iterable[str], sample: int = SAMPLE_ROWS) -> DateProfile:
    """逐列探测日期格式. 采样整列 (非头几行), 按 shape 聚类, 找 day>12 定 dayfirst."""
    vals = [str(v).strip() for v in values if str(v).strip() and str(v).strip().lower() != "nan"]
    vals = vals[:sample]
    n = len(vals)
    if n == 0:
        return DateProfile(False, False, False, False, True, False, "", {}, 0, 0.0, [])

    shapes = Counter(_nshape(v) for v in vals)
    dateish = {s: c for s, c in shapes.items() if _looks_dateish(s)}
    if not dateish:
        # 完全不像日期列
        return DateProfile(False, False, False, False, True, False,
                           shapes.most_common(1)[0][0], dict(shapes), n, 0.0, [])

    dominant = max(dateish, key=lambda s: dateish[s])
    # 从 N-shape 判定分量 (shape 里是 'N' 不是数字, 不能用 \d 正则)
    has_time = "N:N" in dominant
    has_date = ("N-N-N" in dominant) or ("N/N/N" in dominant)
    is_time_only = has_time and not has_date

    # dayfirst: 扫描所有值的日期前两组, 找 day>12 (设计 D5)
    dayfirst = True
    dayfirst_confident = False
    if has_date:
        is_iso = False
        for v in vals:
            m = _DATE_DASH.match(v)
            if m and len(m.group(1)) == 4:  # YYYY-MM-DD → 年在前
                is_iso = True
                break
        if is_iso:
            dayfirst = False
            dayfirst_confident = True
        else:
            saw_year_first = saw_day_gt12 = saw_mid_gt12 = False
            for v in vals:
                m = _DATE_SLASH.match(v) or _DATE_DASH.match(v)
                if not m:
                    continue
                g0, g1 = m.group(1), m.group(2)
                if len(g0) == 4:  # 年在前 (斜杠 YYYY/MM/DD) → 非 dayfirst, 不能复用 saw_day_gt12
                    saw_year_first = True
                    break
                try:
                    a, b = int(g0), int(g1)
                except ValueError:
                    continue
                if a > 12:
                    saw_day_gt12 = True
                    break
                if b > 12:
                    saw_mid_gt12 = True
            if saw_year_first:
                dayfirst, dayfirst_confident = False, True
            elif saw_day_gt12:
                dayfirst, dayfirst_confident = True, True
            elif saw_mid_gt12:
                dayfirst, dayfirst_confident = False, True
            else:
                dayfirst, dayfirst_confident = True, False  # 歧义 → 默认 D/M (CN 常见)

    # 解析率 + 异常 (少数派无法解析的值)
    anomalies: list[str] = []
    parsed_ok = 0
    if is_time_only:
        # 纯时间列不做日期解析; 解析率以"符合主 shape"衡量
        for v in vals:
            if _nshape(v) == dominant:
                parsed_ok += 1
            elif not _looks_dateish(_nshape(v)):
                anomalies.append(v)
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for v in vals:
                ts = pd.to_datetime(v, dayfirst=dayfirst, errors="coerce")
                if pd.notna(ts):
                    parsed_ok += 1
                else:
                    anomalies.append(v)
    parse_rate = parsed_ok / n if n else 0.0
    # 异常去重保序, 限量
    seen: set[str] = set()
    uniq_anom = [a for a in anomalies if not (a in seen or seen.add(a))][:20]

    return DateProfile(
        is_date_column=True, has_date=has_date, has_time=has_time, is_time_only=is_time_only,
        dayfirst=dayfirst, dayfirst_confident=dayfirst_confident, dominant_shape=dominant,
        shapes=dict(shapes), sample_size=n, parse_rate=parse_rate, anomalies=uniq_anom,
    )


def normalize_date_series(series: pd.Series, profile: DateProfile | None = None,
                          dayfirst_override: bool | None = None) -> pd.Series:
    """把异构日期列归一为 ISO. 无法解析/纯时间/空 → 原样保留 (异常不丢, 设计 D5).

    dayfirst_override: 用户对歧义列 (D/M vs M/D) 的交互确认结果 (设计 D8),
    给定时优先于探测得到的 prof.dayfirst, 绝不静默用默认反转数据.
    """
    prof = profile or detect_date_format(series.tolist())
    if not prof.has_date or prof.is_time_only:
        return series  # 纯时间无日期列不归一 (避免乱补今天日期)
    dayfirst = prof.dayfirst if dayfirst_override is None else bool(dayfirst_override)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = pd.to_datetime(series, dayfirst=dayfirst, errors="coerce", format="mixed")
    fmt = "%Y-%m-%d %H:%M:%S" if prof.has_time else "%Y-%m-%d"
    iso = parsed.dt.strftime(fmt)
    # NaT (解析失败) / 原空 → 保留原值
    return iso.where(parsed.notna(), series).fillna(series)


# ────────────── 列剖析 (数值 / 键) ──────────────

@dataclass
class ColumnProfile:
    name: str
    kind: str                      # numeric / date / key / text
    count: int                     # 非空计数
    null_count: int
    null_rate: float
    # numeric
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    # key
    unique: int | None = None
    dup_rate: float | None = None  # 1 - unique/count (越低越像键)
    # date
    date: DateProfile | None = None
    exact: bool = True             # False = 采样近似


def _to_num(values: list[str]) -> pd.Series:
    return pd.to_numeric(pd.Series(values), errors="coerce")


def profile_column(values: Iterable[str], name: str = "", as_key: bool = False,
                   exact: bool = True) -> ColumnProfile:
    """对一列值剖析. as_key=True 强制按键剖析 (唯一值/重复度)."""
    raw = [str(v) for v in values]
    nonempty = [v.strip() for v in raw if v.strip() and v.strip().lower() != "nan"]
    total = len(raw)
    cnt = len(nonempty)
    null_cnt = total - cnt
    null_rate = null_cnt / total if total else 0.0

    # 日期优先探测
    dprof = detect_date_format(nonempty)
    if dprof.is_date_column and dprof.parse_rate >= 0.6:
        return ColumnProfile(name=name, kind="date", count=cnt, null_count=null_cnt,
                             null_rate=null_rate, date=dprof, exact=exact)

    # 键剖析
    uniq = len(set(nonempty))
    dup_rate = (1 - uniq / cnt) if cnt else None
    if as_key:
        return ColumnProfile(name=name, kind="key", count=cnt, null_count=null_cnt,
                             null_rate=null_rate, unique=uniq, dup_rate=dup_rate, exact=exact)

    # 数值剖析
    nums = _to_num(nonempty)
    numeric_rate = nums.notna().mean() if cnt else 0.0
    if cnt and numeric_rate >= 0.9:
        return ColumnProfile(name=name, kind="numeric", count=cnt, null_count=null_cnt,
                             null_rate=null_rate, min=float(nums.min()), max=float(nums.max()),
                             mean=float(nums.mean()), unique=uniq, dup_rate=dup_rate, exact=exact)

    return ColumnProfile(name=name, kind="text", count=cnt, null_count=null_cnt,
                         null_rate=null_rate, unique=uniq, dup_rate=dup_rate, exact=exact)


# ────────────── 大文件按需剖析 (chunk 流式) ──────────────

def sample_column(path: Path, col: str, n: int = SAMPLE_ROWS) -> list[str]:
    """采样近似: 只读前 n 行的某列 (秒出, 不全载内存)."""
    df = pd.read_csv(path, usecols=[col], dtype=str, nrows=n, low_memory=False)
    return df[col].fillna("").tolist()


def full_column(path: Path, col: str, chunksize: int = 200_000) -> list[str]:
    """全量精确: chunk 流式读某列 (设计 D6, 不全量载整文件)."""
    out: list[str] = []
    for chunk in pd.read_csv(path, usecols=[col], dtype=str, chunksize=chunksize, low_memory=False):
        out.extend(chunk[col].fillna("").tolist())
    return out


def profile_file_column(path: Path | str, col: str, mode: str = "sample",
                        as_key: bool = False, sample_rows: int = SAMPLE_ROWS) -> ColumnProfile:
    """按需剖析文件某列. mode=sample (采样近似, 默认) / full (全量精确扫描)."""
    p = Path(path)
    if mode == "full":
        values = full_column(p, col)
        prof = profile_column(values, name=col, as_key=as_key, exact=True)
    else:
        values = sample_column(p, col, sample_rows)
        prof = profile_column(values, name=col, as_key=as_key, exact=False)
    return prof
