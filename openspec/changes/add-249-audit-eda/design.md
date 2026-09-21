## Context

已安装LIS补丁的249正在执行冻结名单。用户要求下班前手动统计本批与存量。工作台按patient_id+rule_id最新created_at展示；当前结果没有结构化违规金额，rule_yaml_snapshot保存违规类别，duration_ms保存单规则耗时。

## Goals / Non-Goals

Goals：一个独立shell入口、无需停服，可复核的人数/命中/类别/耗时/金额覆盖；中文TXT和Microsoft 365可打开的BOM CSV，无患者明细输出。

Non-Goals：不自动定时；不重跑存量审计；不将整次住院费或LLM文本数字当违规金额；不修改运行中批次/指纹文件，不改库结构。

## Decisions

1. delivery/audit_eda内shell+Python独立运行，复用已安装run_243_lis_batch.runtime_profile加载受控配置，使用hub_source.connect/resolve_patient/fetch_fees；不复制TB字段映射或配置密码。
2. 读取SQL Server结果，先固定MAX(id)，后续只统计不大于此水位；同一次结果集取工作台最新和所选批次内最新。相同created_at以id倒序稳定择一并报告并列情况，未声称解决工作台本身的并列歧义。正在执行的未落库部分不计为完成；批次进度单独读summary并标记读取时间。
3. 全量包含存量和本批，二者不能相加。各类别人数按自身去重，不相加为总人数。历史运行开销另取水位内各批次原始duration_ms累计，不与最新结果耗时混同。
4. rule_yaml_snapshot优先给类别，缺失时当前规则YAML回退且记录数量。专家review读取最新标记，只报告一致V/分歧/无复核，不将机器V等同专家确认。
5. 未记录结构化违规金额时输出待核定/null，非0。另按唯一命中首页复用DETAIL结算费用读路径求当前全住院净费用，逐人关联失败列覆盖/错误码；此数不是违规额且不是历史审计时点快照。无法计费的患者绝不默认为0。
6. 脚本只SELECT和会话超时设置，结果输出新建0700目录，0600文件。所有敏感行只在内存，异常不回显连接信息/患者号；不导出reasoning/evidence/tool_calls/名单。失败无完成标记。
7. 仅使用已安装依赖和标准库，提供离线demo与unittest验证重复跑、全量/批次范围、金额未知及退款、耗时单位、部分结果、类别口径、隐私和CSV公式转义。

## Risks / Trade-offs

- 源库正在同步 → 各读取时点写入报告，不称跨库事务快照；水位后新结果下次再统计。
- 每人费用读取较慢 → 顺序只查命中患者、显示聚合进度，可--no-fees快速统计；不并发压院内库。
- 旧存量缺少结构化金额和快照 → 正确显示未知，保留当前结算费参考和覆盖率。
- 按首页统计并非自然人 → 中文口径明确同一人多次住院算多个首页。

## Migration Plan

独立文件夹上传/home/admin2/releases/Javert-249-EDA-v1，校验SHA后bash eda.sh；参数可指定批次目录，--no-fees跳过费用读取。无需安装/重启；删除独立工具即可撤回，不删已有报告。
