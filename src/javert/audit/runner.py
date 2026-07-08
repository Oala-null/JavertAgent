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
from javert.tools.llm_provider import LlmUnavailableError, Qwen35Provider
from javert.tools.tool_executor import ToolExecutor

from javert.data.fee_netting import net_fee_items

from .prompt_assembler import (
    assemble_system_prompt,
    initial_user_message,
    load_base_prompt,
    load_experience_doc,
    load_hospital_config,
)
from .precheck import CLEAN as PC_CLEAN, FACTS as PC_FACTS, PrecheckResult, run_precheck
from .result import AuditResult, Evidence, ToolCall
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
                logger.warning("net_fee_ctx 构建失败 patient=%s: %s (单次闸 fail-open)", patient_id, exc)
        self._net_fee_ctx_cache[patient_id] = ctx
        return ctx

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
    ) -> int:
        """执行一批 tool_call: 记录到 tool_records、把结果回灌对话, 返回成功次数.

        主循环与 repair 路径共用 — repair 响应含 tool_call 时也走这里续跑,
        不再丢弃. 成功次数供「至少 1 次成功 tool_call 才解锁裁决」判定.
        """
        tool_results_text: list[str] = []
        n_ok = 0
        for call in tool_calls:
            t0 = time.perf_counter()
            result_text, cached = self.executor.execute(call)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            truncated = _truncate(result_text, self.config.tool_result_max_chars)
            tool_records.append(ToolCall(
                tool_name=call["name"],
                arguments=call.get("arguments", {}) or {},
                result=truncated,
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
        n_success = 0  # 成功 (非错误串) 的 tool_call 次数 — 放行裁决的门槛
        verdict_data: dict[str, Any] | None = None
        final_reason = ""

        max_calls = self.config.max_tool_calls
        for turn in range(1, max_calls + 1):
            try:
                resp = self.provider.chat_with_retry(messages)
            except LlmUnavailableError:
                # 让上层决定是否重试 / skip — 此处直接抛
                raise

            content = resp["content"] or ""
            self.emit(f"[LLM #{turn}] {content[:1500]}{'...' if len(content) > 1500 else ''}")

            tool_calls = self.executor.parse_tool_calls(content)
            if tool_calls:
                n_success += self._execute_and_record(content, tool_calls, messages, tool_records)
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
            # 若含畸形 tool_call 标签, 回传具体 JSON 解析错误 (针对性反馈); 否则通用提示.
            tc_errors = self.executor.parse_errors(content)
            if tc_errors:
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
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": repair_prompt})
            try:
                resp_repair = self.provider.chat_with_retry(messages)
            except LlmUnavailableError:
                raise
            content_repair = resp_repair["content"] or ""
            self.emit(f"[LLM #{turn}-repair] {content_repair[:1500]}")
            # repair 响应含 tool_call → 执行并回主循环续跑, 不再丢弃直接 INCONCLUSIVE
            repair_calls = self.executor.parse_tool_calls(content_repair)
            if repair_calls:
                n_success += self._execute_and_record(content_repair, repair_calls, messages, tool_records)
                continue
            verdict_data = _parse_verdict_block(content_repair)
            if verdict_data is not None and n_success > 0:
                break
            # 二次失败 → INCONCLUSIVE
            final_reason = "malformed verdict JSON (repair failed)"
            verdict_data = None
            break

        else:
            # for-else: max_calls 用完未 break — 触顶兜底前先发 deadline turn
            # 给 LLM 最后一次基于已有 tool result 出 verdict 的机会 (fix-tool-call-budget-fallback).
            # 仅在已经调过工具时尝试 deadline; 完全没调过工具 → 不救, 走旧兜底.
            if tool_records:
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
                    self.emit(
                        f"[LLM #deadline] {content_deadline[:1500]}"
                        f"{'...' if len(content_deadline) > 1500 else ''}"
                    )
                    parsed = _parse_verdict_block(content_deadline)
                    if parsed is not None:
                        verdict_data = parsed
                        final_reason = "tool budget exhausted (deadline verdict accepted)"
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

        # --- 裁决后确定性 gate (add-verdict-gate-layer) ---
        # 解析完 verdict、构建 AuditResult 之前调 apply_gate; 仅作用 VIOLATION, 只降不升.
        gate_tag = ""
        if (
            verdict_data is not None
            and verdict == "VIOLATION"
            and str(self.config.verdict_gate).lower() != "off"
        ):
            outcome = apply_gate(
                verdict_data,
                rule,
                self._build_net_fee_ctx(patient_id),
                get_gate_config(),
                self._build_clinical_ctx(patient_id),
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
        )
        self.emit(
            f"[Verdict] {verdict[0]} conf={confidence:.2f} "
            f"duration={duration_ms / 1000:.1f}s tool_calls={len(tool_records)} run_id={run_id}"
        )
        return result


def stdout_emit(msg: str) -> None:
    """dry-run 用的标准输出回调."""
    print(msg, file=sys.stdout)
