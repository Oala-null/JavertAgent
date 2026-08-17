# -*- coding: utf-8 -*-
"""Audit Runner — 单 (rule, patient) 的 LLM agent loop."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable

from javert.config import JavertConfig, get_config
from javert.oncology.contracts import EligibilityEvaluation
from javert.oncology.runtime import decode_structured_payload
from javert.tools.llm_provider import LlmUnavailableError, Qwen35Provider
from javert.tools.tool_executor import ToolExecutor
from javert.promises.loader import get_promise_repository
from javert.promises.registry import evaluate_terminal_promises

from javert.data.fee_netting import net_fee_items

from .prompt_assembler import (
    assemble_system_prompt,
    initial_user_message,
    load_base_prompt,
    load_experience_doc,
    load_hospital_config,
)
from .precheck import CLEAN as PC_CLEAN, FACTS as PC_FACTS, PrecheckResult, run_precheck
from .result import AuditResult, Evidence, ToolCall, TOOL_FAILURE_GATE_TAG
from .rule import Rule
from .run_id import new_run_id
from .verdict_gate import apply_gate, get_gate_config

logger = logging.getLogger("javert.audit.runner")

# 解析最终 fenced JSON
_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)
_TRUNCATE = 2000  # 单工具结果存储上限 (默认; 实际用 config.tool_result_max_chars)

# 分段截断标记 (fix-drug-audit-precision D1): 工具把「必留头部」放标记之前、
# 「可截明细」放标记之后; 无此标记的工具结果维持旧尾截断行为.
RETAIN_HEAD_MARKER = "====[必留头部结束]===="

_TECHNICAL_FAILURE_MARKERS = (
    "not iterable",
    "执行失败",
    "技术故障",
    "工具调用错误",
    "tool error",
    "traceback",
)


def _truncate(text: str, limit: int = _TRUNCATE) -> str:
    if len(text) <= limit:
        return text
    pos = text.find(RETAIN_HEAD_MARKER)
    if pos == -1:
        # 无标记: 旧尾截断
        return text[:limit] + f"\n...[已截断, 原长 {len(text)}]"
    # 含标记: 头部 (含标记行) 优先保全, 只截明细段
    head = text[: pos + len(RETAIN_HEAD_MARKER)]
    detail = text[pos + len(RETAIN_HEAD_MARKER):]
    if len(head) > limit:
        # fix-scan-residuals: 头部本身超总预算时也硬截 (防单条工具结果整体超预算 → context 溢出 400).
        # 权衡: 极端诊断数患者可能丢部分 ground truth, 但换来总长有界; 提示让模型可感知.
        return head[:limit] + f"\n...[头部超总预算, 已硬截; 原头部 {len(head)} 字符]"
    kept = detail[: max(0, limit - len(head))]
    return head + kept + f"\n...[明细已截断, 原明细 {len(detail)} 字符]"


def _scan_balanced_objects(text: str) -> list[str]:
    """扫出所有顶层平衡的 `{...}` 对象子串 (识别字符串字面量与转义).

    替代「首 `{` 到末 `}`」贪婪切片: reasoning 内含花括号时贪婪切片会拼出
    不可解析 blob; 平衡扫描则把每个完整对象单独抽出.
    """
    objs: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    objs.append(text[start:i + 1])
                    start = -1
    return objs


def _parse_verdict_block(text: str) -> dict[str, Any] | None:
    """从 LLM 输出提取 verdict JSON. 优先 fenced ```json``` 块; 无围栏时回退裸 JSON.

    deadline turn 常直接吐裸 `{...}` (不带 ``` 围栏), 旧版只认 fenced → 合法 verdict
    被丢成 conf=0.00. 无围栏时用括号平衡扫描逐个抽取候选, 从后往前取首个合法块.
    """
    candidates = _JSON_BLOCK_PATTERN.findall(text)
    if not candidates:
        # 回退: 裸 JSON (deadline turn 不带围栏的常见情况), 括号平衡扫描逐个候选
        candidates = _scan_balanced_objects(text)
    # 从后往前取第一个合法 verdict 块 (最终裁决通常在尾部)
    for raw in reversed(candidates):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("verdict") in {"VIOLATION", "CLEAN", "INCONCLUSIVE"}:
            return data
    return None


def _coerce_evidence(raw: list | None) -> list[Evidence]:
    out: list[Evidence] = []
    if not raw:
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Evidence(
                source=str(item.get("source", "unknown")),
                locator=str(item.get("locator", "")),
                text=str(item.get("text", ""))[:1000],
            ))
        except Exception:
            continue
    return out


def _contains_technical_failure(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _TECHNICAL_FAILURE_MARKERS)


def _is_fact_evidence(evidence: Evidence) -> bool:
    return (
        evidence.source.lower() != "etl_warning"
        and bool(evidence.text.strip())
        and not _contains_technical_failure(f"{evidence.locator}\n{evidence.text}")
    )


# 结构化资格 → 自然语言: 状态词 + 结论句. 前端 follow-up 面板另有逐条清单.
_STATE_MARK = {
    "SATISFIED": "✓",
    "NOT_SATISFIED": "✗",
    "UNKNOWN": "？",
    "CONFLICT": "⚠",
}
_DISPOSITION_ZH: dict[tuple[str, str], str] = {
    ("NO_VIOLATION_FOUND", "SATISFIED"): "患者情况满足该药全部医保限定支付条件，未见超范围支付。",
    ("NO_VIOLATION_FOUND", "DOCUMENTATION_GAP"): (
        "现有病历未见明确违规，但部分限定条件缺少文书佐证，"
        "建议补充相关记录后归档（不影响本次合规结论）。"
    ),
    ("VIOLATION_FOUND", "NOT_SATISFIED"): "患者情况明确不满足该药医保限定支付条件，属超范围支付。",
    ("REVIEW_REQUIRED", "DOCUMENTATION_GAP"): "关键限定条件缺少文书佐证，无法自动定性，需人工复核。",
    ("REVIEW_REQUIRED", "CONFLICT"): "限定条件出现相互矛盾的证据，需人工复核。",
}


def _oncology_candidate_names(structured_payloads: list[dict[str, Any]]) -> list[str]:
    """从最近一次 oncology 结构化 payload 取候选药通用名 (去重保序)."""
    for payload in reversed(structured_payloads):
        rows = payload.get("candidate_evaluations")
        if not rows:
            continue
        names: list[str] = []
        for row in rows:
            name = str(row.get("generic_name") or "").strip()
            if name and name not in names:
                names.append(name)
        if names:
            return names
    return []


def _structured_evidence(
    evaluation: EligibilityEvaluation,
    candidate_names: list[str] | None = None,
) -> list[Evidence]:
    out: list[Evidence] = []
    seen: set[tuple[str, str, str]] = set()
    # 命中项目 (target item): 候选药作 drug 锚点, 让 hit_resolver 在明细表定位该药 + 附医保限定.
    for name in candidate_names or []:
        key = ("drug_audit_lookup", name, "")
        if key in seen:
            continue
        seen.add(key)
        out.append(Evidence(source="drug_audit_lookup", locator=name, text=""))
    scoped = evaluation.scope_evaluations or [evaluation]
    for scope in scoped:
        for assessment in scope.criterion_assessments:
            for anchor in assessment.evidence_anchors:
                key = (anchor.source, anchor.locator, anchor.text)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    Evidence(
                        source=anchor.source,
                        locator=anchor.locator,
                        text=anchor.text[:1000],
                        anchor=anchor.anchor,
                    )
                )
    return out


def _structured_reasoning(
    evaluation: EligibilityEvaluation,
    candidate_names: list[str] | None = None,
) -> str:
    names = candidate_names or []
    if evaluation.scope_evaluations:
        drug_clause = "、".join(names[:3]) + (
            f" 等 {len(names)} 种" if len(names) > 3 else ""
        ) if names else "本例肿瘤药"
        overall = {
            "NO_VIOLATION_FOUND": "各政策范围均未发现不符合。",
            "VIOLATION_FOUND": "至少一个政策范围明确不符合，旧三态按最严重范围投影为违规。",
            "REVIEW_REQUIRED": "至少一个政策范围需人工复核，旧三态按最严重范围投影为不明。",
        }[evaluation.audit_disposition.value]
        lines = [f"{drug_clause}（肿瘤资格双来源核对）：{overall}"]
        conclusions = {
            ("NO_VIOLATION_FOUND", "SATISFIED"): "满足该范围全部条件，未见不符合。",
            ("NO_VIOLATION_FOUND", "DOCUMENTATION_GAP"): (
                "现有病历未见明确不符合，但部分条件缺少文书佐证"
                "（不影响本次该范围结论）。"
            ),
            ("VIOLATION_FOUND", "NOT_SATISFIED"): "明确不满足该范围条件。",
            ("REVIEW_REQUIRED", "DOCUMENTATION_GAP"): (
                "关键条件缺少文书佐证，无法自动定性，需人工复核。"
            ),
            ("REVIEW_REQUIRED", "CONFLICT"): "条件出现相互矛盾的证据，需人工复核。",
        }
        for scope in evaluation.scope_evaluations:
            label = scope.policy_scope_display_label
            conclusion = conclusions.get(
                (scope.audit_disposition.value, scope.eligibility_status.value),
                "核对结果见下。",
            )
            lines.extend(["", f"[{label}] {conclusion}"])
            for item in scope.criterion_assessments:
                mark = _STATE_MARK.get(item.state.value, "·")
                reason = item.reason or item.criterion_id
                lines.append(f"{mark} {reason}")
            for suggestion in scope.documentation_suggestions:
                lines.append(f"[病历完善建议] {suggestion.suggested_content}")
            if scope.temporal_warning:
                lines.append(f"[时间提示] {scope.temporal_warning}")
            if scope.data_quality_flags:
                lines.append(f"[数据质量] {'; '.join(scope.data_quality_flags)}")
        return "\n".join(lines)

    if names:
        drug_clause = "、".join(names[:3]) + (
            f" 等 {len(names)} 种" if len(names) > 3 else ""
        ) + "（医保限定支付肿瘤药）"
    else:
        drug_clause = "本例医保限定支付肿瘤药"
    conclusion = _DISPOSITION_ZH.get(
        (evaluation.audit_disposition.value, evaluation.eligibility_status.value),
        "肿瘤药医保限定支付条件核对结果见下。",
    )
    lines = [f"{drug_clause}：{conclusion}"]
    if evaluation.criterion_assessments:
        lines.append("")
        lines.append("逐条核对：")
        for item in evaluation.criterion_assessments:
            mark = _STATE_MARK.get(item.state.value, "·")
            reason = item.reason or item.criterion_id
            lines.append(f"{mark} {reason}")
    for suggestion in evaluation.documentation_suggestions:
        lines.append(f"\n[病历完善建议] {suggestion.suggested_content}")
    if evaluation.data_quality_flags:
        lines.append(f"\n[数据质量] {'; '.join(evaluation.data_quality_flags)}")
    return "\n".join(lines)


class Runner:
    """执行单条 (rule, patient_id) 的 agent loop."""

    def __init__(
        self,
        executor: ToolExecutor,
        provider: Qwen35Provider | None = None,
        config: JavertConfig | None = None,
        emit: Callable[[str], None] | None = None,
        loader=None,
    ):
        self.executor = executor
        self.config = config or get_config()
        self.provider = provider or Qwen35Provider(self.config)
        # emit: 单参回调, 用于 dry-run 打印 trace; None = 静默
        self.emit = emit or (lambda _msg: None)
        # loader: 供 verdict_gate ② 单次闸重算净收费次数; None → 单次闸 fail-open
        self.loader = loader
        # 临床事实闸 (⑥⑦⑧ + 影像确认) 的 per-patient 缓存; 跨规则复用同一 ctx.
        self._clinical_ctx_cache: dict[str, Any] = {}
        # 单次闸净费上下文 per-patient 缓存 (同策略, None 也缓存) — 免同患者多条 V 重复全表扫 fee.
        self._net_fee_ctx_cache: dict[str, Any] = {}
        # 套餐闸原始费用帧 per-patient 缓存 (同日不同项目名数需按日期分组, net ctx 拿不到).
        self._fee_df_cache: dict[str, Any] = {}
        # 资产非法时 Runner 构造即 fail closed；不静默退回可漂移的普通裁决。
        self._promise_repository = get_promise_repository()

    # --- 公共 API ---
    def audit(
        self,
        rule: Rule,
        patient_id: str,
        reset_cache: bool = True,
        manage_patient_context: bool = True,
    ) -> AuditResult:
        """跑一次 (rule, patient) 审计.

        Args:
            rule: 规则
            patient_id: 患者住院号
            reset_cache: True (默认) 在 audit 开始时 reset ToolExecutor 缓存; False
                则保留缓存以跨规则复用 (调用方需自己管理「patient 切换时清缓存」).
            manage_patient_context: True (默认) audit 边界 set/clear executor.patient_context;
                False 则不动 — 调用方 (例如并发 batch 入口) 负责 set/clear.
        """
        run_id = new_run_id()
        started = datetime.now(timezone.utc)
        t_start = time.perf_counter()

        if reset_cache:
            # 重置工具缓存 — 不同 audit 间不应复用 (单 patient 单 rule 默认行为)
            self.executor.reset_cache()

        if manage_patient_context:
            self.executor.set_patient_context(patient_id)

        try:
            return self._audit_body(rule, patient_id, run_id, started, t_start)
        finally:
            if manage_patient_context:
                self.executor.clear_patient_context()

    def _build_net_fee_ctx(self, patient_id: str):
        """该 patient 的退费净额 {group_key: NetItem}; loader 缺失/取数失败 → None (单次闸 fail-open).
        per-patient 缓存, 跨规则复用 (与 _build_clinical_ctx 同策略)."""
        if patient_id in self._net_fee_ctx_cache:
            return self._net_fee_ctx_cache[patient_id]
        ctx = None
        if self.loader is not None:
            try:
                ctx = net_fee_items(self.loader.get_fees(patient_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "net_fee_ctx 构建失败 error_type=%s (Promise 不适用，单次闸 fail-open)",
                    type(exc).__name__,
                )
        self._net_fee_ctx_cache[patient_id] = ctx
        return ctx

    def _build_fee_df(self, patient_id: str):
        """该 patient 的原始费用帧 (套餐闸按日期分组用); 取数失败 → None (fail-open). per-patient 缓存."""
        if patient_id in self._fee_df_cache:
            return self._fee_df_cache[patient_id]
        df = None
        if self.loader is not None:
            try:
                df = self.loader.get_fees(patient_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fee_df 构建失败 patient=%s: %s (套餐闸 fail-open)", patient_id, exc)
        self._fee_df_cache[patient_id] = df
        return df

    def _build_clinical_ctx(self, patient_id: str):
        """该 patient 的病案首页临床事实 (手术/麻醉/诊断 + 检查报告确认); 构建失败 → None
        (临床事实闸 ⑥⑦⑧ + 影像确认闸 fail-open). per-patient 缓存, 跨规则复用."""
        if patient_id in self._clinical_ctx_cache:
            return self._clinical_ctx_cache[patient_id]
        ctx = None
        try:
            from javert.data.clinical_context import build_clinical_context
            from javert.tools.registry import get_exam_loader
            # exam_loader 单例为 lazy: 只有影像规则调 has_imaging_report() 时才真正 build index.
            ctx = build_clinical_context(
                patient_id,
                self.config.ss_path,
                self.config.zd_path,
                exam_loader=get_exam_loader(self.config),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("clinical_ctx 构建失败 patient=%s: %s (临床事实闸 fail-open)", patient_id, exc)
            ctx = None
        self._clinical_ctx_cache[patient_id] = ctx
        return ctx

    def _execute_and_record(
        self,
        content: str,
        tool_calls: list[dict[str, Any]],
        messages: list[dict[str, str]],
        tool_records: list[ToolCall],
        *,
        audit_rule_id: str = "",
        structured_payloads: list[dict[str, Any]] | None = None,
        feed_messages: bool = True,
    ) -> int:
        """执行一批 tool_call: 记录到 tool_records、把结果回灌对话, 返回成功次数.

        主循环与 repair 路径共用 — repair 响应含 tool_call 时也走这里续跑,
        不再丢弃. 成功次数供「至少 1 次成功 tool_call 才解锁裁决」判定.
        """
        tool_results_text: list[str] = []
        n_ok = 0
        for call in tool_calls:
            t0 = time.perf_counter()
            execute_call = call
            if call["name"] == "drug_audit_lookup" and audit_rule_id:
                execute_call = {
                    **call,
                    "arguments": {
                        **(call.get("arguments", {}) or {}),
                        "_audit_rule_id": audit_rule_id,
                    },
                }
            result_text, cached = self.executor.execute(execute_call)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            structured_output = (
                decode_structured_payload(result_text)
                if call["name"] == "drug_audit_lookup"
                else None
            )
            if structured_output is not None and structured_payloads is not None:
                structured_payloads.append(structured_output)
            truncated = _truncate(result_text, self.config.tool_result_max_chars)
            tool_records.append(ToolCall(
                tool_name=call["name"],
                arguments=call.get("arguments", {}) or {},
                result=truncated,
                structured_output=structured_output,
                duration_ms=elapsed_ms,
                cached=cached,
            ))
            if not ToolExecutor.is_error_result(result_text):
                n_ok += 1
            self.emit(
                f"[Tool] {call['name']}({json.dumps(call.get('arguments', {}), ensure_ascii=False)}) "
                f"→ {truncated[:200].replace(chr(10), ' ')} "
                f"({elapsed_ms}ms{', cached' if cached else ''})"
            )
            tool_results_text.append(f"工具 {call['name']} 返回:\n{truncated}")
        if feed_messages:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "\n\n".join(tool_results_text)})
        return n_ok

    # --- 确定性预检 (pilot-deterministic-precheck) ---
    def _run_precheck(self, rule: Rule, patient_id: str) -> PrecheckResult | None:
        """带 precheck 字段 + 开关 on 时跑确定性预检; 否则/取数失败 → None (走原 LLM 路径)."""
        if getattr(rule, "precheck", None) is None:
            return None
        if str(self.config.precheck).lower() == "off":
            return None
        try:
            fee_df = self.loader.get_fees(patient_id) if self.loader is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("precheck 取 fee 失败 patient=%s: %s (skip)", patient_id, exc)
            return None
        return run_precheck(rule.precheck, fee_df)

    def _make_precheck_clean(
        self,
        rule: Rule,
        patient_id: str,
        run_id: str,
        started: datetime,
        t_start: float,
        pc: PrecheckResult,
    ) -> AuditResult:
        """预检短路 CLEAN → 直接构造结果 (0 LLM 调用, 0 工具调用)."""
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        self.emit(f"[Precheck] {rule.rule_id} → CLEAN ({pc.precheck_tag}): {pc.reason}")
        result = AuditResult(
            run_id=run_id,
            rule_id=rule.rule_id,
            patient_id=patient_id,
            verdict="CLEAN",
            confidence=0.9,
            reasoning=pc.reason,
            evidence=pc.evidence,
            tool_calls=[],
            duration_ms=duration_ms,
            model=self.provider.model_name,
            started_at=started,
            precheck_tag=pc.precheck_tag,
        )
        self.emit(
            f"[Verdict] C conf=0.90 duration={duration_ms / 1000:.1f}s "
            f"tool_calls=0 (precheck) run_id={run_id}"
        )
        return result

    def _audit_body(
        self,
        rule: Rule,
        patient_id: str,
        run_id: str,
        started: datetime,
        t_start: float,
    ) -> AuditResult:
        """audit() 主体 — 提出来便于 try/finally 包裹 patient_context 管理."""
        # --- 终局 Promise: 在任何普通预检或 LLM 裁决之前 ---
        net_fee_ctx = self._build_net_fee_ctx(patient_id)
        promise_outcome = evaluate_terminal_promises(
            self._promise_repository.active_definitions,
            rule.rule_id,
            net_fee_ctx or {},
        )
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        if promise_outcome.conflict_ids:
            # 只记录安全 Promise ID；不记录 patient、费用事实或完整 trace。
            logger.error(
                "terminal Promise conflict promises=%s",
                ",".join(promise_outcome.conflict_ids),
            )
            return AuditResult(
                run_id=run_id,
                rule_id=rule.rule_id,
                patient_id=patient_id,
                verdict="INCONCLUSIVE",
                confidence=0.0,
                reasoning="PROMISE_CONFLICT",
                evidence=[],
                tool_calls=[],
                duration_ms=duration_ms,
                model=self.provider.model_name,
                started_at=started,
            )
        if promise_outcome.match is not None:
            match = promise_outcome.match
            self.emit(
                f"[Promise] {match.trace.promise_id}@{match.trace.version} "
                f"→ {match.guarantee} ({match.trace.reason_code})"
            )
            return AuditResult(
                run_id=run_id,
                rule_id=rule.rule_id,
                patient_id=patient_id,
                verdict=match.guarantee,
                confidence=1.0,
                reasoning="退费后目标收费项目净数量未超过一次，未触发多次检查边界。",
                evidence=[],
                tool_calls=[],
                duration_ms=duration_ms,
                model=self.provider.model_name,
                started_at=started,
                promise_trace=match.trace,
            )

        # --- 确定性预检 (pilot-deterministic-precheck): LLM loop 之前 ---
        # clean → 短路 CLEAN 零 LLM 调用; facts → 注入事实块 + 判 V 时合并费用锚点; 否则原路径.
        precheck_facts: str | None = None
        precheck_tag = ""
        precheck_evidence: list[Evidence] = []
        pc = self._run_precheck(rule, patient_id)
        if pc is not None:
            if pc.outcome == PC_CLEAN:
                return self._make_precheck_clean(rule, patient_id, run_id, started, t_start, pc)
            if pc.outcome == PC_FACTS:
                precheck_facts = pc.fact_block
                precheck_tag = pc.precheck_tag
                precheck_evidence = pc.evidence

        base_prompt = load_base_prompt(self.config.prompts_path)
        hospital_config = load_hospital_config(self.config.hospital_config_path)
        experience_doc = load_experience_doc(
            self.config.resolve("configs/experience.md")
        )
        system_prompt = assemble_system_prompt(
            rule,
            base_prompt,
            self.executor.get_tools_prompt(),
            hospital_config=hospital_config,
            experience_doc=experience_doc,
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_user_message(rule, patient_id, precheck_facts)},
        ]

        tool_records: list[ToolCall] = []
        structured_payloads: list[dict[str, Any]] = []
        n_success = 0  # 成功 (非错误串) 的 tool_call 次数 — 放行裁决的门槛
        verdict_data: dict[str, Any] | None = None
        final_reason = ""

        # v2: RD04 先确定性取医保/指南双 scope 候选与条件树，保证每个净正收费
        # 肿瘤候选都进入结构化求值。
        # shadow 只记录预取结果，不喂回 LLM、也不计入旧「至少一次工具成功」门槛。
        if (
            rule.rule_id == "RD04"
            and self.config.oncology_eligibility_v2 in {"shadow", "on"}
        ):
            oncology_mode = self.config.oncology_eligibility_v2
            prefetch_success = self._execute_and_record(
                "[Oncology v2] 预取 RD04 肿瘤医保/指南候选与结构化资格证明。",
                [{
                    "name": "drug_audit_lookup",
                    "arguments": {
                        "patient_id": patient_id,
                    },
                }],
                messages,
                tool_records,
                audit_rule_id=rule.rule_id,
                structured_payloads=structured_payloads,
                feed_messages=oncology_mode == "on",
            )
            if oncology_mode == "on":
                n_success += prefetch_success

        oncology_no_candidate = (
            rule.rule_id == "RD04"
            and self.config.oncology_eligibility_v2 == "on"
            and any(payload.get("no_candidate") for payload in structured_payloads)
        )
        if oncology_no_candidate:
            self.emit("[Oncology v2] 未发现 RD04 候选，确定性短路 CLEAN，跳过 LLM。")

        max_calls = 0 if oncology_no_candidate else self.config.max_tool_calls
        for turn in range(1, max_calls + 1):
            try:
                resp = self.provider.chat_with_retry(messages)
            except LlmUnavailableError:
                # 让上层决定是否重试 / skip — 此处直接抛
                raise

            content = resp["content"] or ""
            finish_reason = resp.get("finish_reason")
            self.emit(f"[LLM #{turn}] {content[:1500]}{'...' if len(content) > 1500 else ''}")

            tool_calls = self.executor.parse_tool_calls(content)
            if tool_calls:
                n_success += self._execute_and_record(
                    content,
                    tool_calls,
                    messages,
                    tool_records,
                    audit_rule_id=rule.rule_id,
                    structured_payloads=structured_payloads,
                )
                continue

            # 没 tool_call: 尝试解析最终 verdict
            verdict_data = _parse_verdict_block(content)
            if verdict_data is not None:
                if n_success == 0:
                    # 至少 1 次成功 tool_call 才解锁裁决 (全失败/未调用均拒绝)
                    self.emit("[Runner] 无成功 tool_call, 拒绝 verdict")
                    verdict_data = None
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": (
                            "你必须在裁决前至少成功调用 1 次工具 (此前调用均失败或未调用). "
                            "请换参数重发 <tool_call>; 若确实查不到证据可裁 INCONCLUSIVE."
                        ),
                    })
                    continue
                final_reason = ""
                break

            # 没 tool_call 也没合法 verdict — 触发一次 repair.
            # length 截断不回灌数万字残片, 改用短提示 + 小 token budget 收敛;
            # 其他畸形 tool_call 仍回传具体 JSON 解析错误.
            truncated = finish_reason == "length"
            tc_errors = self.executor.parse_errors(content)
            if truncated:
                self.emit("[Runner] LLM 输出达到长度上限, 发起有界 repair")
                if n_success == 0:
                    repair_prompt = (
                        "上一轮输出因达到长度上限被截断。禁止解释或复述；"
                        "只输出一个简短、完整、合法的 <tool_call>{...}</tool_call>，"
                        "先查询裁决所需的最关键证据。"
                    )
                else:
                    repair_prompt = (
                        "上一轮输出因达到长度上限被截断。已有工具证据；"
                        "禁止解释或继续调用工具，只输出一个简短、完整的 fenced JSON，"
                        "字段仅含 verdict、confidence、evidence、reasoning。"
                    )
            elif tc_errors:
                self.emit(f"[Runner] tool_call JSON 畸形 ({tc_errors[0]}), 发起针对性 repair")
                repair_prompt = (
                    f"你的 tool_call JSON 非法: {tc_errors[0]}. "
                    "请修正后重新发出 <tool_call> (仍可继续调查); 若已可裁决则只输出 ```json {...} ``` 块."
                )
            else:
                self.emit("[Runner] 输出既无 tool_call 也无合法 verdict JSON, 发起 repair turn")
                repair_prompt = (
                    "你的输出无法解析. 请: 若需更多证据则发 <tool_call>; "
                    "若已可裁决则只输出 ```json {...} ``` 块, 字段含 verdict/confidence/evidence/reasoning."
                )
            if not truncated:
                messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": repair_prompt})
            try:
                repair_kwargs = (
                    {"max_tokens": min(512, self.config.llm_max_tokens)}
                    if truncated
                    else {}
                )
                resp_repair = self.provider.chat_with_retry(messages, **repair_kwargs)
            except LlmUnavailableError:
                raise
            content_repair = resp_repair["content"] or ""
            repair_finish_reason = resp_repair.get("finish_reason")
            self.emit(f"[LLM #{turn}-repair] {content_repair[:1500]}")
            # repair 响应含 tool_call → 执行并回主循环续跑, 不再丢弃直接 INCONCLUSIVE
            repair_calls = self.executor.parse_tool_calls(content_repair)
            if repair_calls:
                n_success += self._execute_and_record(
                    content_repair,
                    repair_calls,
                    messages,
                    tool_records,
                    audit_rule_id=rule.rule_id,
                    structured_payloads=structured_payloads,
                )
                continue
            verdict_data = _parse_verdict_block(content_repair)
            if verdict_data is not None and n_success > 0:
                break
            # 二次失败 → INCONCLUSIVE
            final_reason = (
                "LLM output truncated (repair failed)"
                if truncated or repair_finish_reason == "length"
                else "malformed verdict JSON (repair failed)"
            )
            verdict_data = None
            break

        else:
            # for-else: max_calls 用完未 break — 触顶兜底前先发 deadline turn
            # 给 LLM 最后一次基于已有 tool result 出 verdict 的机会 (fix-tool-call-budget-fallback).
            # 仅在已经调过工具时尝试 deadline; 完全没调过工具 → 不救, 走旧兜底.
            if oncology_no_candidate:
                final_reason = "RD04 no candidate (deterministic short circuit)"
            elif tool_records:
                self.emit(
                    f"[Runner] tool budget 用尽 ({max_calls} 轮 LLM call, {len(tool_records)} tc), "
                    "发起 deadline turn 强制收敛 verdict"
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"已用尽 {max_calls} 轮工具调用 budget, 你不能再发 <tool_call>. "
                        "请立即基于已收集到的 tool result 输出最终裁决 (fenced JSON 块). "
                        "若证据真不足支持 V/C, 输出 INCONCLUSIVE conf 0.40~0.60 + evidence 引已查工具结果即可, "
                        "不要输出 conf=0.00. 此轮只接受 ```json ... ``` 输出, tool_call 一律忽略."
                    ),
                })
                try:
                    resp_deadline = self.provider.chat_with_retry(messages)
                    content_deadline = resp_deadline["content"] or ""
                    deadline_finish_reason = resp_deadline.get("finish_reason")
                    self.emit(
                        f"[LLM #deadline] {content_deadline[:1500]}"
                        f"{'...' if len(content_deadline) > 1500 else ''}"
                    )
                    parsed = _parse_verdict_block(content_deadline)
                    if parsed is not None:
                        verdict_data = parsed
                        final_reason = "tool budget exhausted (deadline verdict accepted)"
                    elif deadline_finish_reason == "length":
                        final_reason = "tool budget exhausted (deadline verdict truncated)"
                    else:
                        final_reason = "tool budget exhausted (deadline verdict malformed)"
                except LlmUnavailableError:
                    final_reason = "tool budget exhausted (deadline LLM unavailable)"
            else:
                final_reason = "max tool calls exhausted (no tool was called)"

        duration_ms = int((time.perf_counter() - t_start) * 1000)

        if verdict_data is None:
            verdict = "INCONCLUSIVE"
            confidence = 0.0
            reasoning = final_reason or "未产出有效 verdict"
            evidence: list[Evidence] = []
        else:
            verdict = verdict_data["verdict"]
            try:
                confidence = float(verdict_data.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            reasoning = str(verdict_data.get("reasoning", ""))
            evidence = _coerce_evidence(verdict_data.get("evidence"))

        eligibility_evaluation: EligibilityEvaluation | None = None
        selected_structured = next(
            (
                payload.get("selected_eligibility_evaluation")
                for payload in reversed(structured_payloads)
                if payload.get("selected_eligibility_evaluation")
            ),
            None,
        )
        if rule.rule_id == "RD04" and self.config.oncology_eligibility_v2 == "shadow":
            if selected_structured:
                shadow = EligibilityEvaluation.model_validate(selected_structured)
                self.emit(
                    f"[Oncology shadow] legacy={verdict} structured={shadow.legacy_verdict} "
                    f"status={shadow.eligibility_status.value}"
                )
        elif rule.rule_id == "RD04" and self.config.oncology_eligibility_v2 == "on":
            if selected_structured:
                eligibility_evaluation = EligibilityEvaluation.model_validate(
                    selected_structured
                )
                candidate_names = _oncology_candidate_names(structured_payloads)
                verdict = eligibility_evaluation.legacy_verdict
                confidence = 1.0 if verdict != "INCONCLUSIVE" else 0.9
                reasoning = _structured_reasoning(
                    eligibility_evaluation, candidate_names
                )
                evidence = _structured_evidence(
                    eligibility_evaluation, candidate_names
                )
                verdict_data = {
                    "verdict": verdict,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "evidence": [item.model_dump() for item in evidence],
                }
            elif any(payload.get("no_candidate") for payload in structured_payloads):
                verdict = "CLEAN"
                confidence = 1.0
                reasoning = "RD04 未发现净正收费的肿瘤医保限定候选，本规则不适用。"
                evidence = []
                verdict_data = {
                    "verdict": verdict,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "evidence": [],
                }
            elif (
                any(payload.get("error") for payload in structured_payloads)
                or any(
                    record.tool_name == "drug_audit_lookup"
                    and ToolExecutor.is_error_result(record.result)
                    for record in tool_records
                )
            ):
                # 知识资产/求值异常不得静默退回 LLM 自由解释.
                verdict = "INCONCLUSIVE"
                confidence = 0.0
                reasoning = "肿瘤医保结构化资格求值被阻断，需人工复核。"
                evidence = []
                verdict_data = None

        # --- 裁决后确定性 gate (add-verdict-gate-layer) ---
        # 解析完 verdict、构建 AuditResult 之前调 apply_gate; 仅作用 VIOLATION, 只降不升.
        gate_tag = ""
        if (
            verdict_data is not None
            and verdict == "VIOLATION"
            and eligibility_evaluation is None
            and str(self.config.verdict_gate).lower() != "off"
        ):
            outcome = apply_gate(
                verdict_data,
                rule,
                self._build_net_fee_ctx(patient_id),
                get_gate_config(),
                self._build_clinical_ctx(patient_id),
                fee_df=self._build_fee_df(patient_id),
            )
            if outcome.changed:
                self.emit(f"[Gate] {verdict} → {outcome.verdict} ({outcome.tag}): {outcome.reason}")
                verdict = outcome.verdict
                gate_tag = outcome.tag
                # fix-scan-residuals: 降级后 confidence 归一到 0.5 (原 V 值留在注记),
                # 避免落库出现「INCONCLUSIVE conf=0.90」误读; 原值仍可从 reasoning 追溯.
                reasoning = (
                    reasoning + f"\n[gate: {outcome.reason} | 原 conf={confidence:.2f}]"
                ).strip()
                confidence = 0.5

        # pilot-deterministic-precheck: 事实成立判 V → 合并预检确定性费用锚点 (去重),
        # 保证新 V 的 evidence 100% 带机器可复核费用行 (与 LLM 引用是否准确解耦).
        if verdict == "VIOLATION" and precheck_evidence:
            seen = {(e.source, e.locator) for e in evidence}
            for e in precheck_evidence:
                if (e.source, e.locator) not in seen:
                    evidence.append(e)
                    seen.add((e.source, e.locator))

        # 药品类 V 数据缺口提醒 (med_rst): 费用数据无自付明细 + 病理文书常缺,
        # 药品违规判定有系统性盲区 → 确定性追加提醒 (不依赖 LLM 记性; 只加文字, 不动 verdict).
        if (
            verdict == "VIOLATION"
            and getattr(rule, "drug_rule_type", None)
            and eligibility_evaluation is None
            and "建议复查病理" not in reasoning
        ):
            reasoning = (
                reasoning.rstrip()
                + "\n建议复查病理文书及该项目是否自费后再最终定性 "
                "(本项目费用数据无自付明细、病理文书常缺, 药品违规存在系统性盲区)."
            ).strip()

        # OCR/脱敏质量门禁：技术失败是系统事实，不是患者风险事实。只要失败调用导致
        # 不明裁决、进入公开文案，或所谓违规没有独立可复核证据，就保守隔离为 CLEAN。
        # 有独立正向证据、且公开结果完全不引用技术失败的 VIOLATION 仍保留。
        failed_tool_calls = [
            record for record in tool_records
            if ToolExecutor.is_error_result(record.result)
        ]
        public_text = "\n".join(
            [reasoning]
            + [f"{item.locator}\n{item.text}" for item in evidence]
        )
        if (
            failed_tool_calls
            and eligibility_evaluation is None
            and (
                verdict != "VIOLATION"
                or _contains_technical_failure(public_text)
                or not any(_is_fact_evidence(item) for item in evidence)
            )
        ):
            self.emit(
                "[QualityGate] 技术失败未形成可复核异常证据，结果隔离为 CLEAN: "
                + ",".join(sorted({record.tool_name for record in failed_tool_calls}))
            )
            verdict = "CLEAN"
            confidence = 0.0
            reasoning = "未形成可复核的异常证据，本规则不输出风险判定。"
            evidence = []
            gate_tag = TOOL_FAILURE_GATE_TAG

        result = AuditResult(
            run_id=run_id,
            rule_id=rule.rule_id,
            patient_id=patient_id,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            evidence=evidence,
            tool_calls=tool_records,
            duration_ms=duration_ms,
            model=self.provider.model_name,
            started_at=started,
            gate_tag=gate_tag,
            precheck_tag=precheck_tag,
            eligibility_evaluation=eligibility_evaluation,
        )
        self.emit(
            f"[Verdict] {verdict[0]} conf={confidence:.2f} "
            f"duration={duration_ms / 1000:.1f}s tool_calls={len(tool_records)} run_id={run_id}"
        )
        return result


def stdout_emit(msg: str) -> None:
    """dry-run 用的标准输出回调."""
    print(msg, file=sys.stdout)
