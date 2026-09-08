# 慢病专家反馈与单PDF试跑

当前入口是 CD 规则的显式 shadow 评估。仓库默认
`JAVERT_CHRONIC_DISEASE_CRITERIA=off`；普通审计默认集不包含 CD。
专家解释、资产完整审批、运行时发布、病例试跑分别记录，互不替代。

工作台的“慢病命中（待复核）”表示有来源定位和逐字引文校验的候选证据。
`qualified=null / REVIEW_REQUIRED` 表示尚不能自动认定；不计为医保违规。
未命中不等于否定诊断。没有可靠日期、数值单位或完整条件的节点保留 UNKNOWN。

## 本次授权路径

指定 PDF → 62 内部 PP-OCR → 内部 Qwen 页分类 → 临床页 →
19 条 CD shadow + CD10 关闭状态 → 私有结果清单 → 双库对账 → Workbench“慢病”。

用户明确免除本次 2C 脱敏流程。原文只能存在于受控服务、运行时私有目录和获授权的
工作台数据层；禁止在 Git、终端、QA 报告中放入姓名、患者号、原文、凭据或 run 标识。
不把行政/身份材料和费用汇总页导入临床 notes，不用不完整费用数据宣称全规则审核。
页面分类和文字均未经人工核对，工作台保留 OCR 提示，原文定位使用 `page-N`。

## 执行和恢复

先完成代码测试、`scripts/build_rule_mapping.py`、知识资产检查和 OpenSpec strict，
然后按 [62部署手册](deployment_192_62.md) 从已提交 HEAD 发布，先迁移再重启。
禁止把临时运行目录加入部署制品。生产慢病开关可继续 off，显式脚本独立使用 shadow。

内部 OCR 适配器生成私有 JSON：`case_id=chronic-<随机32位hex>`，
`pages=[{page:1,kind:clinical|fee|financial|administrative|unreadable,text:...}]`。
全部页必须连续且唯一；随机病例键只用于隔离，不意味着病历正文已经脱敏。
含患者数据的目录0700、文件0600，调用过程禁止回显文档内容和异常响应正文。

```bash
# 路径由本次受控运行环境提供，不在命令中传患者身份或凭据。
PYTHONPATH=src .venv/bin/python scripts/run_chronic_pdf_pilot.py \
  --bundle "$CHRONIC_BUNDLE" --private-dir "$CHRONIC_PRIVATE_DIR"

# 同一私有清单发布/恢复；先备份SQLite和overlay，确认目标为62工作台。
PYTHONPATH=src .venv/bin/python scripts/run_chronic_pdf_pilot.py \
  --bundle "$CHRONIC_BUNDLE" --private-dir "$CHRONIC_PRIVATE_DIR" \
  --publish --overlay "$CHRONIC_OVERLAY" --backup "$CHRONIC_BACKUP"
```

脚本只续跑该清单未完成的规则，并只发布清单中的run；资产、原文、病例或已有结果冲突
立即拒绝。旧overlay字节保留，病例已存在且内容一致时不重复追加。双库结构化结果和tag
对账通过才报告published。SQL不可达时保留私有恢复清单，不得调用全历史pending同步。
清单在验收完成或决定终止本次运行后清理；生产备份按受控运维策略保留。

## 验收与回滚

- 合成测试覆盖关闭模式、候选锚点、partial/未审批门禁、旧行兼容、双写与工作台。
- 62检查systemd、登录页、进程实际环境、SQL/Hub、v3空submit=202和unknown=200。
- 使用本次私有case核对两端run集合、结构化JSON、tag和pending状态；线上工作台可发现、
  `filter=all`可显示全部结果，原文页数与临床页清单一致，慢病不增加普通违规计数。
- 清理临时PDF、页面图片、OCR、执行CSV和私有结果清单；报告只保留页数、规则数和状态统计。
- 回滚代码使用上一受控制品；nullable列保留，历史结果不重写。撤回本次原文仅在核验没有
  后续写入时恢复对应备份，不覆盖其他病例新增数据；结果撤回走专家review。

5+45严格QUALIFIED样本、完整跨就诊归一和整体知识签发仍按OpenSpec后续任务进行。

## 原始身份显示（不脱敏导入）

内部病例关联键与界面身份分开：用户要求不脱敏时，OCR服务/内部模型从原文提取姓名与就诊号，逐字核验来源后作为显示列保存，不替换病历正文。可选bundle.source_identity以patient_name、visit_id各自的value/source_page/quote记录来源；脚本保留source_patient_name、source_visit_id，不转换为星号或替代姓名。侧栏、详情、概览和原文弹窗显示原始身份；查询及证据仍沿原有内部关联，不重写已发布审计结果。

值冲突时显示身份待核，缺少原号显示“原文未提取”，不拿随机关联键冒充住院号。显示字段仅存在于受控患者数据，不进入规则资产或Git。增加显示列时CSV会重写表头，但所有原有字段逐值保留；不适用此前“仅追加字节”的描述。旧普通病例没有有效原始显示列时仍沿用原先显示。
