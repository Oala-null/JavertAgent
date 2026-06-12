# Javert 数据范围清单（希望授权读取的范围）

> 配套文件：[`01_Zadig与Javert_产品与数据对接说明.md`](./01_Zadig与Javert_产品与数据对接说明.md)
> 用途：告诉贵院信息科，**Javert 希望被授权只读读取的住院病历数据范围**，便于贵方开权限与审计。
> 来源：本清单依据一份完整住院病历的数据字典整理，覆盖 **16 个文档类型 / 94 张逻辑表 / 1,465 个字段**。
> 版本：v1.0 ｜ 2026-06-10

---

## 写在前面

1. **原则：多多益善。** Javert 是医保违规自查系统，病历给得越全，能覆盖的违规条目越多。
   本清单是「**理想全集**」——不是每张表都必须立刻给齐，但**给到的范围越大，自查越完整**。
2. **不取隐私字段。** 姓名、身份证、住址、电话等 PII 一律不需要；用**住院号 / 病案号**关联即可。
   数据字典里标注「【涂黑】」的字段我们都不需要。
3. **列名无需贵方改。** 给 HIS / EMR 原始库的表与列名即可，列名对齐由我方完成。
4. **优先级三档**（仅说明价值高低，不代表「不给低档的」）：
   - ★ **核心**：现有审计能力直接消费，**最优先**。
   - ◆ **重要**：接进来即可解锁一批违规规则（药品、耗材、化验/检查、麻醉、手术真实性）。
   - ○ **补充**：作为佐证/背景，提升判定准确度、降低误报。

---

## 一、总览（按优先级）

| 优先级 | 文档类型 | 表数 | 对 Javert 的主要用途 |
|--------|---------|------|---------------------|
| ★ 核心 | 住院病案首页 | 4 | 诊断/手术/费用的**金标准基准**（判过度检查、串换、手术真实性） |
| ★ 核心 | 入院记录 | 4 | 入院诊断与指征——判「有没有做某项检查/治疗的理由」 |
| ★ 核心 | 病程记录 | 12 | 诊疗过程、术前术后记录——判过度诊疗、手术对应 |
| ★ 核心 | 手术记录 | 6 | 手术真实性、术中耗材/器械——判虚记手术、串换耗材 |
| ★ 核心 | 出院小结 | 5 | 诊断/手术/检查检验摘要——交叉校验全程一致性 |
| ◆ 重要 | 医嘱单 | 3 | 用药医嘱——判药品超适应症/超说明书/限二线 |
| ◆ 重要 | 检验报告 | 3 | 化验项目与结果——判无指征化验、重复化验 |
| ◆ 重要 | 检查报告 | 4 | 影像/心电等检查——判过度检查、无指征检查 |
| ◆ 重要 | 麻醉记录 | 13 | 麻醉与手术对应、麻醉/镇痛收费——判麻醉相关违规 |
| ◆ 重要 | 知情同意书 | 7 | 拟行操作/植入物告知——佐证手术与耗材真实性 |
| ◆ 重要 | 植入性医疗器械使用登记表 | 3 | 植入物条码/规格/数量——判耗材多记、串换、虚记 |
| ○ 补充 | 手术风险评估与术前准备自查表 | 2 | 术前准备真实性辅证 |
| ○ 补充 | 手术安全核对表 | 6 | 手术三方核查——佐证手术真实发生 |
| ○ 补充 | 手术交接核查表 | 2 | 手术交接——佐证手术真实发生 |
| ○ 补充 | 护理记录 | 19 | 护理收费、术中巡回、转运——背景与护理类收费佐证 |
| ○ 补充 | 住院证 | 1 | 入院信息背景 |
| | **合计** | **94** | |

> **另需一份关键数据：费用明细（医保结算明细）。** 这是「钱侧」的逐笔收费记录（每笔收费一行），
> 通常来自医保结算 / HIS 收费库，**不在上面这份病历数据字典里**，但它是 Javert 所有违规判定的核心依据，请一并授权。

---

## 二、逐表清单（按文档类型）

> 每张表标注：中文名 ｜ 英文表 ID ｜ 字段数。表 ID 与数据字典一致。

### ★ 住院病案首页（4 张表 / 135 字段）— 诊断/手术/费用金标准

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 病案首页主表 | `mr_face_sheet_main` | 86 |
| 诊断子表 | `mr_face_sheet_diagnosis` | 6 |
| 手术及操作子表 | `mr_face_sheet_operation` | 11 |
| 住院费用汇总子表 | `mr_face_sheet_fee` | 32 |

