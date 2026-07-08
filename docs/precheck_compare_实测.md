# precheck ON vs OFF 对照实测 (pilot-deterministic-precheck 任务 5.3)

- 患者数: **100** · M1(precheck) 规则: 21 · 判定单元: 2100

## 1. LLM 调用降幅

- ON 总 chat 次数: **4** · OFF 总 chat 次数: **6993**
- **LLM 调用降幅: 99.9%** (目标 ≥50%)
- 短路 CLEAN (0 LLM): 2099/2100 (100.0%) · facts→LLM: 1 · skip(数据缺): 0

## 2. V/I/C 分布对照 + 0 漏检

| verdict | ON | OFF |
|---|---|---|
| VIOLATION | 1 | 2 |
| INCONCLUSIVE | 0 | 12 |
| CLEAN | 2099 | 2086 |

- **短路漏检 (OFF=V 而 ON 短路 CLEAN): 1** ⚠️
  - MISS J90508 / R228
- verdict 翻转总数 (含 facts 路径 LLM 噪声): 13
  - C→I: 12
  - C→V: 1

## 3. 新 V 证据机器锚点覆盖

- ON facts 路径判 V: 1 · 带 fee 锚点: 1 · **覆盖率 100.0%** (目标 100%)


---

## 4. 解读与关键发现 (成色)

### 4.1 短路是主战场, 且比自由 LLM 更可靠
- 100 患者 2100 判定单元中 **2099 短路 CLEAN (0 LLM)**, 全数据集 3309 患者扫描: 20/21 条 M1 规则**没有任何患者**出现 A∩B 并存 → 短路是 M1 的绝对主路。
- 唯一「短路漏检」告警 (J90508/R228) 经复核为 **OFF 侧 LLM 噪声**: 同规则 precheck-off 重跑即翻回 CLEAN (「A 类无命中 → CLEAN」)。precheck 的确定性 CLEAN 稳定正确, 自由 LLM 才是抖的那个 (13 次 verdict 翻转全来自 OFF 侧, ON 侧确定性无翻转)。
- **0 真漏检**: 无任何「precheck 短路 CLEAN 掩盖了真违规」的情形。

### 4.2 facts 路径极稀疏, 全集中在 R069 (血液透析)
- 全 3309 患者确定性扫描: A∩B 并存仅 **4 例, 全部 R069**: J24278 / J59453 / J94935 / K01731。M1「重复收费」并存在真实数据里是极少数模式。
- 这 4 例 = M1 真正需要 LLM 判断的**全部审计面**; precheck 把它们直接送到窄问题 (核实反证) 并带机器锚点。

### 4.3 facts 路径暴露 R069 项目集过粗 (→ make-rules-code-portable)
4 例 facts 逐一 ground-truth (确定性 A/B 命中):

| 患者 | 真实 dialysis 耗材? | ON 判 | 说明 |
|---|---|---|---|
| J24278 | ✅ 血液透析滤过器及配套管路 + 置换基础液 | V | 真并存, V 合理 |
| J94935 | ✅ 血液滤过器 / 血液灌流器 / 过滤管路 | V | 真并存, V 合理 |
| J59453 | ⚠️ 有透析滤过器但无独立治疗费 | C | 边界, 修复后倾向保守 CLEAN |
| K01731 | ❌ 仅治疗费 + 血管通路导管 + **呼吸**过滤器 | C | **原为假阳性 V, 已修** |

根因: R069 的 `a_items` (血液透析/血液滤过…) 会子串命中**耗材名** (「一次性使用血液透析滤过器」含「血液透析」), `b_items` 的 `滤器`/`管路` 会命中**呼吸**耗材 (呼吸过滤器/湿热交换器)。子串匹配分不清「血液净化**服务**」与「耗材」, 也分不清透析滤器与呼吸滤器 → R069 的 facts/并存不可靠。**这是规则项目集精度问题, 属 `make-rules-code-portable` 范畴, 非 precheck 层缺陷。**

### 4.4 已做的精度修复 (fact block 恢复语义护栏)
narrowing 原本告诉 LLM「A∩B 已并存, 只查反证」, 使 LLM 无从发现 B 其实与 A 无关 (K01731 的呼吸滤器被当透析耗材)。已改 `_build_fact_block` 为**两步**: (1) 先确认 B 确为 A 的内涵/附属 (无关项如呼吸耗材予以排除), (2) 再核实反证。B 项目名本就在事实块里, LLM 无需重搜费用即可做语义排除 —— 保留效率 + 恢复对 precheck 模糊匹配的语义 sanity check。修复后 K01731 假阳性 V → CLEAN, 真并存 (J24278/J94935) 仍 V。

### 4.5 结论
- **precheck 层验证通过, 可上线**: 短路安全 (0 真漏检) + LLM 调用降 99.9% + facts 路径机器锚点 100%。
- **facts 路径的 V 可信度受限于规则项目集精度** (当前仅 R069 有 facts, 且其项目集过粗) → 交 `make-rules-code-portable` 精化 a_items/b_items 后再对 R069 facts 判定加压。
- ③conf 闸 (评估项 5.4): 本轮 facts V 均 conf 0.90 ≥ ceiling 0.85, 未被 ③ 压制; 暂无需为 precheck V 放宽 ③。

> 数据: `output/precheck_cmp/` (100 患者 ON/OFF) + `output/precheck_facts/` (4 facts 患者). 复现: `scripts/compare_precheck.py` + `scripts/report_precheck.py`。
