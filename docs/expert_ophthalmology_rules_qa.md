# 专家规则定向病例验证与发布记录

2026-09-07 UTC。用户授权从指定桌面PDF选择对应病例，且明确允许OCR文本发送到62内网Qwen用于脱敏和本次验证。仅处理一份30页扫描件；报告只使用语义化别名 `EYE-PDF-01`，不保存患者姓名、原始标识、病例原文、源文件指纹或运行标识。

## 已完成的病例验证

- 本机 Apple Vision OCR 全部30页，复用 text-sanitizer 的规则/LLM实体识别及本机HMAC替换；识别128处隐私实体，再去除身份页眉并做已知姓名/原住院号残留检查。发布输入不含这些原始标识，原始临时产物完成后清理。
- 原页核实诊疗计划、长期医嘱及总账三项中医治疗收费；收费名称的OCR错字用原页和项目编码交叉校正。没有修改或补写治疗执行事实。
- 输入为31段脱敏文书（30页，其中诊疗计划按原文标题分段）及三项已核实收费切片。**费用不是全病案全集，只支持本次R326定向核查**；总账未列逐次服务日期，日期字段留空，不以打印日期代替服务日期。
- R326：Router选中、presence预检返回facts，真实Qwen审计最终为 **INCONCLUSIVE / 0.50**，9次工具调用。现有诊疗计划和概括性出院治疗经过未被视作充分的逐次执行依据，需补治疗单/护理执行记录。该结论提示复核，不认定已发生虚构医疗服务。
- 修复编码读取后再次运行同一输入，结果保持I/0.50，耗时9.062秒。三项费用全部生成可定位命中，项目编码前导零完整；同时有诊疗计划与出院记录文书锚点。
- 仅R326完成真实病例验证。其他八条本次通过配置/合成回归，仍需对应实际病例及地方政策核查。九条保持ready，不升validated，不宣称已全面验证临床准确率。

## 测试中发现并修复

1. R323/RD38新写的行为类别未注册，`Runner`加载Promise规则库时抛两项 `BEHAVIOR_MAPPING_MISSING`，会阻断所有患者审计。分别采用已注册的“分解收费”“过度诊疗”类别，保留具体临床问题；新增实际规则库加载回归。
2. CSV将全数字收费编码推断为整数，导致命中编码前导零丢失。`CsvLoader`对国家/院内编码显式按字符串读取，主CSV和overlay复用同一dtype；数量、金额仍按原数值读取。新增失败再修复的base+overlay回归。

## 验证命令与结果

在隔离发布工作树执行，保留原工作树的慢病等未提交修改：

```bash
PYTHONPATH=src python -m pytest \
  tests/test_csv_loader.py tests/test_ophthalmology_expert_rules.py \
  tests/test_precheck.py tests/test_routing.py tests/test_verdict_gate.py \
  tests/test_deployment_sync.py tests/test_routes_2c.py \
  tests/test_workbench_routes.py tests/test_sqlserver_ocr_visibility.py \
  tests/test_hit_resolver.py tests/test_headline_sqlserver_store.py -q
```

结果：**181 collected / 180 passed / 1 skipped / 0 failed / 0 errors**。skip为既有 `test_j66252_baseline_row_count_unchanged`，隔离工作树没有该真实数据快照；本次没有跑全量套件。OpenSpec strict与git diff格式检查通过。

## 发布范围与交接

本次生产变更是九条规则、两份生成索引和CSV编码读取修复；必须成组合并。发布基线来自已提交HEAD，其上已有处理等级编码API提交，62此前版本尚不含该提交，相关2C兼容测试已纳入组合门禁。原工作树的慢病及其他未提交代码不进入制品。

按用户要求先commit、push，再从已提交HEAD通过 `deployment_sync.py artifact/install` 发布62；固定production-62分支，先保留进程实际环境、代码及.env备份，安装后schema、重启及状态/SQL/Hub/v3验收。仅将这一次验证结果以“眼科”tag写入工作台，原始页/完整OCR不进入Git，工作台使用脱敏文书与已核实三项收费切片。

发布完成事实追加到change任务及部署记录；本文的病例验证结果不替代生产验收。

## 已完成的生产验收

功能提交 `b620eacd562f1c30913a1a8b6cc9bc652e5ec2ed` 已推送隔离发布分支，并按规定顺序安装62。官方部署check返回synced，两端HEAD相等，受控tracked clean；进程29个JAVERT环境键及所核对生效配置保持一致。

schema命令成功、systemd active、登录200、v3空数组submit202、不存在的合成号results200/unknown，SQL/Hub健康。最终一次R326结果已双写并标记“眼科”；线上v3实读1张I卡、3项完整收费编码，工作台待复核查询可读，原文源实读31段脱敏文书、3项核实收费。原overlay的251段文书和4315行费用逐行保留。没有把首轮中间结果、原PDF或未脱敏OCR发布到工作台。

生产代码/.env/旧进程环境和SQLite备份按runbook保留；敏感临时产物清理，不纳入Git。后续临床核验边界仍按上文，仅R326有本次真实病例证据。
