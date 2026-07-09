# -*- coding: utf-8 -*-
"""verdict_gate 单测 (add-verdict-gate-layer, 任务 1.6).

覆盖:
  ① 文件缺失闸: 仅缺失证据降 I / 有正向佐证不降 / 非文件依赖类不误降
  ② 单次闸: 净次数 1 降 C / 全退 0 降 C / 例外穿透 V / net 缺失 fail-open / ≥2 不降
  ③ conf 底线闸: conf 0.80 V→I
  直通: 非 V 不动 / 不命中任何闸直通
"""

from __future__ import annotations

from javert.audit.rule import Rule
from javert.audit.verdict_gate import GateConfig, apply_gate
from javert.data.clinical_context import PatientClinicalContext, Surgery
from javert.data.fee_netting import NetItem


def _gate_cfg() -> GateConfig:
    return GateConfig(
        file_dependent_rules={"R103", "R203"},
        single_instance_violation={"R156"},
        conf_floor=0.70,
        conf_ceiling=0.85,
    )


def _clinical_cfg() -> GateConfig:
    """含临床事实闸 (⑥⑦⑧ + 影像/缺文书) 的配置, 用于新闸单测."""
    return GateConfig(
        single_instance_violation={"R156"},
        anesthesia_reality_rules={"R203", "R205"},
        preop_cardiopulmonary_rules={"R131"},
        tumor_marker_rules={"R153", "R154", "R156"},
        imaging_confirmable_rules={"R103", "R105"},
        unconfirmable_doc_rules={"R034", "R165", "R224"},
        conf_floor=0.70,
        conf_ceiling=0.85,
    )


class _FakeExamLoader:
    """has_imaging_report() 测试桩: 返回固定行数."""

    def __init__(self, rows: int):
        self._rows = rows

    def get_examinations(self, patient_id: str):
        return [{"r": i} for i in range(self._rows)]


def _ctx(*, surgeries=None, diagnoses=None, exam_rows=None) -> PatientClinicalContext:
    return PatientClinicalContext(
        patient_id="TEST",
        surgeries=surgeries or [],
        diagnoses=diagnoses or [],
        exam_loader=_FakeExamLoader(exam_rows) if exam_rows is not None else None,
    )


_VD_V = {"verdict": "VIOLATION", "confidence": 0.95,
         "evidence": [{"source": "fee", "locator": "x", "text": "y"}]}


