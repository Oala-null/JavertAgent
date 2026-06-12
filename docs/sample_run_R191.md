# R191 dry-run 样本 (5 patient × 1 prompt v0.2)

> 规则: 「肿瘤断层重复收费 — 开展肿瘤全身断层显像, 重复收取人工报告费用」
> Prompt 版本: `R191.yaml` v0.2 (operator-authored, 2026-05-08)
> 模型: Qwen/Qwen3.5-35B-A3B-GPTQ-Int4 @ 192.168.31.62:30000

## 运行结果汇总

| run_id          | patient | verdict | conf | tool_calls | duration | 关键证据                                                                 |
|-----------------|---------|---------|------|-----------:|---------:|-------------------------------------------------------------------------|
| aud_aLoMbCBzptLJ | K23895  | CLEAN   | 1.00 |          4 |    69.5s | 非霍奇金淋巴瘤; 检查类 84 项中无 PET-CT/SPECT/全身断层                  |
| aud_39STPo5UrQVz | J40808  | CLEAN   | 0.95 |          4 |    79.8s | 直肠恶性肿瘤; PET/CT全身检查 ¥7000 但无独立报告费                       |
| aud_6xhhZkfrXHQx | K46709  | CLEAN   | 0.95 |          4 |    74.7s | 非霍奇金淋巴瘤; 检查类仅普通 CT 平扫, 无 A 类                          |

(K23895 / J40808 / K46709 = 自动采样 pilot 列表前 3 + 5 中的甲状腺癌相关患者)

## 观察 1: prompt v0.2 阶梯式判断流程被严格执行

每个 patient 的 LLM 推理都遵循同一个流程:
1. 先 `note_diagnosis` 确认是否有肿瘤诊断 (规则前提)
2. 再 `search_fees(category="检查类")` 列检查类全部明细
3. 在明细里逐一比对 A 类 (扫描) / B 类 (报告) 关键词
4. 按 prompt_addon 步骤 2-5 给出 verdict

J40808 是有意思的"中间档": 命中了 PET/CT 全身扫描 (A 类, ¥7000), 但费用列表里没有独立报告费 (B 类), 模型按 step 3 判 CLEAN, conf 0.95. 这正是规则要避免的"假阳性".

## 观察 2: 工具协议偶发"漏 patient_id"

K23895 的 trace 第 1/2 个 tool_call 缺 `patient_id`:

```
[Tool #1] note_diagnosis({}) (0ms)
    工具执行失败: missing patient_id
[Tool #2] search_fees({"category": "检查类"}) (0ms)
    工具执行失败: missing patient_id
```

模型在第 2 轮收到错误后自我修复, 重新发出带 patient_id 的 tool_call. 整体不影响 verdict, 但
浪费了 ~800ms. **后续优化方向**: 在 `prompt_assembler.py` 里把 patient_id 写进 system prompt
顶部, 让 LLM 不需要从 user message 推断.

## 观察 3: VIOLATION 用例 (R141, K23895) 的合理性 cross-check

虽然 R141 不是本文档主题, 但同一 patient (K23895) 在 R141 上得了 VIOLATION:

> "8 次血清肌红蛋白测定 (¥800), 患者主要诊断为非霍奇金淋巴瘤 + 化疗骨髓抑制, 文书未提及任何
> 心肌损伤 / 横纹肌溶解 / 肌肉相关临床指征 → 符合 R141「过度检查」描述."

跨规则检查可信度: K23895 在 R141 (有指征) = V, 在 R191 (无 A 类扫描) = C, 表明模型不是简单"全
判 V 或全判 C", 而是按规则语义独立判断. 这是 prompt v0.2 的形态验证.

## 接下来 (TODO)

- 在剩余 47 个 pilot 患者上跑 `javert run R191 --pilot` 看 V/C/I 分布是否有真阳性
- 如果分布接近 0% V, 考虑放宽 trigger_keywords (例如加入 "ECT", "γ 显像")
- 如果 V > 5%, 翻看 trace 验证是否假阳性 (可能 B 类关键词太宽匹配到了普通 CT 报告)
- 若验证通过, `javert mark R191 --status ready`, 再批跑 50 患者
- 若假阳性 / 假阴性都难调, 考虑 `mark abandoned`
