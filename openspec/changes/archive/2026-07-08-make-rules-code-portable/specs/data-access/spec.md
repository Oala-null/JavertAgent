# data-access — delta spec

## ADDED Requirements

### Requirement: 费用分类优先官方类别标签

`search_fees._classify` MUST 优先读费用行的官方类别标签 `medins_chrgitm_type` (data-hub 路径已将 MXFYLB 2 位国标码回填为同款中文, 故跨院可移植): 标签含「手术」→手术类; 含「西药/中药/中成药」→药品类; 含「材料/耗材」→耗材类; 含「CT/检查/化验/拍片/病理/影像/超声/检验」→检查类. 标签缺失或落在模糊类 (治疗/床位/护理/其他/麻醉…) 时 MUST 回退现有名称关键词启发式. 本院自定义数字码 `med_chrgitm_type` MUST NOT 参与分类 (实测为非国标脏码). 类别列缺失时输出 MUST 与本 change 之前 (纯名称启发式) 一致.

#### Scenario: 药品按标签不再落其他类

- **WHEN** 某药品费用行 `medins_chrgitm_type` = 「西药」
- **THEN** 归入「药品类」, 不再因名称未命中关键词而落「其他类」

#### Scenario: 造影归类不靠 dict 顺序

- **WHEN** 某「造影」项目 `medins_chrgitm_type` = 「检查」(或「拍片」)
- **THEN** 归入「检查类」(按标签), 不再取决于 `_CATEGORY_KEYWORDS` 中手术类/检查类的字典顺序

#### Scenario: 缺类别列名称兜底

- **WHEN** 费用数据无 `medins_chrgitm_type` 列 (老 CSV / 外部院未供)
- **THEN** 分类结果 MUST 与本 change 之前的名称关键词启发式逐字一致
