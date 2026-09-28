# fix-scan-residuals — tasks

## 1. hub 空 ZYZD 兜底

- [x] 1.1 `hub_source.fetch_zd` jbk 查询后过滤 ZYZD 空串行 (ba_keys 从过滤后派生, main/zyzd_of 一致)
- [x] 1.2 单测: SYJBK 有行但 ZYZD 空 → 回退 IH 不生成空主诊 / jbk 有主诊但 zdk 零行只剩主诊

## 2. fees 匹配差异核验 (运维)

- [x] 2.1 跑 `scripts/diff_fee_match.py` 快照, 核对差异只含子串误归属修正、无丢费用回归 (Mac 3309 键/695681 行: 完全一致)
- [x] 2.2 结论记入 `docs/deployment_192_62.md` §10.4 ① (csv_loader 两级精确匹配安全网留档)

## 3. bff 契约通知 (文档)

- [x] 3.1 向 2C bff 发 run-batch SSE 语义变更说明 (deployment §10.3 ⑤ 补 done.completed=落库成功数 口径 + fail.stage)

## 4. build_overview 深拷贝护栏

- [x] 4.1 拆 `_build_overview_cached` (lru_cache) + `build_overview` 薄壳 deepcopy; `reset_caches` 清内层
- [x] 4.2 单测: 两次调用返回不同对象, 改一个不污染另一个

## 5. gate 降级 confidence 口径

- [x] 5.1 `runner` gate `outcome.changed` 分支: 原 conf 写进 `[gate: ...]` 注记 + confidence 归一 0.5
- [x] 5.2 单测: 降级后 conf=0.5 且 reasoning 含原值 / 未降级 conf 不变 (happy path 已覆盖)

## 6. 必留头部上限 + marker 语义

- [x] 6.1 `runner._truncate`: `len(head) > limit` 时对 head 硬截 + 头部截断提示
- [x] 6.2 `base.txt` 加一行解释 `====[必留头部结束]====` 标记语义
- [x] 6.3 单测: head 超预算硬截到上限量级 (更新 fix-drug-audit-precision 旧「头部全留」断言)

## 7. 验证

- [x] 7.1 `uv run pytest tests/ -v` 全绿 (565 passed + 1 skipped, +3 新测试)
