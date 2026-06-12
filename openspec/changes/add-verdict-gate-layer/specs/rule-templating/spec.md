## MODIFIED Requirements

### Requirement: M2 过度检查模板

M2「过度检查」模板 SHALL 把 `min_count` 默认值改为 **2** (单次不再默认触发 V), 并在判定「无指征」前 **MUST** 先用 `scan_progress_indications` 扫该检查的症状词 (新增 `symptom_kw_list` 字段, 逐规则填该检查对应症状), 命中具体症状即判 CLEAN (有指征)。仅当诊断无指征 **且** 病程症状检索 0 命中 **且** 净次数 ≥ `min_count` 时才判 VIOLATION。

#### Scenario: 单次默认不再 V

- **WHEN** 某 M2 规则未显式设 `min_count`, 该检查净次数 = 1、诊断无指征
- **THEN** 默认 `min_count=2`, 不满足 ≥2 → 不判 V (走 CLEAN/INCONCLUSIVE)

#### Scenario: 判无指征前强制扫病程

- **WHEN** `note_diagnosis` 无指征诊断
- **THEN** 模板流程 MUST 先调 `scan_progress_indications(pid, symptom_kw_list)`, 命中症状 → CLEAN; 仅在病程也 0 命中时才继续向 V 推进

#### Scenario: symptom_kw_list 驱动症状检索

- **WHEN** 渲染某心评类 M2 规则 (如 R141/R146)
- **THEN** 其 `symptom_kw_list` 含 `胸闷/气短/喘/憋/下肢水肿/胸痛/心悸` 等, 注入 master_prompt 的症状检索步

#### Scenario: 与单次硬闸一致

- **WHEN** 模板未拦住的单次 V 流到 gate
- **THEN** `verdict-gate` 的单次硬闸兜底降级 (模板软层 + gate 硬层双保险, 语义一致)
