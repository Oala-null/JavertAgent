# 243/249热补丁同步核验（本地2026-09-20，现场截图2026-09-21）

## 结论与证据边界

维护目标为`gnome-243`，基线提交`8645a2deb1c4c99921a65a3ab01754275ec2ac4e`。用户明确要求不改FP8分支；本次仅恢复该院内维护线的已交付热补丁和说明材料。

| 检查 | 本次结果 |
|---|---|
| LIS运行文件及前置依赖 | 10+4个文件与已交付manifest的SHA256全部一致 |
| 原正式发行包叠加热补丁 | 376个已知运行文件与本地gnome工作树全部一致 |
| 原交付ZIP | 保持原字节与原SHA，没有修改或用新ZIP冒充原件 |
| 本地直接相关测试 | 41 passed，0 failed，0 skipped |
| 本地相关模块组合 | 108 collected / 108 passed / 0 failed / 0 skipped / 0 error |
| 分支与用户资料 | FP8引用保持7572a006ee7b0eea22a9a0536a6995a7261a5857；既有未跟踪文件保留 |
| 直接SSH核验 | 2026-09-20历史t243超时、249 SSH未成功；后续由用户在院内运行检查包并回传截图 |
| 现场磁盘文件 | 2026-09-21截图：disk_match=true、hotfix_match=true、differences=[]；376个运行文件及14个补丁文件全部匹配 |
| 现场基线戳 | deploy_stamp_match=true，DEPLOY_STAMP=MATCH |
| 现场Web进程 | 已观察到同目录Web；started_after_code_mtime=false，尚未验证进程内存代码 |

本报告中的“一致”现已包含**本地文件与用户回传的现场磁盘核验**，限定在已知发行范围；不等于本次重新部署或验证了进程内存。新维护工具、测试、文档不要求与院内文件完全相同；现场配置、患者数据、模型文件、额外文件及进程内存不在此摘要证明范围。

此前安装成功及首例反馈之外，已取得以下独立现场文件核验结果。

## 2026-09-21现场回传

依据用户提供的IMG_3708.JPG，核验时间为`2026-09-21T02:48:46.323989+00:00`（北京时间10:48:46）；主机gnome26，应用目录`/home/admin2/Javert`。检查包的三个文件SHA校验均为OK，随后显示HOTFIX_FILES=PASS、KNOWN_RELEASE_FILES=PASS、DEPLOY_STAMP=MATCH。

Web进程启动时间为`2026-09-17T06:06:47.350000+00:00`。`started_after_code_mtime=false`仅表示其启动早于376个受控文件中的最新mtime；非Web脚本的更新、原字节重传等也可能触发，不能直接推断正在运行旧版。

`loaded_code_verified=false`是此只读工具固定表达的“没有验证进程内存代码”，不是检测到代码不一致；即使重启，工具也不会把此字段自动改成true。不能为了让它变绿而反复重启。若需进一步确认运行态，先核对哪些文件时间较新及受控启动记录，再在任务空闲时决定是否重启。

该截图确认的是上述时刻的磁盘内容，不证明临床数据齐全、所有候选完成审计或后续磁盘永远不变。

## 恢复了什么

- 结算费用`TB_HIS_ZY_FEE_DETAIL`，时间字段`STFSJ`，没有退回FS/FYFSSJ。
- 已交付侧栏隔离修复。
- LIS医疗记录路径、正式/待核查分区、快照来源与范围核验。
- 全量候选冻结名单、逐人状态、独立完成回执和同批恢复。
- 已交付EDA、Web后台助手及单SYXH只读SQL纳入维护目录。

数据覆盖或临床判断正确性不由文件摘要保证：候选报告可能属于其他住院、存在歧义或缺少费用/文书；这些仍由原校验处理，不因本次同步而放宽。

## 测试覆盖与复现

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:delivery/audit_eda .venv/bin/python -m pytest \
  tests/test_hospital_linkage.py tests/test_hospital_linkage_sql.py \
  tests/test_lis_medical_path.py tests/test_lis_cohort_batch.py tests/test_lis_surface.py \
  tests/test_lis_frontend_js.py tests/test_lis_hotfix_installer.py \
  tests/test_hub_raw_source.py tests/test_workbench_routes.py tests/test_patient_overview.py \
  tests/test_249_hotfix_sync.py delivery/audit_eda/test_eda.py -q
```

验证了医疗记录与小结异号、同卡多次住院、键冲突、数据为空/新增、候选不被小结提前截断、费用映射不变、待核化验不进入审计、SHA/范围校验、批次续跑与不确定状态、SQL同步失败处理、前端显示/缓存/转义、安装/回滚/中途失败恢复及只读核验的缺失/差异/软链接/路径越界。

本次没有运行全仓测试或院内真实患者LLM，不声称“全仓全绿”。原LIS交付时独立工作树曾有3项外部CSV缺失失败；本次本地相关组合108项全部通过，两个环境的验收结果分别保留，不篡改原交付记录。

## 发布身份如何维护

- `delivery/lis_hotfix/v1-manifest.json`是原v1的10文件+4依赖摘要。
- `deploy/249/known-release.json`从原8645a2d正式包manifest叠加上述摘要得到；排除文档及受保护配置。
- `delivery/lis_hotfix/baseline-code.json.gz`是安装前受控代码夹具，已逐一匹配before摘要；没有.env、数据库配置、名单或患者内容。
- 原包仍是`Javert-249-LIS-delivery-20260916.zip`，SHA256为`735da7552a4da6cce8a4c4f46d71a63d8c11d6ade1480b4e1f35b3cb528e7a1e`。
- 修改v1业务源码后构建器会拒绝继续使用v1；新逻辑需要新版本和新的验证，不改现有清单让失败“变绿”。

## 重复现场检查的方法

上传`Javert-249-code-check`文件夹到`/home/admin2/releases/`，运行：

```bash
cd /home/admin2/releases/Javert-249-code-check
sha256sum -c SHA256SUMS
python3 check_249_hotfix.py --root /home/admin2/Javert --manifest known-release.json
```

本次回传已达到文件核验两项PASS、differences为空、deploy_stamp_match=true。工具只读，不改服务或数据库。进程时间仅提供线索；应先确定较新文件是否影响Web，再决定是否在空闲维护窗口重启，不能仅运行“启动助手”冒充重启。

## 文档口径

当前操作以[运维入口](deployment_243_gnome.md)、[LIS教程](deployment_249_lis_medical.md)、[发布Cookbook](243_release_cookbook.md)和[EDA教程](../delivery/audit_eda/README_zh.md)为准。通用国标表单保留legacy FS定义，并增加本院DETAIL例外；历史性能、样本、开机故障记录保留其快照日期。文档影响清单另见[文档盘点](243_249_document_audit.md)。
