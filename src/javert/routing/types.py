"""Router data types — PatientRecord (输入) / RouterDecision (输出) / TriggerEvidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FeeItem:
    """单条费用明细的 router 输入子集 (字段对齐 shi_fee.csv)."""

    item_sn: str                       # feedetl_sn (唯一键)
    medins_list_name: str              # 项目/药品名称 (主要 keyword)
    medins_list_codg: Optional[str] = None  # 本院医保编码
    med_list_codg: Optional[str] = None     # 国标医保目录编码 (C 码, 跨院可移植 → trigger_codes 前缀)
    chrgitm_type: Optional[str] = None      # medins_chrgitm_type 类别标签 (西药/中成药/检查/治疗 etc)
    inscp_scp_amt: Optional[float] = None   # 医保内金额; None = 数据缺失, 视为可能命中
    self_flag: bool = False                 # 全自费标志
    fee_ocur_time: Optional[str] = None     # 发生时间
    spec: Optional[str] = None              # 规格 (可选)

    def has_insurance_coverage(self) -> bool:
        """是否有医保入账 (用于 RULE36 非基本医保目录判定).

        数据缺失时返回 True (保守, 让 LLM 验证, 避免 false-negative 漏检).
        """
        if self.self_flag:
            return False
        if self.inscp_scp_amt is None:
            return True
        return self.inscp_scp_amt > 0


@dataclass
class PatientRecord:
    """病案 router 输入: 患者属性 + 费用清单 + 诊断."""

    patient_id: str
    gender: Optional[str] = None          # 'M' / 'F'
    age: Optional[int] = None
    hospital_level: Optional[int] = None  # 1/2/3 三级医院级别
    visit_type: Optional[str] = None      # 'opt' (门诊) / 'ipt' (住院)
    department: Optional[str] = None      # 主科室 (用于 domain 匹配)
    diagnoses: list[str] = field(default_factory=list)   # 诊断编码 + 文本混合
    diagnosis_codes: list[str] = field(default_factory=list)  # 纯 ICD 编码
    fee_items: list[FeeItem] = field(default_factory=list)

    def fee_item_names(self) -> list[str]:
        return [f.medins_list_name for f in self.fee_items if f.medins_list_name]

    def fee_item_codes(self) -> list[str]:
        return [f.medins_list_codg for f in self.fee_items
                if f.medins_list_codg and f.medins_list_codg.strip()]


@dataclass(frozen=True)
class TriggerEvidence:
    """Java 引擎触发证据 (单条字典命中)."""

    entry_idx: int                      # violation_dict.entries 下标
    java_rule_no: str                   # RULE19 / RULE5 / ...
    category: str                       # 14372 大类 (e.g. 药品重复收费)
    matched_keyword: str                # 字典里命中的关键词
    matched_fee_name: str               # 病人 fee 里被命中的具体项目
    matched_item_sn: Optional[str]      # 对应 feedetl_sn (LLM 取证用)
    warn_msg: str                       # 字典原 warn_msg


@dataclass
class RouterDecision:
    """Router 输出: final_rules + 触发依据 + 统计."""

    patient_id: str
    final_rules: list[str]                    # 排序后, Stage B 要跑的 javert rule ids
    pruned_out: list[str]                     # 被 prune 掉的 javert rule ids
    java_triggered: dict[str, list[TriggerEvidence]] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def n_final(self) -> int:
        return len(self.final_rules)

    def evidence_pointers(self, javert_rule_id: str) -> list[TriggerEvidence]:
        """方便 LLM 工具调用前取证据: 此 yaml 由哪些 fee item 触发."""
        out: list[TriggerEvidence] = []
        # final_rules 已含此规则的前提下, 给出全部相关 java 证据
        for evs in self.java_triggered.values():
            for ev in evs:
                out.append(ev)
        return out
