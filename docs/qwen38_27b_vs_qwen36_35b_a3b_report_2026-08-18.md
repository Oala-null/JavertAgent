# Qwen3.8-27B 与 Qwen3.6-35B-A3B 在 Javert 的部署与 A/B 报告

日期：2026-08-18

测试主机：62（NVIDIA RTX 5880 Ada 48GB）

结论口径：部署可行性与 Javert 适配评估，不是临床金标准验证

## 1. 结论摘要

官方 `Qwen3.8-27B` 已在 62 完成可复现的 SGLang 部署验证，但**不应直接替换当前
Qwen3.6-35B-A3B-FP8 生产端点**：

- 62 的 48GB 显存放不下 55.6GB 的 BF16 原始权重，因此实际部署使用同一官方模型的
  `Qwen/Qwen3.8-27B-FP8`，不是第三方量化。
- Qwen3.8 成功完成权重加载、FP8 kernel、BF16 KV、Mamba cache、CUDA graph、OpenAI
  API 和 non-thinking 冒烟；未出现 scale 丢失、OOM、NaN 或空回复。
- 固定 4096 输入/256 输出的 72 请求性能测试全部成功，但 Qwen3.8 的输出吞吐只有
  Qwen3.6 的 21%～28%，并发 5 时为 72.93 vs 258.04 tok/s。
- Javert 配对回放在 85 个计划患者×规则对后触发安全性提前停止：Qwen3.8 成功 79、
  失败 6；与 Qwen3.6 的完全裁决一致率为 72.9%（62/85），成功对上的 Cohen's
  κ=0.147。
- 4 条 Qwen3.6 VIOLATION 中，Qwen3.8 保留为 VIOLATION 的为 0：3 条变成
  INCONCLUSIVE，1 条超时失败。该指标以现网模型为运行参考，不等同于真实临床召回率，
  但足以触发“不得静默切换”的安全门禁。
- 两个完整病例的总墙钟从 250.1 秒增至 3536.5 秒，慢 14.14 倍；工具调用从 446 次增至
  855 次，且候选少完成 6 条规则。主要问题是 Qwen3.8 多 `<tool_call>` 输出与 Javert
  现有文本协议不兼容，导致解析告警、调用膨胀、技术性 I 和 300 秒重试。

因此本次结果是：**保留 Qwen3.8 权重、隔离 runtime 和启动脚本作为研究部署；生产
30000 已恢复 Qwen3.6，Javert 配置未切换。** 若要重新评估，先改造/适配工具协议，再用
专家金标集重跑；不能依据官方通用榜单直接上线。

## 2. 模型与部署身份

| 项目 | Qwen3.6 基线 | Qwen3.8 候选 |
|---|---|---|
| 逻辑模型 | `Qwen3.6-35B-A3B`，MoE、约 3B 激活 | `Qwen3.8-27B`，27B dense |
| 部署权重 | `Qwen/Qwen3.6-35B-A3B-FP8` | `Qwen/Qwen3.8-27B-FP8` |
| revision | `95a723d08a9490559dae23d0cff1d9466213d989` | `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` |
| 上游 BF16 revision | — | `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` |
| 许可 | Apache-2.0 | Apache-2.0 |
| 模型目录 | 35GB | 29GB |
| 结构 | 40 层、hidden 2048、256 experts | 64 层、hidden 5120、dense |
| 配置原生上下文 | 262,144 | 262,144 |
| 本次服务上下文 | 65,536 | 65,536；实际总 token pool 47,158 |