### ★ 入院记录（4 张表 / 86 字段）— 入院诊断与指征

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 入院记录主表 | `admission_record_main` | 70 |
| 初步诊断子表 | `admission_preliminary_diagnosis` | 5 |
| 48小时主治医师诊断子表 | `admission_attending_48h_diagnosis` | 5 |
| 辅助检查子表 | `admission_auxiliary_exam` | 6 |

### ★ 病程记录（12 张表 / 140 字段）— 诊疗过程

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 病程记录主表 | `progress_note_main` | 20 |
| 病程记录-诊断子表 | `progress_note_diagnosis` | 7 |
| 病程记录-生命体征子表 | `progress_note_vital_sign` | 9 |
| 病程记录-肩关节专科检查子表 | `progress_note_shoulder_exam` | 14 |
| 病程记录-鉴别诊断子表 | `progress_note_differential_diagnosis` | 5 |
| 病程记录-诊疗计划子表 | `progress_note_treatment_plan` | 4 |
| 病程记录-查房意见子表 | `progress_note_round_opinion` | 8 |
| 病程记录-术前小结要素子表 | `progress_note_preop_summary` | 21 |
| 病程记录-术前讨论与参加人员子表 | `progress_note_preop_discussion` | 16 |
| 病程记录-术后首次记录子表 | `progress_note_postop_first` | 14 |
| 病程记录-出院记录子表 | `progress_note_discharge` | 13 |
| 病程记录-引用辅助检查子表 | `progress_note_referenced_exam` | 9 |

### ★ 手术记录（6 张表 / 69 字段）— 手术真实性 + 术中耗材

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 手术记录主表 | `operation_record_main` | 31 |
| 手术诊断子表 | `operation_diagnosis` | 4 |
| 手术人员子表 | `operation_staff` | 3 |
| 手术物品清点单表头 | `surgical_count_header` | 13 |
| 手术物品清点明细子表 | `surgical_count_item` | 6 |
| 植入物/器械条码标签子表 | `implant_instrument_label` | 12 |

### ★ 出院小结（5 张表 / 59 字段）— 全程一致性校验

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 出院小结主表 | `discharge_summary_main` | 36 |
| 出院小结-出院医嘱明细 | `discharge_summary_orders` | 2 |
| 出院小结-诊断明细 | `discharge_summary_diagnosis` | 5 |
| 出院小结-手术操作明细 | `discharge_summary_operation` | 7 |
| 出院小结-检查检验摘要 | `discharge_summary_lab_exam` | 9 |

### ◆ 医嘱单（3 张表 / 48 字段）— 药品适应症/限定审计

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 医嘱单表头 | `medical_order_header` | 8 |
| 长期医嘱明细 | `long_term_order_item` | 18 |
| 临时医嘱明细 | `temporary_order_item` | 22 |

### ◆ 检验报告（3 张表 / 48 字段）— 无指征/重复化验

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 检验报告主表 | `lab_report_master` | 28 |
| 检验结果明细子表 | `lab_result_item` | 13 |
| 血栓弹力图(TEG)曲线参数子表 | `teg_curve_parameter` | 7 |

### ◆ 检查报告（4 张表 / 51 字段）— 过度检查/无指征检查

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 检查报告主表 | `exam_report_main` | 32 |
| 检查结论诊断条目表 | `exam_conclusion_item` | 5 |
| 放射病灶测量表 | `radiology_lesion_measure` | 5 |
| 心电图测量参数表 | `ecg_measurement` | 9 |

### ◆ 麻醉记录（13 张表 / 313 字段）— 麻醉/镇痛收费与手术对应

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 麻醉记录单主表 | `anes_intraop_record` | 49 |
| 麻醉记录-术中监测时点子表 | `anes_intraop_monitoring_point` | 12 |
| 麻醉记录-术中用药/输液输血子表 | `anes_intraop_medication` | 5 |
| 麻醉记录-术中事件/操作子表 | `anes_intraop_event` | 3 |
| 复苏记录单主表 | `pacu_recovery_record` | 57 |
| 复苏记录-监测时点子表 | `pacu_monitoring_point` | 8 |
| 复苏记录-用药/补液子表 | `pacu_medication` | 4 |
| 复苏记录-备注事件子表 | `pacu_event` | 2 |
| 麻醉术前访视记录主表 | `preanes_visit_record` | 57 |
| 麻醉术后随访记录 | `postanes_followup_record` | 14 |
| 麻醉总结主表 | `anesthesia_summary` | 43 |
| 术后镇痛记录主表 | `postop_analgesia_record` | 44 |
| 术后镇痛-随访时点子表 | `postop_analgesia_followup` | 15 |

