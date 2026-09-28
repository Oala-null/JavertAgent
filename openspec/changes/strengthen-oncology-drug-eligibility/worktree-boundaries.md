# Implementation Worktree Boundaries

共享契约提交完成后，实施线按以下边界隔离。所有共享运行时接线只在
`codex/oncology-integration` 完成。

| Worktree | Branch | Exclusive ownership |
|---|---|---|
| A | `codex/oncology-eligibility-pathology` | `src/javert/oncology/eligibility.py`, `pathology.py`, eligibility/pathology builders and assets, their focused tests |
| B | `codex/oncology-regimen` | `src/javert/oncology/regimen.py`, regimen builder/asset/review artifact, their focused tests |
| C | `codex/oncology-result-guidance` | `src/javert/audit/result.py`, store/schema/SQL Server/API/workbench eligibility serialization and presentation, documentation guidance, their focused tests |
| Integration | `codex/oncology-integration` | `runner.py`, `drug_audit_lookup.py`, RD04/R007, routing, end-to-end/shadow tests and reports |

以下文件只允许 Integration 线修改：

- `src/javert/audit/runner.py`
- `src/javert/tools/drug_audit_lookup.py`
- `configs/rules/R007.yaml`
- `configs/rules/RD04.yaml`
- `src/javert/routing/`
- `data/router/`

合并顺序固定为 A → B → C。每条分支先跑独立专项测试；Integration 合并后再改共享
运行时文件并跑端到端测试。
