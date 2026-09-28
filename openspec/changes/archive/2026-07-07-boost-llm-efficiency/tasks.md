# boost-llm-efficiency — tasks

## 1. prompt 布局与多调用

- [x] 1.1 `prompt_assembler.py` tools 段挪到规则个性化段之前 (段内容逐字不动)
- [x] 1.2 单测: 两规则公共前缀 ≥ tools 段末尾 / 同规则挪位前后段集合与文本逐字一致
- [x] 1.3 `base.txt` 加「同一轮可并列多个 tool_call」小节 + 双调用示例 (保留分轮追问自由度)
- [x] 1.4 5 患者 dry-run 对照 (J66252/J18906/J90508 + szx 211530148/211345984): 同代码基线 107 条 0 V 级差异; 平均轮数 8-9 → 3.25-4.6 → `docs/boost_llm_efficiency_实测.md`

## 2. 工具缓存真并发

- [x] 2.1 `tool_executor.py` `_cache_lock` 改两层 per-key compute-once 锁
- [x] 2.2 单测: 不同 key 不互斥 / 同 key 双并发单次计算 / `--share-tool-cache` 命中回归

## 3. fee 明细信号

- [x] 3.1 `search_fees` 行输出追加 `单价×数量` + 开单科室/开单医师, 缺列整体省略
- [x] 3.2 单测: 量价/科室医师出现 / 缺列省略回归 / 行锚与既有列文本不变
- [x] 3.3 hit_resolver 回归确认 (锚点 join 不受加列影响)

## 4. 工作台顺手项

- [x] 4.1 `build_overview` 挂 lru_cache, 失效接进既有 reset 钩子 + 单测
- [x] 4.2 detail 页不复跑全表 ROW_NUMBER CTE — 实现为 sidebar 短 TTL 缓存 (按 pid 窄查询会砍掉侧栏, 见 design D5 修正), 列表页不动

## 5. 实测与部署

- [x] 5.1 `uv run pytest tests/ -v` 全绿 (562 passed + 1 skipped, 含 +14 本 change 新测试)
- [x] 5.2 62 升级 (src 已部署), 单患者 `--concurrency 5` 冒烟通过 (J66252 29 条 0 失败, verdict 与 Mac 一致); ⚠ javert-web systemd 重启需 sudo 待手动
- [x] 5.3 10 患者小批实测 (6/10 完成即被外部中止, 累计 11 患者/499 条已足): cache hit 73%→76-79%, 轮数 8-9→3.66, 吞吐 +12% (未减半, 归因见文档) → `docs/boost_llm_efficiency_实测.md`