### ◆ 知情同意书（7 张表 / 76 字段）— 操作/植入物告知佐证

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 知情同意书主表 | `informed_consent_main` | 32 |
| 知情同意书-诊断明细子表 | `informed_consent_diagnosis` | 5 |
| 知情同意书-拟行操作/治疗方案子表 | `informed_consent_procedure` | 6 |
| 知情同意书-风险及并发症告知子表 | `informed_consent_risk_item` | 5 |
| 知情同意书-植入物明细子表 | `informed_consent_implant` | 7 |
| 知情同意书-签名/谈话子表 | `informed_consent_signature` | 8 |
| 授权委托书-委托/代理信息子表 | `informed_consent_proxy` | 13 |

### ◆ 植入性医疗器械使用登记表（3 张表 / 54 字段）— 耗材/植入物审计

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 植入器械登记单主表 | `implant_registry_header` | 17 |
| 植入器械使用明细子表 | `implant_device_usage_detail` | 18 |
| 植入器械条码标签子表 | `implant_device_barcode_label` | 19 |

### ○ 手术风险评估与术前准备自查表（2 张表 / 30 字段）

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 手术风险评估与术前准备主表 | `surgical_risk_assessment_main` | 27 |
| 术前准备/术前检查自查勾选明细 | `surgical_risk_preop_checklist` | 3 |

### ○ 手术安全核对表（6 张表 / 29 字段）

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 手术安全核对表主表 | `surgical_safety_checklist_main` | 12 |
| 三阶段核查项明细表 | `ssc_checklist_item` | 7 |
| 手术麻醉风险预警陈述表 | `ssc_risk_warning_statement` | 3 |
| 离室前管路核查表 | `ssc_catheter_line_check` | 2 |
| 离室前患者去向表 | `ssc_patient_destination` | 1 |
| 三阶段签名表 | `ssc_phase_signature` | 4 |

### ○ 手术交接核查表（2 张表 / 21 字段）

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 手术交接核查表主表 | `surgical_handover_checklist_main` | 10 |
| 手术交接核查环节明细表 | `surgical_handover_stage` | 11 |

### ○ 护理记录（19 张表 / 261 字段）

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 护理单据主表(患者-单据头) | `nursing_doc_header` | 16 |
| 手术护理记录单(术中巡回护理) | `or_nursing_record` | 43 |
| 术中用药记录(手术护理单子项) | `or_nursing_med` | 4 |
| CORN术中获得性压力性损伤风险评估(主表) | `corn_pi_assessment` | 11 |
| CORN压力性损伤评估明细(术前/术中条目) | `corn_pi_item` | 5 |
| CORN压力性损伤预防措施(条目) | `corn_pi_prevention` | 4 |
| 体温单(主表) | `temperature_chart` | 3 |
| 体温单逐日记录(出入量/血压/体征等) | `temperature_chart_daily` | 16 |
| 体温单生命体征时点(曲线点) | `temperature_chart_vital_point` | 7 |
| 病人转运交接记录(逐次转运) | `patient_transfer_record` | 20 |
| 护理记录单(逐时观察记录) | `nursing_progress_record` | 23 |
| 入院护理评估(护理记录单首页评估) | `admission_nursing_assessment` | 15 |
| 住院患者毛细血糖检测记录(逐次) | `capillary_glucose_record` | 6 |
| 疼痛评估记录单(主表) | `pain_assessment_header` | 2 |
| 疼痛部位记录(逐次) | `pain_site_record` | 4 |
| 术前护理评估单(逐行评估) | `preop_nursing_assessment` | 17 |
| 围手术期ERAS记录单(主表) | `eras_perioperative_record` | 52 |
| ERAS术后镇痛医嘱(按术后日) | `eras_postop_analgesia` | 5 |
| ERAS术后护理评估(按术后日) | `eras_postop_nursing` | 8 |

### ○ 住院证（1 张表 / 45 字段）

| 逻辑表 | 表 ID | 字段数 |
|--------|-------|-------|
| 住院证主表 | `admission_certificate` | 45 |

---

## 三、最小可跑起步集（如不便一次给全）

如果一次性授权全集有难度，按以下顺序分批给，每多一批解锁更多审计能力：

| 批次 | 内容 | 解锁能力 |
|------|------|---------|
| **第 1 批（必给）** | 费用明细（医保结算）+ 住院病案首页（诊断/手术/费用汇总） | Javert 跑通基础违规审计 |
| **第 2 批** | 入院记录 + 病程记录 + 手术记录 + 出院小结 | 过度诊疗、手术真实性、全程一致性 |
| **第 3 批** | 医嘱单 + 检验报告 + 检查报告 | 药品适应症、无指征化验/检查 |
| **第 4 批** | 麻醉 + 知情同意 + 植入器械 + 护理等其余 | 麻醉/耗材/护理类违规，全集覆盖 |

> 起步只要 **第 1 批** 就能演示效果；后续每批接入都会让自查更全面。