来源：[Qwen3.8 BF16 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)、
[Qwen3.8 官方 FP8](https://huggingface.co/Qwen/Qwen3.8-27B-FP8)、
[Qwen3.6-35B-A3B-FP8](https://huggingface.co/Qwen/Qwen3.6-35B-A3B-FP8)。

Qwen3.8 下载后 81 份 Hugging Face metadata 全部固定到同一 revision；关键文件摘要：

| 文件 | SHA-256 |
|---|---|
| `config.json` | `74227dd615bf1ea975aa676bdf355a0379858c12f394b5365cd9dfa5fc2c70bc` |
| `model.safetensors.index.json` | `f0838c766951bdfe76d6afbdb2771a8f67aaa2231dedb3d33cebd817729843a2` |
| `tokenizer_config.json` | `b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27` |

### 2.1 Runtime

| 组件 | Qwen3.6 生产 | Qwen3.8 隔离环境 |
|---|---|---|
| SGLang | `0.0.0.dev1+gf5c225eeb` | `0.5.17` |
| Torch / CUDA | `2.9.1+cu128` / 12.8 | `2.11.0+cu129` / 12.9 |
| Transformers | 5.3.0 | 5.12.1 |
| SGLang kernel | 0.4.0 | `0.4.5+cu129` |
| XGrammar | 0.1.27 | 0.2.1 |

旧 runtime 虽能识别 Qwen3.8 架构，却出现
`gate_gate_up_proj.weight_scale_inv not found`，属于会破坏 dense FP8 权重加载的硬错误，
因此不能用旧 runtime 做候选服务。新 runtime 的二进制依赖来自 SGLang/PyTorch 官方索引；
经镜像传输的 wheel 均按官方索引 SHA-256 复核：

- `sglang-kernel 0.4.5+cu129`：`aa09af4599121558f813e7089caa9d4ff43e7a31b3794c0e48523085484484f6`
- `torch 2.11.0+cu129`：`68b83cb7d7d43bc67c2833c8aebaea6a966f2017c3389885affa3361c258b7e3`

部署参数基于 [SGLang Qwen3.8 cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B)
并按 Javert 负载调整。仓库启动脚本为
`deploy/launch_qwen38_27b_fp8.sh`，62 上安装为 `/home/admin2/launch_qwen3.8.sh`。

### 2.2 已验证的候选资源包络

| 指标 | Qwen3.6 | Qwen3.8 |
|---|---:|---:|
| GPU 全卡空闲常驻 | 约 39.94GB | 约 43.62GB |
| 增量 | — | +3.68GB |
| Qwen3.8 权重本体 | — | 28.47GB |
| Qwen3.8 Mamba cache | — | 5.88GB（40 slots） |
| Qwen3.8 BF16 KV | — | 2.88GB / 47,158 tokens |
| CUDA graph 后余量 | — | 约 5.0GB |

虽然 Qwen3.8 的磁盘权重少约 6GB，但 dense 计算和更大的状态/cache 使其常驻显存反而更高。
`context_length=65536` 是接口上限，不代表单卡能同时保留 65,536 个 KV token；本次实际
总 pool 为 47,158，应把它视为生产长上下文限制。

## 3. 评价体系

### 3.1 六层评价框架

| 层级 | 目的 | 核心指标 | 本次状态 |
|---|---|---|---|
| L0 身份与供应链 | 确认测的是哪份权重 | repo、revision、许可、文件哈希 | 通过 |
| L1 服务正确性 | 确认能稳定生成 | load、health、schema、OOM/NaN、thinking 开关 | 通过 |
| L2 Agent 协议 | 确认能按 Javert 工具协议收敛 | 解析失败、工具次数、轮数、超时、失败率 | 失败 |
| L3 领域配对回放 | 同输入比较裁决迁移 | 混淆矩阵、κ、V 丢失、新增 V、I/失败 | 失败 |
| L4 专家金标 | 评价真实临床准确性 | sensitivity、specificity、PPV、NPV、F1、coverage-risk | 未执行 |
| L5 生产效能 | 评价单卡 SLA 和成本 | 吞吐、p50/p95/p99、患者墙钟、显存、长稳 | 失败 |

### 3.2 金标集的科学设计

下一轮不能再把 Qwen3.6 当“真值”。建议建立受控专家集：

1. 从 ready 规则按科室、违规类型、Router 命中、费用规模和数据完整度分层抽样。
2. 至少 300 个患者×规则对；关键规则至少各有 30 个阳性和 30 个困难阴性。真实低基率
   分布另保留一份，用于估算 PPV 和工作量。
3. 两名医保/临床专家在不知道模型身份与互相答案的前提下独立标 V/I/C；不一致由第三人裁决。
4. V 作为阳性、C 作为阴性；I 单独作为 abstention，报告 coverage、covered-set risk，
   不把 I 粗暴并入“错”或“对”。
5. 报告 sensitivity、specificity、PPV、NPV、macro-F1、Brier/ECE（若置信度可校准）及
   每规则结果；比例给 Wilson 95% CI，模型差异用配对 McNemar，耗时/成本用患者级 bootstrap。
6. 固定 prompt、规则、Router、知识资产、温度、thinking、最大 token、并发、硬件和 runtime；
   模型顺序随机或交叉，边界分歧至少复跑 3 次以测运行噪声。

建议上线硬门禁：

- 服务成功率 ≥99.5%，技术失败、malformed 和超时均为 0；
- 专家金标 sensitivity 下置信界不低于基线 3 个百分点，specificity 同理；
- 未经专家确认，不允许任何基线 V 静默降为 C/I；
- 同硬件并发 5 吞吐不少于基线 80%，或有明确容量扩容方案；
- 患者级 p95 墙钟不超过基线 1.25 倍；
- 30 分钟 soak 无 OOM、NaN、队列持续增长和显存爬升。

本次在 L2 即失败，因此 L4 未启动；下文“质量”只描述与现网运行参考的行为差异，不能解释为
临床准确率。

## 4. A/B 方法

### 4.1 固定条件

- 同一台 62、同一张 RTX 5880 Ada 48GB，两个模型顺序加载，避免争抢显存。
- 同一 Javert HEAD：`9dad8c9f7cb2b05c82e6a94f50b88965707c8aeb`。
- ready/drafting：118/41，共 159；只运行 ready。
- 基础 prompt：`2bd43f0eb67011c764bf0a7efd4923bf4b66f4b04fbe9a96baf19b3017de66af`。
- `configs/llm.yaml`：`333ed35adc56d4cc3cd82b482710a742e76199e5a40e580e469b6a791e3af4b2`。
- Router index：`4847fa3058a1baf6444bf549fa72ae40c657237a08c28ddb1e7eb4172d98b093`。
- 规则聚合：`7dbd3b24f1c397e4a721270fa99b1462b66421a24a0d1e0667f48dedf7790ca3`。
- `temperature=0`、thinking 关闭、`max_tokens=8192`、Javert 并发 5、LLM timeout 300 秒。
- 使用 `scripts/compare_llm_efficiency.py`；显式 `JAVERT_SQL_ENABLED=false`，不写 SQLite、
  SQL Server 或工作台。
- 患者只在受保护原始输出中存在；本报告用 Case-A、Case-B 两个语义别名。

Qwen3.6 与 Qwen3.8 必须使用不同 SGLang runtime，因此本报告比较的是两个**可部署 bundle**，
不是只替换权重的纯模型实验。性能和协议差异都应按端到端替换成本理解。

### 4.2 性能负载

从现网日志 40,953 个 prefill 事件得到：new-token p50=1,147、p90=3,480、p95=4,096；
运行请求 p50=3、p95=4、max=6。性能曲线因此固定为：

- random token IDs，seed `20260818`；
- 每模型、每并发 24 请求；输入 4,096、输出 256；
- 并发 1/3/5，发压前 3 个 warmup 并 flush cache；
- 两边使用同一旧版 `sglang.bench_serving` 客户端。

该 harness 在 Qwen3.6 上没有可靠 TTFT/ITL（报告为 0），所以本报告不比较 TTFT/ITL，避免把
客户端测量缺陷解释为模型优势。

### 4.3 提前停止

原计划 3 个病例、152 对。完成 Case-A/Case-B 共 85 对后，Qwen3.8 已出现基线 V 丢失、
6 个失败、技术性 I、多次 300 秒重试和超过 10 倍的墙钟退化，因此按安全性/效能 futility
停止；第三例仅启动 3 条、未形成病例 JSON，不纳入任何统计。

## 5. 性能结果

所有性能请求均成功（每模型 72/72）。

| 并发 | 指标 | Qwen3.6 | Qwen3.8 | Qwen3.8 / Qwen3.6 |
|---:|---|---:|---:|---:|
| 1 | 输出吞吐 tok/s | 93.80 | 20.10 | 21.4%（慢 4.67×） |
| 1 | 输入吞吐 tok/s | 1500.87 | 321.62 | 21.4% |
| 1 | mean E2E | 2.73s | 12.73s | 4.67× |
| 1 | p99 E2E | 2.77s | 12.89s | 4.65× |
| 3 | 输出吞吐 tok/s | 198.03 | 54.59 | 27.6%（慢 3.63×） |
| 3 | 输入吞吐 tok/s | 3168.56 | 873.51 | 27.6% |
| 3 | mean E2E | 3.87s | 14.06s | 3.63× |
| 3 | p99 E2E | 3.92s | 14.35s | 3.66× |
| 5 | 输出吞吐 tok/s | 258.04 | 72.93 | 28.3%（慢 3.54×） |
| 5 | 输入吞吐 tok/s | 4128.72 | 1166.92 | 28.3% |
| 5 | mean E2E | 4.79s | 16.90s | 3.53× |
| 5 | p99 E2E | 8.02s | 17.49s | 2.18× |

Qwen3.8 是 dense 模型，Qwen3.6 是 3B 激活的 MoE；“27B 少于 35B”不代表推理更快。
在本卡和本 runtime 下，Qwen3.6 有决定性的吞吐优势。

## 6. Javert 领域回放

### 6.1 病例级结果

| 病例 | 规则 | Qwen3.6 V/I/C | Qwen3.8 V/I/C/失败 | 3.6 墙钟 | 3.8 墙钟 | 倍率 |
|---|---:|---|---|---:|---:|---:|
| Case-A | 30 | 0/1/29 | 0/3/26/1 | 77.4s | 1005.1s | 12.99× |
| Case-B | 55 | 4/3/48 | 1/11/38/5 | 172.7s | 2531.4s | 14.66× |
| **合计** | **85** | **4/4/77** | **1/14/64/6** | **250.1s** | **3536.5s** | **14.14×** |

工具行为：

| 指标 | Qwen3.6 | Qwen3.8 |
|---|---:|---:|
| 成功规则 | 85 | 79 |
| 总 LLM 轮数 | 238 | 225（分母更小） |
| 已记录工具调用 | 446 | 855（分母更小） |
| 双方成功规则上的平均工具调用 | 4.73 | 10.82 |
| 配对 LLM 规则耗时倍率 p50/p90/p95 | — | 3.42× / 37.44× / 58.92× |
| 最大单规则 | 144.3s | 1013.6s；另有多条 912s 后失败 |

### 6.2 裁决混淆矩阵

行是 Qwen3.6，列是 Qwen3.8：

| Qwen3.6 \ Qwen3.8 | V | I | C | FAILED |
|---|---:|---:|---:|---:|
| V | 0 | 3 | 0 | 1 |
| I | 0 | 1 | 3 | 0 |
| C | 1 | 10 | 61 | 5 |

- 把失败计为不一致：完全一致 62/85 = 72.9%；Wilson 95% CI 62.7%～81.2%，
  患者×规则对 bootstrap 95% CI 63.5%～82.4%。
- 仅双方成功：62/79 = 78.5%，Cohen's κ=0.147，说明高一致率主要由 CLEAN 低基率主导，
  去掉偶然一致后稳定性很弱。
- 候选失败率 6/85 = 7.1%，Wilson 95% CI 3.3%～14.6%。
- 相对运行参考的 V 保留率 0/4，Wilson 95% CI 0%～49.0%。样本很小，不能当临床
  sensitivity；但生产替换不能接受未经专家确认的 3 个 V→I 和 1 个 V→FAILED。
- 23 条需人工处理：17 条成功但裁决不同，6 条技术失败。

### 6.3 根因

主要根因不是单纯 decode 慢，而是三者叠加：

1. **工具协议漂移**：Qwen3.8 会连续输出多个 `<tool_call>` 块或嵌套标签，Javert 当前
   文本解析器多次报 `Extra data`；部分规则零成功工具调用后落 INCONCLUSIVE。
2. **调用膨胀**：单规则最高记录 275 次工具调用；平均工具量为基线 2.29 倍以上。
3. **调度与 timeout**：Javert 每请求预留 8192 输出 token，候选 47k token pool 在真实
   长提示下常只能同时运行约 2 条，其余排队；300 秒客户端 timeout 导致最多 3 次重试，
   形成约 912 秒的失败长尾。

不能用调低并发、增大 timeout 或把 I 当 CLEAN 来“修饰”结果。正确顺序是先让 Qwen3.8
走原生 tools 协议或新增严格兼容适配层，给总工具数硬上限，再重新测量。

## 7. 当前部署状态与运维

| 项目 | 当前状态 |
|---|---|
| 生产 30000 | 已恢复 `Qwen/Qwen3.6-35B-A3B-FP8` |
| Javert `.env` | 未改；仍使用 30000，模型走代码默认 Qwen3.6 |
| Qwen3.8 权重 | `/home/admin2/models/Qwen/Qwen3.8-27B-FP8-017b9c7a` |
| Qwen3.8 runtime | `/home/admin2/sglang_qwen38_env` |
| Qwen3.8 启动脚本 | `/home/admin2/launch_qwen3.8.sh`（研究用途，非 systemd） |
| Javert Web | systemd active；`/login` HTTP 200；`/api/health` HTTP 200/status=ok |
| 最终 LLM 冒烟 | Qwen3.6 返回 `OK`，reasoning_tokens=0，finish=`stop` |

研究窗口复启 Qwen3.8 前必须确认无审计任务，并停止当前 30000；单卡不能让两个 FP8 服务
共存。启动示例：

```bash
nohup /home/admin2/launch_qwen3.8.sh \
  > /home/admin2/qwen38-fp8-sglang.log 2>&1 < /dev/null &
```

回滚：停止候选主 PID，确认 30000 释放，再执行：

```bash
nohup /home/admin2/launch_qwen3.6_mem080.sh \
  > /home/admin2/qwen36-restore.log 2>&1 < /dev/null &
```

回滚后必须验证 `/v1/models`、non-thinking `OK`、Javert Web active 和登录页 HTTP 200。

## 8. 后续建议

1. 暂不切生产；保持当前 Qwen3.6。
2. 为 Qwen3.8 新建协议适配 change：优先走 OpenAI 原生 `tools`，或严格解析其 XML
   function/parameter 格式；增加每轮、每规则总工具调用硬上限和重复调用去重。
3. 在协议单测中覆盖并列 tool calls、嵌套/重复标签、thinking 关闭、deadline verdict、
   malformed repair 和客户端取消。
4. 先用合成无 PHI 的 30 条协议集达到 100% 解析/收敛，再跑本报告的 85 对回归。
5. 通过 L2/L3 后再构建双盲专家金标集；未通过金标门禁前，官方通用 benchmark 只能作为
   研发信号，不能作为医保审计上线依据。
