# Tasks: add-fn-regression-library

## 1. 案例库

- [x] 1.1 定案例 yaml schema (pydantic 校验, expected_verdict 允许 CLEAN 误判修正锚) + 建 `tests/fn_cases/`, 登记首批 4 例: FN-001 211351896×TBD-溶栓虚构 / FN-002 211427558×TBD-内镜治疗虚构 / FN-003 211419211×R155 / FN-005 211318013×R225 (期望 V + 证据含「关节松动训练」「颈椎」)。FN-004 (211440399×R063) 编号保留**不登记** — 专家未裁定 ground truth (75% 收费疑似正确, 老 V 疑似证据 bug 所致假阳性), 待 recover change 漂移报告送裁后回填

## 2. 回归 runner

- [x] 2.1 写 `scripts/fn_regression.py`: 逐例 dry-run 真 LLM (`JAVERT_SQL_ENABLED=false` 不落生产库), 三档判定 full/partial/miss (严重度下限断言), TBD 规则判 miss 注明"无规则", 报告 + catch 率
- [x] 2.2 hub 患者自动取数: `data_source: hub` → `etl_from_data_hub` 到 `output/fn_cache/<pid>/` + env 覆盖, 缓存命中跳过
- [x] 2.3 基线模式: `--save-baseline` 存 `docs/fn_baseline.md`, `--against-baseline` 比对跌档非零 exit; 可选 `--repeat N` 多数投票

## 3. 基线验证

- [x] 3.1 跑基线并核对与人工归因一致: 预期 FN-005 full / FN-003 miss (单次闸) / FN-001/002 miss (无规则); 存 `docs/fn_baseline.md`
- [x] 3.2 62 SSH 复跑已过 (scp 脚本+案例 → `source .env` 跑, catch 1/4 与 Mac 基线逐例一致, 零工具失败). 首跑暴露真 bug: 62 `.env` 导出 `JAVERT_NOTES_FILE/FEES_FILE=*_with_szx.csv`, `_point_config_at` 原只覆盖 DATA_DIR/ZD/SS/LABS/EXAM → 已补 pin 全 6 数据文件裸名. pytest 629 passed + 10 新测试绿, 唯 2 失败为 test_hub_source_ba (工作树 uncommitted hub_source.py 在建改动, 与本 change 无关, revert 即 9 passed)
- [x] 3.3 `Javert/CLAUDE.md` 关键文件表 + 变更日志补一行; `Javert问题汇总.md` 逐条标注案例编号
