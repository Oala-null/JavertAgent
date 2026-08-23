# Diagnosis Evidence Shadow v0.1

## 定位

该路径是显式、默认关闭的 Evidence Contract 垂直切片，不参与 Runner、Router、precheck、
verdict gate、`persist_one`、`audit_runs`、SQL Server、SSE 或 2C。它只从 canonical `shi_zd`
行和诊断文书 span 产生 `CandidateAssertion`，经 schema/source/ontology 校验后写入独立 SQLite
append-only ledger，再生成可重放的只读 projection。

结构化 locator 以 immutable SourceArtifact version/checksum、zero-based row ordinal、row
fingerprint、field/value checksum 为权威；文书另带 `[start_char,end_char)` 与 span checksum。
`ba_id`、`ipt_medcas_hmpg_sn`、`WSLSH` 只有在上游明确保留且验证过时才是额外 hop，不能猜测。

| Canonical 输入 | Evidence Contract 投影 |
|---|---|
| `shi_zd.diag_code`（回退 `inhosp_diag_code`） | versioned `ConceptRef.system/code/version` |
| `shi_zd.diag_name`（回退 `inhosp_diag_name`） | display/terminology mapping，不参与 code identity |
| SourceArtifact + row ordinal/fingerprint + 实际消费字段 | `StructuredLocator` |
| `case_notes.子阶段` + `内容` | diagnosis section + `DocumentLocator[start,end)` |
| 明确诊断 / 明确否认 / 疑似待排 | `Assertion.value=TRUE / FALSE / UNKNOWN` |
| extractor/version + source checksum | `ProvenanceActivity` + exact `OntologyRef` |

该表是唯一 mapping 口径；shadow 不扩展 `DataLoader`、manifest 输出列或 Hub canonical schema。

## 合成门禁

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/diagnosis_evidence_shadow.py \
  --input tests/fixtures/diagnosis_shadow/golden.json \
  --ledger /tmp/javert-diagnosis-shadow/ledger.sqlite \
  --summary /tmp/javert-diagnosis-shadow/summary.json \
  --evaluation-output /tmp/javert-diagnosis-shadow/evaluation \
  --repeat 2
```

第二次重复提交必须只增加 duplicate 计数，accepted stream 和 projection checksum 不变。
合成 A/B 使用公共 CONFORMANCE profile：A 是现有 `note_diagnosis`/canonical diagnosis 可见
输出，B 是逐 row/span Evidence shadow。它只证明 grounding、locator、provenance、UNKNOWN/
Conflict 和重复稳定性，不计算 oncology false V/C、专家 outcome agreement 或 PROMOTION。

## 真实数据默认关闭

真实患者 source 必须同时满足以下条件，否则 CLI 在读取 source 前 fail closed：

- `JAVERT_DIAGNOSIS_EVIDENCE_SHADOW=on`；
- `JAVERT_DIAGNOSIS_SHADOW_OWNER` 非空；
- `JAVERT_DIAGNOSIS_SHADOW_RETENTION_DAYS` 为正整数；
- patient/case 只使用 `anon-<salted hex>` 安全引用；
- ledger 与 evaluation package 位于 Git 工作区外的批准路径。

真实 ledger 目录强制 0700、文件 0600。运行日志和公开 summary 只包含 source kind、版本、
accepted/duplicate/rejected/failed/unmapped 计数、稳定错误码和 digest，不得输出患者号、诊断原文、
source locator、SQL、凭据、salt、完整 candidate/envelope 或未盐化 run/ownership 标识。

## Retention 与销毁

启用前必须由数据 owner 书面确认 store location、owner、保留期、备份和销毁审批。v0.1 不实现
自动 retention/delete/compaction；单条 Fact/Assertion 不允许 update/delete。到期销毁只能由独立、
可审计的生命周期操作处理获批的 sealed ledger/segment 整体，不能伪装成运行时“清理历史”。

## 解释口径

- `UNKNOWN` 表示明确 source/time coverage 内仍无法建立真或假，不创建 Unknown Diagnosis，
  也不把技术失败当临床 UNKNOWN。
- 只有同一规范化命题、时间重叠的 TRUE/FALSE 才形成 Conflict；不同诊断可并存。
- alias 来自 Diagnosis 自有、版本化 deterministic terminology mapping，不扩张 Ontology v0.1，
  未映射 code/text 不冒充 ICD。
- 本 change 的 PASS 不授权真实数据运行、62 调度、生产裁决或任何 API 字段变化。
