## 1. Phase 1 — 高耗时段 (R208 R228 R226)

- [x] 1.1 写 `docs/m1_R208_vars.json` (基础麻醉 + 强化麻醉; fee_category=其他类)
- [x] 1.2 跑 `uv run javert prompt-fit R208 --template M1 --vars docs/m1_R208_vars.json`; "✓ R208 written from M1"
- [x] 1.3 写 `docs/m1_R228_vars.json` (重症监护 + 各类基础护理; fee_category=其他类)
- [x] 1.4 跑 prompt-fit R228 写盘
- [x] 1.5 写 `docs/m1_R226_vars.json` (静脉高营养 + 静脉用药集中配置; fee_category=其他类)
- [x] 1.6 跑 prompt-fit R226 写盘
- [x] 1.7 跑 `uv run javert dry-run R208 --patient J66252`, 看 trace — **82.8s, verdict=C conf=0.95, 4 tool_calls, LLM 走完 5 步审计**
- [x] 1.8 R208 dry-run 通过, 继续

## 2. Phase 2 — 血液净化 + 影像

- [x] 2.1 写 `docs/m1_R077_vars.json` (血透 + 监测费; fee_category=检查类)
- [x] 2.2 跑 prompt-fit R077 写盘
- [x] 2.3 写 `docs/m1_R069_vars.json` (血液净化 + 不能单独收的一次性耗材; fee_category=耗材类)
- [x] 2.4 跑 prompt-fit R069 写盘
- [x] 2.5 写 `docs/m1_R112_vars.json` (CT/MRI 增强 + 平扫; fee_category=检查类)
- [x] 2.6 跑 prompt-fit R112 写盘
- [x] 2.7 写 `docs/m1_R118_vars.json` (MRI 引导 + MRI 扫描; fee_category=检查类)
- [x] 2.8 跑 prompt-fit R118 写盘
- [x] 2.9 写 `docs/m1_R119_vars.json` (CT 引导 + CT 扫描; fee_category=检查类)
- [x] 2.10 跑 prompt-fit R119 写盘
- [x] 2.11 写 `docs/m1_R116_vars.json` (超声图文报告 + 彩色打印/胶片; fee_category=检查类)
- [x] 2.12 跑 prompt-fit R116 写盘
- [ ] 2.13 跑 `uv run javert dry-run R077 --patient J66252`, 看 trace — **跳过 (R208 已确认 prompt 形态, 后续抽样足够)**

## 3. Phase 3 — 余 4 条 + R045 推 ready

- [x] 3.1 写 `docs/m1_R047_vars.json` (关节镜下手术 + 关节镜加收; fee_category=手术类)
- [x] 3.2 跑 prompt-fit R047 写盘
- [x] 3.3 写 `docs/m1_R185_vars.json` (根治性宫颈切除术 + 卵巢动静脉高位结扎术; fee_category=手术类)
- [x] 3.4 跑 prompt-fit R185 写盘
- [x] 3.5 写 `docs/m1_R260_vars.json` (牙龈翻瓣术 + 根面平整术; fee_category=手术类)
- [x] 3.6 跑 prompt-fit R260 写盘
- [x] 3.7 写 `docs/m1_R300_vars.json` (精神科监护 + 抗精神病药物治疗监测; fee_category=其他类)
- [x] 3.8 跑 prompt-fit R300 写盘
- [ ] 3.9 跑 `uv run javert dry-run R300 --patient J66252`, 看 trace — **跳过 (R208 已确认形态)**

## 4. Phase 4 — 验收

- [x] 4.1 批量 mark 14 条 (R045 R047 R069 R077 R112 R116 R118 R119 R185 R208 R226 R228 R260 R300) ready
- [x] 4.2 `uv run javert list` 验证: 末行 `ready:15` (含 R191)
- [x] 4.3 全 15 条 `derived_from_template=M1` 校验通过 (含 R191, 见 4.4)
- [x] 4.4 R191 复跑 `prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json` 写回 — prompt_addon 620 chars byte-equal 保持; derived_from_template=M1 增加; 然后 mark ready (原 status=drafting, 非已 ready, 设计描述错误)
- [x] 4.5 跑 `audit-patient J66252 --share-tool-cache --rules R045,...,R300` — **18.6 min, V=0 C=15 I=0, avg 74.5s/rule, tool cache hit 35.3%**
- [x] 4.6 sample_audit_patient.md 新增 "组 E: m1-rollout 后" 节, 含逐条耗时表 + 与组 A 对比 + 结论
- [x] 4.7 全部单测 `uv run pytest tests/ -v` 仍 134 passed (无回归)

## 5. Phase 5 — 文档收尾

- [x] 5.1 更新 `CLAUDE.md` 当前阶段标记: 标 ✅ "m1-rollout (15 条 M1 ready)"
- [x] 5.2 更新 `README.md` 路线图: m1-rollout ✅ done; m2-rollout 下一候选
- [x] 5.3 `openspec validate m1-rollout --strict` 通过

## 6. 验收

- [x] 6.1 spec scenario "M1 set is loadable and all ready" 通过 (`javert list` 显示 ready:15 / 41 条)
- [x] 6.2 spec scenario "derived_from_template invariant for M1 set" 通过 (全 15 条 derived_from_template=M1, min prompt_addon=620, min trigger_keywords=5)
- [x] 6.3 spec scenario "audit-patient sanity" 通过 — 15 条全部产出 verdict (V=0 C=15 I=0), 0 failed, 0 prompt 解析错
