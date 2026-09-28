# tasks — add-workbench-sql-raw-source

## 1. 共享映射模块提取 (先固化 oracle)

- [x] 1.1 提取前跑 `etl_from_data_hub.py --patients <5个混合患者(sy+szx)>` 存 oracle 输出目录 (逐字节回归基线)
- [x] 1.2 新建 `src/javert/data/hub_source.py`: 迁入 CS 构造(改由 config)、MXFYLB2CN/YCTS2FLAG/SENT、q/in_clause/clean_dt、6 个 `fetch_xxx(cn, pids) -> DataFrame` 纯函数 (fee/notes/zd/ss/labs/exams, 列契约不变)
- [x] 1.3 `scripts/etl_from_data_hub.py` 改薄壳: import hub_source, 只留 argparse + to_csv + 汇总打印
- [x] 1.4 重跑 1.1 同参数, 6 文件与 oracle 逐字节 diff 为空 (含 BOM/列序/行序)

## 2. 配置

- [x] 2.1 `config.py` 加 `hub_raw_enabled: bool = False` (env `JAVERT_HUB_RAW_ENABLED`) + `hub_database: str = "TP_data_hub"` (env `JAVERT_HUB_DATABASE`); 连接串复用 sql_* 凭据 + `Encrypt=no`
- [x] 2.2 config 单测: 默认关 / env 开 / hub_database 覆盖

## 3. HubRawSource 适配器

- [x] 3.1 新建 `src/javert/web/hub_raw_source.py`: lazy 连接 (失败可重建) + `get_notes(pid)/get_fees(pid)` 返回与 CsvLoader 同列 DataFrame (患者号大写归一, JZLSH 精确匹配)
- [x] 3.2 labs getter: fetch_labs → routes 现用记录结构 (report_dt 升序, result_flag 同规则); exams getter 同构 (可裁项, sy 有 RIS 数据)
- [x] 3.3 主诊断 getter: fetch_zd → maindiag_flag=1 行诊断名
- [x] 3.4 逐患者 LRU (maxsize=32, 无 TTL), 全部 getter 走同一缓存条目 (一个患者一次抓齐 6 类)
- [x] 3.5 异常降级: 任何 SQL 异常 → log warning + 返回空 (单测: mock 连接抛错不冒泡)

## 4. 工作台接线

- [x] 4.1 `routes_workbench.get_raw_patient`: CSV notes+fees 双空且开关开 → HubRawSource; 双 miss 才 404
- [x] 4.2 labs/exams tab 与 `_get_main_diagnosis` 同链回退 (文件 miss → hub)
- [x] 4.3 `reset_loader()` 一并清 hub 缓存/单例
- [x] 4.4 单测: 开关关零行为变化 / CSV 命中不查库 (mock 断言) / hub 命中 200 / 双 miss 404 / hub 异常时 CSV 患者无感知

## 5. 回归与部署

- [x] 5.1 全量 `uv run pytest tests/ -v` 绿
- [x] 5.2 Mac 本地冒烟: 开关开 + 空 data_import, `/api/patient/211318013/raw` 返回 200 且内容与流B ETL 产出一致; J66252 与改前一致
- [x] 5.3 142 核对/补 JZLSH 索引 (幂等 CREATE INDEX, 只读库加索引): TB_HIS_ZY_FEE_DETAIL_FS/TB_CIS_MEDICAL_DOCUMENT/TB_LIS_REPORT/TB_IH_DIAGNOSIS_DETAIL/TB_OPERATION_DETAIL
- [x] 5.4 62 部署 (开关未配, 验证零变化) → `.env` 加开关 → 重启 → 点 hub-only 患者原文 200 + J66252 一致
- [x] 5.5 CLAUDE.md 变更日志 + `docs/deployment_192_62.md` 升级步骤 (开关/回滚一行)
