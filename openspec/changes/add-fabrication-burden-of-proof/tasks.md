# Tasks: add-fabrication-burden-of-proof

> 前置: `add-fn-regression-library` 已落 (FN-001/002 为验收判据)。与 `recover-deterministic-recall` 无逻辑依赖可并行, 但两者都碰 precheck 邻域 (对方改净额 helper / 本方加 mode 字段) — 并行开发时先沟通 `precheck.py` 改动面, 或串行避开合并冲突

## 1. companion 预检模式 (引擎面先行, 确定性可单测)

- [x] 1.1 `Rule.precheck` 加可选 `mode` 字段 (缺省 coexist), `precheck.py` 实现 companion 三态 (A无→clean / A有B无→facts / 双有→skip 不注偏置); facts→evidence 机器锚点复用既有机制 → 单测: 三态 + 缺省 mode 行为逐字不变 + facts evidence 锚点
- [x] 1.2 调研 ~20 条高价术式必备配套存 `docs/companion_术式配套表.md` (溶栓/支架/球囊/取栓等; 本 change 只消费溶栓 1 条, 余为素材库)

## 2. M5 举证倒置

- [x] 2.1 `configs/templates/M5.yaml` master prompt 加"证据不足默认 I / 正面反证才 C"裁决语义段 + 名称不匹配警示行 (禁止因名称不同断言未收费/未执行), 重渲染 M5 派生规则
- [x] 2.2 抽 2 条既有 M5 规则 dry-run 对照 (改前改后各 1 轮) 确认无回归; FN-005 anchor 不退化

## 3. 两条覆盖空白规则

- [x] 3.1 查 0325 清单 H 类条目, 确定溶栓虚构 / 内镜治疗虚构归属 (细化既有 vs 新建 rule_id); 回填 FN-001/002 案例文件的 rule_id
- [x] 3.2 写溶栓虚构规则: companion precheck (A=溶栓术式, B=尿激酶/阿替普酶/rt-PA/替奈普酶/瑞替普酶...) + prompt (手术记录反证三层递进); 重建 router index
- [x] 3.3 写内镜治疗虚构规则 (M5 派生, 治疗费 vs 操作记录执行证据); 重建 router index
- [x] 3.4 dry-run 211351896 / 211427558 各 ≥5 轮看 trace 调 prompt → FN 回归: FN-001/002 至少 partial (I + 证据点名费用行)

## 4. 端到端 + 部署

- [x] 4.1 `uv run pytest tests/ -v` 全绿; FN 回归 --against-baseline: FN-001/002 升档, 其余不跌档
- [ ] 4.2 62 部署 (tar src+configs), 正式重跑 211351896 / 211427558 (SQL_ENABLED=true) 上工作台交专家复核
- [x] 4.3 `Javert/CLAUDE.md` 变更日志; `Javert问题汇总.md` 案例①②标注结论
