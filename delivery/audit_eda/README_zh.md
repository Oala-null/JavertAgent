# 249 下班前审计统计

这个文件夹独立放在 `releases` 中。无需安装补丁、停止跑批或重启工作台；不会更改正在跑的批次代码指纹。请在249的Linux SSH终端执行，Windows跳板机只负责传文件。

## 1. 先继续跑全部候选

首例试跑结束后，接着原批次运行，不再写人数上限：

```bash
cd /home/admin2/Javert
bash scripts/lis_batch.sh resume latest
bash scripts/lis_batch.sh status
```

这是后台任务，关闭SSH仍会运行。它保留原TAG、原名单和已完成结果，继续剩余候选；数据关联缺失/有歧义或本次住院没有可用化验的会明确跳过，不当作成功审计。新同步到库里的患者不会自动追加到已经冻结的名单。

若提示 `RESUME_BATCH_NOT_UNIQUE`，先运行status找到这次试跑的进度目录，再把 `latest` 换为显示的完整目录。若提示已有批跑，就等当前任务，勿重复start。已结束全量名单不需要resume；要处理后续DE新增数据，应另开新批次，可能重新审计旧患者。

## 2. 上传统计文件夹

在Mac或Windows解压后，将整个 `Javert-249-EDA-v1` 文件夹上传到：

```text
/home/admin2/releases/Javert-249-EDA-v1/
    eda.sh
    eda_249.py
    README_zh.md
    START_HERE.txt
    test_eda.py
    VALIDATION.md
    SHA256SUMS
```

文件名全英文，脚本使用UTF-8/Linux换行。不要用Windows记事本另存脚本，也不要覆盖 `/home/admin2/Javert/scripts/` 下的原有文件。

如果上传ZIP，则把ZIP和同名sha256文件放到 `/home/admin2/releases/eda-upload/`，执行：

```bash
cd /home/admin2/releases/eda-upload
sha256sum -c Javert-249-EDA-v1.zip.sha256
test ! -e /home/admin2/releases/Javert-249-EDA-v1 && \
python3 -m zipfile -e Javert-249-EDA-v1.zip /home/admin2/releases
```

若目标目录已经存在，先核对已有目录，勿覆盖另一个版本。已经解压上传的，直接进行下一步。

## 3. 下班前手动运行一次

```bash
cd /home/admin2/releases/Javert-249-EDA-v1
sha256sum -c SHA256SUMS
bash eda.sh
```

校验全部显示OK后执行。默认读取创建时间最新的LIS批次（包括已经完成的批次），同时统计工作台全量最新结果，含所有历史批次和无TAG存量。

这是前台统计，执行过程中保持SSH窗口开启。费用按患者顺序只读核对，每25人显示一次聚合进度，可能需要几分钟。无需等批次全部结束；仍在运行时，报告会注明阶段、时间、水位和剩余人数。

看到下面两行代表报表已成功写出，不代表所有候选均审计成功或所有金额都已核定：

```text
EDA_REPORT=PASS
统计目录：/home/admin2/Javert/output/eda-运行日期时间.随机字符
```

如果只想快速看数量和耗时，暂时不查费用：

```bash
bash /home/admin2/releases/Javert-249-EDA-v1/eda.sh --no-fees
```

这里的费用会标记“未请求/不可计”，不是0元。

要固定统计某一批，先从 `lis_batch.sh status` 复制完整进度目录，然后运行：

```bash
bash /home/admin2/releases/Javert-249-EDA-v1/eda.sh --batch /home/admin2/Javert/output/lis-batch.实际目录后缀
```

只统计工作台全部存量，不选特定LIS批次：

```bash
bash /home/admin2/releases/Javert-249-EDA-v1/eda.sh --batch none
```

## 4. 结果文件看哪个

在MobaXterm左侧SFTP进入命令最后显示的统计目录。

| 文件 | 内容 |
|---|---|
| report.txt | 可直接阅读和复制的中文完整汇总 |
| overview.csv | 本批/工作台全量：人数、命中、金额覆盖、耗时 |
| categories.csv | 每类违规：命中次数、涉及患者、待核查数 |
| rules.csv | 按规则编号和类别细分 |
| historical_batches.csv | 各历史TAG原始执行量和规则计算耗时，含重跑 |
| report.json | 统计口径、时间水位和全部聚合指标 |
| REPORT_COMPLETE | 本次报告完整生成标记 |

CSV为UTF-8 BOM，可直接用Microsoft 365 Excel打开。所有报告不输出患者号、姓名、病历、证据原文或数据库密码。每次运行生成新目录，旧报告保留。

## 5. 数字的口径

- **本批**：选中TAG内，每个患者每条规则只取最新一次结果。
- **工作台全量**：所有TAG和无TAG存量合在一起，每个患者每条规则只取最新一次，包含本批。因此两张总数不能相加。
- **有结果患者**：至少已有一条审计结果落库的首页数。处理中的患者可能已落一部分规则；是否整例完成另看批次PASSED数。
- **命中次数**：VIOLATION的患者×规则数。一位患者命中三条规则，算三次、一人。不是违规收费明细条数。
- **类别患者数**：每类各自去重。同一患者可能涉及多类，各类别人数不能相加为总人数。
- **人数**：按首页SYXH统计住院案例，不是跨住院自然人去重。
- **专家复核**：单独列出机器违规中的专家一致V、存在分歧、尚未复核。复核表不可读时标为未知。

### 金额

现有审计结果没有可复核的结构化违规金额。因此“违规金额”显示**待核定**，不拿模型文字里的数字或整次住院费冒充。

另提供**命中患者当前DETAIL结算净费用参考**：仅统计VIOLATION涉及的患者，每个首页只计一次整次住院费用，复用现有DETAIL/STFSJ映射和退费处理。它不是违规金额或医保损失，也不是原审计日期的费用快照。必须连同取费成功人数一起看；未取到的没有被当成0。

### 耗时

- 规则耗时合计：当前保留的最新规则结果的duration_ms之和。
- 历史计算耗时：含重跑的所有已落库规则duration_ms之和；不能与前一项相加。
- 批次自然跨度：从名单建立到当前/最后处理时刻，含中途暂停和等待。
- 逐人处理累计：已结束患者的取数、校验、审计、同步用时之和，包含跳过病例的处理时间。
- 成功患者平均处理时间：只对有完整起止时间的PASSED病例计算。

规则累计时间不是墙钟时间；并行运行或历史运行跨天时尤其不能混用。脚本没有持续计时记录，不能精确重建历史批次的所有暂停间隔。

## 6. 常见情况

1. `EDA_REPORT=FAILED`：本轮没有成功统计；不要当作0违规。先保留错误代码。可用 `--no-fees` 区分费用读取与结果读取问题。
2. 费用覆盖小于命中患者数：查看report.txt的错误码统计。原首页、小结、就诊号或费用校验问题会导致该患者不可计。
3. 最新时间并列数量大于0：脚本以id最大者选定。原工作台只按时间排序，对并列结果的选择可能不同，需单独核对。
4. 首例完成而本批结果为0：核对脚本显示的TAG；`ROUTER_SKIPPED`不会产生普通规则结果，不等于成功审计一例。
5. 任务尚未完成：统计可照常生成，剩余病例后续完成后再运行一次。两次报告是两个时点，不能相加。
6. 中断统计：Ctrl+C仅停止这次统计，不停止tmux里的全量审计。

这里不自动定时。下班前手动执行第3节即可。
