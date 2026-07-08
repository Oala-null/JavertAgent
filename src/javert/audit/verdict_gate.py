# -*- coding: utf-8 -*-
"""verdict_gate — 裁决后确定性闸层 (add-verdict-gate-layer, 设计 D1-D5).

`runner` 解析完 LLM verdict、落库**之前**调用 `apply_gate` (纯函数). gate 只会
**降级** (V→I / V→C) 并打标签, 绝不升级 (C/I 不变 V). 每次降级写可解释 `reason`.

三道硬闸 (按优先级, 先命中先返回):
  ① 文件缺失闸: 文件依赖类规则 ∧ V 证据「仅缺失」(全 etl_warning / ETL_GAP, 无正向佐证)
                → V→INCONCLUSIVE + tag「缺文书」(待线下核查)
  ② 单次闸: M2 派生 ∧ 按 exam 关键词重算净不同收费次数 ≤1 ∧ 不在例外集
                → V→CLEAN + tag「单次放过」; net 不可用 → fail-open (不降级)
  ③ conf 底线闸: conf < conf_ceiling 的 V → V→INCONCLUSIVE + tag「低置信降级」
                 (含 confidence 缺失/非法归 0; conf_floor 已弃用)

配置来自 `configs/verdict_gate.yaml` (file_dependent_rules / single_instance_violation
/ conf_ceiling; conf_floor 保留读取但不再参与判断), 不改 Rule schema.

Source: 本项目原创 (add-verdict-gate-layer).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from javert.config import get_config
from javert.data.clinical_context import PatientClinicalContext
from javert.data.fee_netting import NetItem

logger = logging.getLogger("javert.audit.verdict_gate")

VIOLATION = "VIOLATION"
INCONCLUSIVE = "INCONCLUSIVE"
CLEAN = "CLEAN"


@dataclass
class GateConfig:
    """verdict_gate.yaml 的结构化载体 (缺字段走默认)."""

    file_dependent_rules: set[str] = field(default_factory=set)
    single_instance_violation: set[str] = field(default_factory=set)
    # 临床事实闸 (fix-anesthesia-false-positive, 判据来自 shi_ss/shi_zd 病案首页)
    anesthesia_reality_rules: set[str] = field(default_factory=set)
    preop_cardiopulmonary_rules: set[str] = field(default_factory=set)
    tumor_marker_rules: set[str] = field(default_factory=set)
    imaging_confirmable_rules: set[str] = field(default_factory=set)
    unconfirmable_doc_rules: set[str] = field(default_factory=set)
    progress_sections: list[str] = field(default_factory=list)
    default_symptom_keywords: list[str] = field(default_factory=list)
    conf_floor: float = 0.70  # 弃用 (fix-drug-audit-precision 1c): ③闸改为 conf < ceiling, 不再用 floor
    conf_ceiling: float = 0.85


@dataclass
class GateOutcome:
    """gate 决策结果. changed=False 表示直通 (verdict 原样)."""

    verdict: str
    tag: str = ""
    reason: str = ""
    changed: bool = False


def load_gate_config(path: Path | None = None) -> GateConfig:
    """读 verdict_gate.yaml → GateConfig. 文件缺失 → 全默认 (gate 退化为只跑 conf 闸)."""
    cfg = get_config()
    p = path or cfg.resolve("configs/verdict_gate.yaml")
    if not p.exists():
        logger.warning("verdict_gate.yaml 不存在 (%s), 用默认配置", p)
        return GateConfig()
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        return GateConfig()
    return GateConfig(
        file_dependent_rules=set(raw.get("file_dependent_rules") or []),
        single_instance_violation=set(raw.get("single_instance_violation") or []),
        anesthesia_reality_rules=set(raw.get("anesthesia_reality_rules") or []),
        preop_cardiopulmonary_rules=set(raw.get("preop_cardiopulmonary_rules") or []),
        tumor_marker_rules=set(raw.get("tumor_marker_rules") or []),
        imaging_confirmable_rules=set(raw.get("imaging_confirmable_rules") or []),
        unconfirmable_doc_rules=set(raw.get("unconfirmable_doc_rules") or []),
        progress_sections=list(raw.get("progress_sections") or []),
        default_symptom_keywords=list(raw.get("default_symptom_keywords") or []),
        conf_floor=float(raw.get("conf_floor", 0.70)),
        conf_ceiling=float(raw.get("conf_ceiling", 0.85)),
    )


@lru_cache(maxsize=1)
def get_gate_config() -> GateConfig:
    """进程内缓存的 gate 配置."""
    return load_gate_config()


# =========================================================
# ① 文件缺失判定
# =========================================================
_ETL_GAP_RE = re.compile(r"etl_gap", re.IGNORECASE)


def _is_missing_marker(ev: dict) -> bool:
    """一条 evidence 是否为「文件缺失」标记 (etl_warning 来源 或 locator 含 ETL_GAP)."""
    src = str(ev.get("source") or "").lower()
    loc = str(ev.get("locator") or "")
    return ("etl_warning" in src) or bool(_ETL_GAP_RE.search(loc))


def _only_missing(evidence: list) -> bool:
    """V 证据是否「仅文件缺失」: 非空 ∧ 每条都是缺失标记 (无任何正向 note/fee/exam/lab 佐证).

    保守: 只要有 1 条非缺失证据 (含 unknown), 即视为有正向佐证 → 不触发该闸.
    """
    items = [ev for ev in (evidence or []) if isinstance(ev, dict)]
    if not items:
        return False
    return all(_is_missing_marker(ev) for ev in items)


# =========================================================
# ② 单次净次数重算
# =========================================================
_QUOTED_KW_RE = re.compile(r"[\"“]([^\"”]{2,30})[\"”]")


def extract_exam_keywords(rule) -> list[str]:
    """从规则取该检查的 exam 关键词 (单次闸按它匹配 fee 行).

    优先读 rule.exam_keywords 显式字段 (make-rules-code-portable, 不再依赖模板措辞);
    缺省时回退旧链: 解析 prompt_addon 里 M2 模板渲染的「检索关键词: "A" / "B" / "C"」行,
    再解析不到时回退 trigger_keywords (含指征词, 但 fee 名通常不含指征词, 多匹配只会更保守).
    """
    explicit = [k for k in (getattr(rule, "exam_keywords", []) or []) if k]
    if explicit:
        return explicit
    addon = getattr(rule, "prompt_addon", "") or ""
    for line in addon.splitlines():
        if "检索关键词" in line:
            kws = [m.strip() for m in _QUOTED_KW_RE.findall(line)]
            kws = [k for k in kws if k]
            if kws:
                return kws
    return [k for k in (getattr(rule, "trigger_keywords", []) or []) if k]


def _net_count_for_exam(
    exam_keywords: list[str], net_fee_ctx: dict[str, NetItem]
) -> int | None:
    """该检查的净不同收费次数. 无匹配 fee 项 → None (无法确认, fail-open).

    完全充退项 (is_full_refund) 视为 0 次; 取匹配项的 max(净不同收费日期数) (保守, 不叠加同日).
    """
    kws = [k for k in exam_keywords if k]
    if not kws:
        return None
    matched: list[NetItem] = []
    for item in net_fee_ctx.values():
        name = item.name or ""
        if any(kw in name for kw in kws):
            matched.append(item)
    if not matched:
        return None
    return max(
        (0 if it.is_full_refund else it.distinct_billing_dates for it in matched),
        default=0,
    )


# =========================================================
# 主入口
# =========================================================
def apply_gate(
    verdict_data: dict[str, Any],
    rule,
    net_fee_ctx: dict[str, NetItem] | None,
    gate_cfg: GateConfig,
    clinical_ctx: PatientClinicalContext | None = None,
) -> GateOutcome:
    """对一条已解析的 verdict 应用确定性闸. 纯函数, 不写库.

    Args:
        verdict_data: LLM 输出解析出的 dict (含 verdict / confidence / evidence).
        rule: Rule 对象 (用 rule_id / derived_from_template / prompt_addon / trigger_keywords).
        net_fee_ctx: 该 patient 的退费净额 {group_key: NetItem}; None = 不可用 (② fail-open).
        gate_cfg: GateConfig.
        clinical_ctx: 该 patient 的病案首页临床事实 (手术/麻醉/诊断/检查报告); None = 不可用
            (临床事实闸 ⑥⑦⑧ + 影像确认闸 fail-open).

    Returns:
        GateOutcome — changed=True 时 verdict 已降级, 附 tag + reason.

    闸优先级 (先命中先返回, 越具体越靠前):
      ⑥ 麻醉真实性 → ⑦ 术前心肺评估 → ⑧ 肿瘤标志物指征 → ① 影像/缺文书 →
      ① 旧『仅缺失』兜底 → ② 单次 → ③ conf 底线.
    """
    verdict = str(verdict_data.get("verdict") or "")
    # gate 只作用 VIOLATION (绝不升级 C/I)
    if verdict != VIOLATION:
        return GateOutcome(verdict=verdict, changed=False)

    rule_id = getattr(rule, "rule_id", "")
    evidence = verdict_data.get("evidence")
    if not isinstance(evidence, list):
        evidence = []

    # ⑥ 麻醉真实性闸 (R203 虚构全麻 / R205 全麻超次)
    # 病案首页手术表有手术 + 麻醉医师签名/全麻 → 麻醉真实开展, 麻醉记录单仅 ETL 未数字化.
    # 专家共识 (zhoulihong/wangxin, R203 ×6 "胡扯"): 不能凭"没检索到麻醉记录"就判 V.
    if rule_id in gate_cfg.anesthesia_reality_rules and clinical_ctx is not None:
        if clinical_ctx.has_anesthesia_service():
            drs = "/".join(clinical_ctx.anesthesiologists()[:3]) or "已签名"
            return GateOutcome(
                verdict=CLEAN,
                tag="麻醉真实",
                reason=(
                    f"⑥麻醉真实性: 病案首页手术表记录麻醉医师({drs}) + 手术真实开展, "
                    f"全麻收费有据 (麻醉记录单仅 ETL 未数字化, 非虚构)"
                ),
                changed=True,
            )

    # ⑦ 术前心肺评估闸 (R131 心脏彩超系列无指征)
    # 病案首页有全麻手术 → 术前心功能评估 (心脏彩超/左心功能/TDI) 属常规必要 → 有指征.
    # 专家共识 (R131 ×8): "手术患者术前需排除心肺禁忌, 有检查必要性".
    if rule_id in gate_cfg.preop_cardiopulmonary_rules and clinical_ctx is not None:
        if clinical_ctx.has_general_anesthesia():
            ops = "/".join(clinical_ctx.general_anesthesia_surgeries()[:2]) or "全麻手术"
            return GateOutcome(
                verdict=CLEAN,
                tag="术前心肺评估",
                reason=(
                    f"⑦术前心肺评估: 病案首页有全麻手术({ops}), 术前心功能评估有指征 (排除心肺禁忌)"
                ),
                changed=True,
            )

    # ⑧ 肿瘤标志物指征闸 (R153/R154/R156 肿瘤标志物无指征)
    # 病案首页诊断含肿瘤/恶性 → 查肿瘤标志物属正常诊疗 (排转移/分期/随访) → 有指征.
    # 专家共识 (13C): "肿瘤病人查肿瘤标志物属于正常诊疗行为, 不予认定".
    if rule_id in gate_cfg.tumor_marker_rules and clinical_ctx is not None:
        if clinical_ctx.has_tumor():
            dxs = "/".join(clinical_ctx.tumor_diagnoses()[:2]) or "肿瘤诊断"
            return GateOutcome(
                verdict=CLEAN,
                tag="肿瘤标志物指征",
                reason=(
                    f"⑧肿瘤标志物指征: 病案首页诊断含肿瘤({dxs}), 查标志物排转移/分期属正常诊疗"
                ),
                changed=True,
            )

    # ① 影像可确认闸 (R103/R105 虚构影像服务)
    # 检查报告表有该患者报告 → 影像服务真实开展 → CLEAN; 报告不在 → 待 PACS 核查 → I.
    # 专家共识: "未见检查报告单 → 应定疑似, 线下核查" (绝不凭 case_notes 缺报告就判虚构 V).
    if rule_id in gate_cfg.imaging_confirmable_rules and clinical_ctx is not None:
        has_report = clinical_ctx.has_imaging_report()
        if has_report is True:
            return GateOutcome(
                verdict=CLEAN,
                tag="影像服务已确认",
                reason="①影像确认: 检查报告表存在该患者影像报告, 服务真实开展 (非虚构)",
                changed=True,
            )
        if has_report is False:
            return GateOutcome(
                verdict=INCONCLUSIVE,
                tag="缺影像报告",
                reason="①缺影像报告: 检查报告表无该患者报告 (可能在 PACS 未数字化), 待线下核查",
                changed=True,
            )
        # has_report is None (exam_loader 未注入) → fail-open, 落到旧 ① 兜底

    # ① 不可确认文书闸 (R015/R026/R034/R080/R112/R165/R212/R224/R278/R014 等)
    # 这些规则的认定依赖 Javert 数据看不到的文书 (医嘱单/耗材条码/评估量表/病理报告/
    # PACU 记录/监测记录/部位时相). 单病历无法证实 → V 降 I (专家口径"未见…报告单").
    if rule_id in gate_cfg.unconfirmable_doc_rules:
        return GateOutcome(
            verdict=INCONCLUSIVE,
            tag="缺文书",
            reason="①缺文书: 认定依赖院内文书(医嘱单/条码/量表/病理/PACU/监测记录等), Javert 数据不可见, 待线下核查",
            changed=True,
        )

    # ① 文件缺失闸 (旧兜底: 证据仅为缺失标记)
    if rule_id in gate_cfg.file_dependent_rules and _only_missing(evidence):
        return GateOutcome(
            verdict=INCONCLUSIVE,
            tag="缺文书",
            reason="①文件缺失: 证据仅为文件缺失标记, 无正向佐证, 待线下核查",
            changed=True,
        )

    # ② 单次闸 (M2 派生, 非例外集)
    if (
        getattr(rule, "derived_from_template", None) == "M2"
        and rule_id not in gate_cfg.single_instance_violation
    ):
        if net_fee_ctx is None:
            # net 不可用 → fail-open (不因数据缺失误降)
            logger.debug("单次闸 fail-open: net_fee_ctx 不可用 rule=%s", rule_id)
        else:
            count = _net_count_for_exam(extract_exam_keywords(rule), net_fee_ctx)
            if count is not None and count <= 1:
                return GateOutcome(
                    verdict=CLEAN,
                    tag="单次放过",
                    reason=f"②单次: 该检查净不同收费次数={count} (≤1), 单次不认定过度检查",
                    changed=True,
                )

    # ③ conf 底线闸: conf < ceiling 的 V 一律降 I (含 confidence 缺失/非法归 0).
    # 原 [conf_floor, ceiling) 区间放过了 floor 以下的低置信 V (fix-drug-audit-precision 1c):
    # floor 以下更该降不是更不该降. conf_floor 字段已弃用 (保留只为 yaml schema 兼容).
    try:
        conf = float(verdict_data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if conf < gate_cfg.conf_ceiling:
        return GateOutcome(
            verdict=INCONCLUSIVE,
            tag="低置信降级",
            reason=f"③低置信: conf={conf:.2f} < ceiling {gate_cfg.conf_ceiling}, 强制改判不明",
            changed=True,
        )

    return GateOutcome(verdict=verdict, changed=False)
