# -*- coding: utf-8 -*-
"""Rule pydantic 模型 + Status 枚举."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Status = Literal["drafting", "ready", "validated", "abandoned"]
Priority = Literal["P0", "P1", "P2", "P3"]
HandlingLevel = Literal["违规（阻断）", "可疑（警告）", "提醒（引导）"]
RuleKind = Literal["audit", "chronic_disease_qualification"]

RULE_ID_BODY_PATTERN = r"(?:R\d{3}|RD\d{2,3}|CD\d{2,3})"
RULE_ID_PATTERN = rf"^{RULE_ID_BODY_PATTERN}$"


class PrecheckSpec(BaseModel):
    """确定性预检的结构化费用项目集.

    a_items/b_items = 主项/附属项目名列表, precheck 按项目名子串匹配患者费用。
    presence 只使用 a_items；coexist/companion 使用 A/B 两组。
    """

    a_items: list[str] = Field(default_factory=list, description="主项 (A 类) 项目名列表")
    b_items: list[str] = Field(default_factory=list, description="附属 (B 类) 项目名列表")
    mode: str = Field(
        default="coexist",
        description="coexist=M1 重复收费 (A∩B 并存→facts); companion=术式↔配套 (A 有 B 无→facts); "
        "presence=目标收费存在性 (A 无→clean, A 有→facts); "
        "presence_review=外部资料复核 (A 无→clean, A 有→review); "
        "coexist_review=中性共存复核 (A/B 缺一→clean, 双有→facts, 不预设附属关系). "
        "缺省 coexist, 既有 M1 规则行为逐字不变",
    )


class Rule(BaseModel):
    """单条「做不了」规则的 yaml 数据载体.

    字段顺序与 spec 一致, 由 rule_writer 控制写入顺序.
    """

    rule_id: str = Field(
        ...,
        pattern=RULE_ID_PATTERN,
        description="形如 R191、RD01 或 CD01 (门诊慢病认定规则)",
    )
    rule_kind: RuleKind = Field(
        default="audit",
        description="audit=既有医保审计；chronic_disease_qualification=慢病认定条件评估",
    )
    clinical_criteria_ref: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,127}/CD\d{2,3}$",
        description="慢病规则引用的版本化条件资产，例如 hlj-outpatient-chronic-2025/CD01",
    )
    domain: str = Field(..., min_length=1, description="所属领域 (肿瘤/各科室通用类/临床检验...)")
    violation_type: str = Field(..., min_length=1, description="违规类型 (从 0325 表 copy)")
    question: str = Field(..., min_length=1, description="问题描述 (从 0325 表 copy)")
    example: str = Field(default="", description="违规参考示例 (从 0325 表 copy, 可空)")
    status: Status = Field(default="drafting", description="状态")
    priority: Priority = Field(default="P3", description="审计优先级 (P0 最高/P3 最低, 由专家标注)")
    handling_level: HandlingLevel = Field(
        default="可疑（警告）",
        description="规则静态处理等级；与患者运行时 verdict、审计 priority 分离",
    )
    prompt_addon: str = Field(default="", description="规则特定 prompt 片段, 由操作者编写")
    trigger_keywords: list[str] = Field(default_factory=list, description="触发关键词列表")
    trigger_codes: list[str] = Field(
        default_factory=list,
        description="触发编码前缀/类别 token 列表 (医保目录码前缀 med_list_codg / 本院码 / 类别标签); "
        "router 编码命中与 keyword 命中取并集, 空=不参与 (换院命名不同仍可召回)",
    )
    exam_keywords: list[str] = Field(
        default_factory=list,
        description="verdict_gate 单次/存在性闸匹配 fee 行的检查名; 空则回退 grep prompt_addon",
    )
    suggested_tools: list[str] = Field(default_factory=list, description="建议优先调用的工具名")
    expected_signal: str = Field(default="", description="预期信号 (操作者备注)")
    notes: str = Field(default="", description="设计 / 取舍说明")
    derived_from_template: str | None = Field(
        default=None,
        description="若 prompt_addon 由某模板 (例如 M1) 渲染生成则记录 template_id; 否则 None",
    )
    drug_rule_type: str | None = Field(
        default=None,
        description="药品类规则 (M8) 的类型: 限适应症/超说明书/限二线/禁忌症; 非药品规则为 None",
    )
    render_hash: str | None = Field(
        default=None,
        description="prompt-fit 最近一次渲染产物的 hash; 供覆盖护栏比对手改 (缺失=未知来源)",
    )
    precheck: PrecheckSpec | None = Field(
        default=None,
        description="确定性费用形态预检；空=无预检",
    )

    @model_validator(mode="after")
    def _validate_rule_kind(self) -> "Rule":
        is_chronic_id = self.rule_id.startswith("CD")
        is_chronic_kind = self.rule_kind == "chronic_disease_qualification"
        if is_chronic_id != is_chronic_kind:
            raise ValueError("CD rule_id 必须且只能使用 chronic_disease_qualification rule_kind")
        if is_chronic_kind:
            expected_suffix = f"/{self.rule_id}"
            if self.clinical_criteria_ref is None:
                raise ValueError("慢病规则必须声明 clinical_criteria_ref")
            if not self.clinical_criteria_ref.endswith(expected_suffix):
                raise ValueError("clinical_criteria_ref 尾部必须与 rule_id 一致")
        elif self.clinical_criteria_ref is not None:
            raise ValueError("audit 规则不得声明 clinical_criteria_ref")
        return self


class RuleValidationError(ValueError):
    """加载 yaml 失败 (字段缺失 / 类型错误 / status 非法)."""