# ========== ⑥ 麻醉真实性闸 ==========
def test_anesthesia_surgery_with_anesthesiologist_downgrades_to_clean():
    ctx = _ctx(surgeries=[Surgery(name="腹腔镜肾上腺病损切除术", anst_way="1", anst_dr="沈兵")])
    out = apply_gate(dict(_VD_V), _rule("R203", template="M5"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "CLEAN" and out.tag == "麻醉真实"


def test_anesthesia_local_only_anesthesiologist_still_clean():
    # anst_way=9 但有麻醉医师签名 → 麻醉服务真实 (J61556 式) → CLEAN
    ctx = _ctx(surgeries=[Surgery(name="腰椎穿刺术", anst_way="9", anst_dr="葛宇星")])
    out = apply_gate(dict(_VD_V), _rule("R205", template="M4"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "CLEAN"


def test_anesthesia_no_surgery_not_cleared():
    # 无任何手术 + 无麻醉医师 → 真·虚构可能, 闸⑥不降
    ctx = _ctx(surgeries=[])
    out = apply_gate(dict(_VD_V), _rule("R203", template="M5"), None, _clinical_cfg(), ctx)
    assert not out.changed and out.verdict == "VIOLATION"


def test_anesthesia_clinical_ctx_none_fail_open():
    out = apply_gate(dict(_VD_V), _rule("R203", template="M5"), None, _clinical_cfg(), None)
    assert not out.changed


# ========== ⑦ 术前心肺评估闸 ==========
def test_preop_ga_surgery_downgrades_to_clean():
    ctx = _ctx(surgeries=[Surgery(name="腹腔镜下肝段切除术", anst_way="1", anst_dr="李俊")])
    out = apply_gate(dict(_VD_V), _rule("R131", template="M2"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "CLEAN" and out.tag == "术前心肺评估"


def test_preop_no_ga_surgery_not_cleared():
    # 只有局麻操作 (anst_way=9) → 非全麻手术 → 闸⑦不降, 保留 LLM 判定 (真·无指征心脏彩超)
    ctx = _ctx(surgeries=[Surgery(name="腰椎穿刺术", anst_way="9", anst_dr="葛宇星")])
    out = apply_gate(dict(_VD_V), _rule("R131", template="M2"), None, _clinical_cfg(), ctx)
    assert not out.changed and out.verdict == "VIOLATION"


# ========== ⑧ 肿瘤标志物指征闸 ==========
def test_tumor_marker_with_malignancy_downgrades_to_clean():
    ctx = _ctx(diagnoses=["弥漫大B细胞淋巴瘤", "高血压2级"])
    out = apply_gate(dict(_VD_V), _rule("R153", template="M2"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "CLEAN" and out.tag == "肿瘤标志物指征"


def test_tumor_marker_uncertain_behavior_tumor_clean():
    # 肾上腺肿瘤/动态未定肿瘤 (J29579 式) → 仍算肿瘤患者 → CLEAN
    ctx = _ctx(diagnoses=["肾上腺肿瘤", "动态未定肿瘤"])
    out = apply_gate(dict(_VD_V), _rule("R154", template="M2"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "CLEAN"


def test_tumor_marker_no_tumor_passthrough_to_single_instance():
    # 无肿瘤诊断 + R156 在单次例外集 → ⑧不降, ②单次例外穿透保 V
    ctx = _ctx(diagnoses=["2型糖尿病", "肝血管瘤"])
    rule = _rule("R156", template="M2", prompt='检索关键词: "PSA"')
    net = _net("PSA前列腺特异性抗原", dates=1)
    out = apply_gate(dict(_VD_V), rule, net, _clinical_cfg(), ctx)
    assert not out.changed and out.verdict == "VIOLATION"


# ========== ① 影像可确认闸 ==========
def test_imaging_confirmable_with_report_clean():
    ctx = _ctx(exam_rows=5)
    out = apply_gate(dict(_VD_V), _rule("R103", template="M5"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "CLEAN" and out.tag == "影像服务已确认"


def test_imaging_confirmable_no_report_inconclusive():
    ctx = _ctx(exam_rows=0)
    out = apply_gate(dict(_VD_V), _rule("R105", template="M5"), None, _clinical_cfg(), ctx)
    assert out.changed and out.verdict == "INCONCLUSIVE" and out.tag == "缺影像报告"


def test_imaging_confirmable_no_loader_fail_open():
    # exam_loader 未注入 → has_imaging_report()=None → 影像闸 fail-open (不降)
    ctx = _ctx(exam_rows=None)
    out = apply_gate(dict(_VD_V), _rule("R103", template="M5"), None, _clinical_cfg(), ctx)
    assert not out.changed


# ========== ① 不可确认文书闸 ==========
def test_unconfirmable_doc_downgrades_to_inconclusive():
    out = apply_gate(dict(_VD_V), _rule("R034", template="M7"), None, _clinical_cfg(), _ctx())
    assert out.changed and out.verdict == "INCONCLUSIVE" and out.tag == "缺文书"


def test_unconfirmable_doc_fires_even_without_clinical_ctx():
    # 不可确认文书闸不依赖 clinical_ctx → 即便 None 也降 I
    out = apply_gate(dict(_VD_V), _rule("R165", template="M4"), None, _clinical_cfg(), None)
    assert out.changed and out.verdict == "INCONCLUSIVE"


def _rule(rule_id: str, *, template=None, prompt="", triggers=None) -> Rule:
    return Rule(
        rule_id=rule_id,
        domain="测试",
        violation_type="过度检查",
        question="q",
        derived_from_template=template,
        prompt_addon=prompt,
        trigger_keywords=triggers or [],
    )


def _net(name: str, *, dates: int, net_qty: float = 1.0) -> dict:
    return {name: NetItem(name=name, code="", net_qty=net_qty, distinct_billing_dates=dates, has_refund=False)}


# ========== ① 文件缺失闸 ==========
def test_file_missing_only_etl_warning_downgrades_to_inconclusive():
    vd = {
        "verdict": "VIOLATION",
        "confidence": 0.95,
        "evidence": [{"source": "etl_warning", "locator": "ETL_GAP: 麻醉记录", "text": "未检索到"}],
    }
    out = apply_gate(vd, _rule("R203", template="M5"), None, _gate_cfg())
    assert out.changed
    assert out.verdict == "INCONCLUSIVE"
    assert out.tag == "缺文书"


def test_file_missing_with_positive_evidence_not_downgraded():
    vd = {
        "verdict": "VIOLATION",
        "confidence": 0.95,
        "evidence": [
            {"source": "etl_warning", "locator": "ETL_GAP: 影像报告", "text": "未检索到"},
            {"source": "fee", "locator": "CT 平扫", "text": "收费 3 次"},
        ],
    }
    out = apply_gate(vd, _rule("R103", template="M5"), None, _gate_cfg())
    assert not out.changed
    assert out.verdict == "VIOLATION"


def test_non_file_dependent_rule_not_downgraded_even_if_sparse():
    vd = {
        "verdict": "VIOLATION",
        "confidence": 0.95,
        "evidence": [{"source": "etl_warning", "locator": "ETL_GAP: x", "text": "未检索到"}],
    }
    # R999 不在 file_dependent_rules → 文件缺失闸不作用 (conf 高也不触发 conf 闸)
    out = apply_gate(vd, _rule("R999"), None, _gate_cfg())
    assert not out.changed


# ========== ② 单次闸 ==========
def test_single_instance_net_count_one_downgrades_to_clean():
    vd = {
        "verdict": "VIOLATION",
        "confidence": 0.95,
        "evidence": [{"source": "fee", "locator": "肌红蛋白测定", "text": "1 次"}],
    }
    rule = _rule("R141", template="M2", prompt='检索关键词: "肌红蛋白" / "Mb"')
    net = _net("肌红蛋白测定", dates=1)
    out = apply_gate(vd, rule, net, _gate_cfg())
    assert out.changed
    assert out.verdict == "CLEAN"
    assert out.tag == "单次放过"


def test_single_instance_full_refund_counts_as_zero_downgrades():
    vd = {"verdict": "VIOLATION", "confidence": 0.95, "evidence": []}
    rule = _rule("R141", template="M2", prompt='检索关键词: "地佐辛"')
    # 完全充退: net_qty<=0 → is_full_refund True, 视为 0 次
    net = {"地佐辛": NetItem(name="地佐辛注射液", code="", net_qty=0.0, distinct_billing_dates=5, has_refund=True)}
    out = apply_gate(vd, rule, net, _gate_cfg())
    assert out.changed
    assert out.verdict == "CLEAN"
    assert out.tag == "单次放过"


def test_single_instance_exception_passes_through_violation():
    vd = {
        "verdict": "VIOLATION",
        "confidence": 0.95,
        "evidence": [{"source": "fee", "locator": "PSA", "text": "1 次"}],
    }
    rule = _rule("R156", template="M2", prompt='检索关键词: "PSA" / "前列腺特异性抗原"')
    net = _net("PSA前列腺特异性抗原", dates=1)
    out = apply_gate(vd, rule, net, _gate_cfg())
    # R156 在例外集 → 单次也保留 V (conf 0.95 不触发 conf 闸)
    assert not out.changed
    assert out.verdict == "VIOLATION"


def test_single_instance_net_unavailable_fail_open():
    vd = {
        "verdict": "VIOLATION",
        "confidence": 0.95,
        "evidence": [{"source": "fee", "locator": "肌红蛋白", "text": "1 次"}],
    }
    rule = _rule("R141", template="M2", prompt='检索关键词: "肌红蛋白"')
    out = apply_gate(vd, rule, None, _gate_cfg())  # net_fee_ctx=None
    assert not out.changed
    assert out.verdict == "VIOLATION"


def test_single_instance_two_dates_not_downgraded():
    vd = {"verdict": "VIOLATION", "confidence": 0.95, "evidence": []}
    rule = _rule("R141", template="M2", prompt='检索关键词: "肌红蛋白"')
    net = _net("肌红蛋白测定", dates=3)
    out = apply_gate(vd, rule, net, _gate_cfg())
    assert not out.changed


def test_single_instance_no_matching_fee_fail_open():
    vd = {"verdict": "VIOLATION", "confidence": 0.95, "evidence": []}
    rule = _rule("R141", template="M2", prompt='检索关键词: "肌红蛋白"')
    net = _net("完全无关项目", dates=1)  # 关键词匹配不到
    out = apply_gate(vd, rule, net, _gate_cfg())
    assert not out.changed  # 无匹配 fee → 无法确认 → 不降


# ========== ③ conf 底线闸 ==========
def test_conf_floor_downgrades_low_confidence_violation():
    vd = {"verdict": "VIOLATION", "confidence": 0.80, "evidence": [{"source": "fee", "locator": "x", "text": "y"}]}
    out = apply_gate(vd, _rule("R999"), None, _gate_cfg())
    assert out.changed
    assert out.verdict == "INCONCLUSIVE"
    assert out.tag == "低置信降级"


def test_conf_at_ceiling_not_downgraded():
    vd = {"verdict": "VIOLATION", "confidence": 0.85, "evidence": [{"source": "fee", "locator": "x", "text": "y"}]}
    out = apply_gate(vd, _rule("R999"), None, _gate_cfg())
    assert not out.changed


def test_conf_below_floor_now_downgraded():
    # fix-drug-audit-precision 1c: ③闸改 conf < ceiling, floor 以下的低置信 V 也降 I
    vd = {"verdict": "VIOLATION", "confidence": 0.50, "evidence": [{"source": "fee", "locator": "x", "text": "y"}]}
    out = apply_gate(vd, _rule("R999"), None, _gate_cfg())
    assert out.changed
    assert out.verdict == "INCONCLUSIVE"
    assert out.tag == "低置信降级"


def test_conf_missing_归零_downgraded():
    # confidence 字段缺失 → 归 0 → conf < ceiling → 降 I (消灭 "VIOLATION conf 0.00" 落库行)
    vd = {"verdict": "VIOLATION", "evidence": [{"source": "fee", "locator": "x", "text": "y"}]}
    out = apply_gate(vd, _rule("R999"), None, _gate_cfg())
    assert out.changed
    assert out.verdict == "INCONCLUSIVE"


def test_conf_high_not_downgraded():
    # conf 0.90 ≥ ceiling 0.85 → ③闸不改判
    vd = {"verdict": "VIOLATION", "confidence": 0.90, "evidence": [{"source": "fee", "locator": "x", "text": "y"}]}
    out = apply_gate(vd, _rule("R999"), None, _gate_cfg())
    assert not out.changed


def test_r205_counting_rule_not_cleared_by_anesthesia_existence():
    # R205 (1台手术按次超收全麻) 是计数类规则 — 麻醉真实存在恰是违规前提, 不在 ⑥ 存在性闸;
    # 高 conf V 应保留进落库 (仅受其他闸约束).
    cfg = GateConfig(
        anesthesia_reality_rules={"R203"},  # R205 已移出 ⑥ (与真实 yaml 一致)
        conf_ceiling=0.85,
    )
    ctx = _ctx(surgeries=[Surgery(name="腹腔镜胆囊切除术", anst_way="1", anst_dr="沈兵")])
    out = apply_gate(dict(_VD_V), _rule("R205", template="M1"), None, cfg, ctx)
    assert not out.changed
    assert out.verdict == "VIOLATION"


# ========== 直通 ==========
def test_clean_verdict_passthrough():
    vd = {"verdict": "CLEAN", "confidence": 0.80, "evidence": []}
    out = apply_gate(vd, _rule("R103", template="M5"), None, _gate_cfg())
    assert not out.changed
    assert out.verdict == "CLEAN"


def test_inconclusive_verdict_passthrough():
    vd = {"verdict": "INCONCLUSIVE", "confidence": 0.50, "evidence": []}
    out = apply_gate(vd, _rule("R141", template="M2"), None, _gate_cfg())
    assert not out.changed


# ========== make-rules-code-portable: extract_exam_keywords 显式字段 ==========
def test_exam_keywords_explicit_field_wins():
    from javert.audit.verdict_gate import extract_exam_keywords
    rule = Rule(
        rule_id="R151", domain="测试", violation_type="过度检查", question="q",
        prompt_addon='检索关键词: "旧措辞A"',
        exam_keywords=["磁共振", "MRI"],
    )
    assert extract_exam_keywords(rule) == ["磁共振", "MRI"]


def test_exam_keywords_absent_falls_back_to_prompt_grep():
    from javert.audit.verdict_gate import extract_exam_keywords
    rule = Rule(
        rule_id="R151", domain="测试", violation_type="过度检查", question="q",
        prompt_addon='检索关键词: "CT" / "增强"',
    )
    # 未填 exam_keywords → 回退旧链 (grep prompt_addon)
    assert extract_exam_keywords(rule) == ["CT", "增强"]


# ========== recover-deterministic-recall: 套餐类规则单次闸改口径 ==========
import pandas as pd  # noqa: E402


def _panel_cfg() -> GateConfig:
    return GateConfig(
        panel_rules={"R155": 3}, panel_downgrade_to="INCONCLUSIVE", conf_ceiling=0.85
    )


def _panel_fee_df(n_items: int, day: str = "1/8/2024 00:00:00") -> pd.DataFrame:
    return pd.DataFrame({
        "medins_list_name": [f"干扰素测定项{i}" for i in range(n_items)],
        "med_list_codg": [f"IFN{i}" for i in range(n_items)],
        "cnt": [1.0] * n_items,
        "fee_ocur_time": [day] * n_items,
    })


def test_panel_many_items_same_day_preserves_violation():
    # 11 项细胞因子单日打包 (≥ min_distinct_items=3) → V 保留, 不被单次闸误杀
    out = apply_gate(
        dict(_VD_V), _rule("R155", template="M2", triggers=["干扰素"]),
        None, _panel_cfg(), None, fee_df=_panel_fee_df(11),
    )
    assert not out.changed and out.verdict == "VIOLATION"


def test_panel_few_items_downgrades_to_inconclusive():
    # 2 项 (< 3) → 降 INCONCLUSIVE (进专家队列), 不落 CLEAN 黑洞
    out = apply_gate(
        dict(_VD_V), _rule("R155", template="M2", triggers=["干扰素"]),
        None, _panel_cfg(), None, fee_df=_panel_fee_df(2),
    )
    assert out.changed and out.verdict == "INCONCLUSIVE" and out.tag == "单次放过"


def test_panel_fee_unavailable_fail_open_keeps_violation():
    # 费用不可得 → fail-open 保留 V (不因数据缺失放大降级)
    out = apply_gate(
        dict(_VD_V), _rule("R155", template="M2", triggers=["干扰素"]),
        None, _panel_cfg(), None, fee_df=None,
    )
    assert not out.changed and out.verdict == "VIOLATION"


def test_non_panel_m2_rule_single_instance_unchanged():
    # 非 panel 规则口径逐字不变: 净次数 1 → 仍降 CLEAN
    net = {"K": NetItem(name="某检查", code="K", net_qty=1.0,
                        distinct_billing_dates=1, has_refund=False)}
    out = apply_gate(
        dict(_VD_V), _rule("R151", template="M2", triggers=["某检查"]),
        net, _panel_cfg(), None,
    )
    assert out.changed and out.verdict == "CLEAN" and out.tag == "单次放过"
