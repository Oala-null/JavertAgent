## 1. Phase 1 — 模板 + init

- [x] 1.1 写 `docs/templates/模板4_超标准收费.md` (phase 0 完成)
- [x] 1.2 装 `configs/templates/M4.yaml` (master_prompt + 14 fields)
- [x] 1.3 跑 `javert template validate M4` 通过
- [x] 1.4 init 9 条骨架 (R020 R063 R074 R165 R212 R250 R291 R292 R293); R193/R196/R200 已 init
- [x] 1.5 改 12 条 priority 到 P0

## 2. Phase 2 — 12 条 vars + prompt-fit

- [x] 2.1 生成 12 条 vars json (脚本批量 + 个别手调)
- [x] 2.2 batch prompt-fit 写盘
- [x] 2.3 验证: 12 条 prompt_addon >= 500c, trigger_keywords >= 5

## 3. Phase 3 — 验收

- [x] 3.1 batch mark 12 条 ready
- [x] 3.2 `javert list` 验证 ready:77
- [x] 3.3 跑 audit-patient J66252 组 K (52 条 P0)
- [x] 3.4 docs/sample_audit_patient.md 加组 K + 六模板汇总
- [x] 3.5 157 tests 全绿
- [x] 3.6 更新 CLAUDE.md + README

## 4. 验收

- [x] 4.1 spec "M4 template validation passes" 通过
- [x] 4.2 spec "M4 template can render with vars" 通过
- [x] 4.3 spec "M4 set is loadable and all ready" 通过
- [x] 4.4 spec "M4 set passes audit-patient sanity" 通过
