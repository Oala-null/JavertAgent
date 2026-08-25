## 1. Rule data preparation

- [x] 1.1 Load all current Rule YAML files, assert unique rule IDs, and map official R rules to the 0325 source workbook without including patient or environment data
- [x] 1.2 Generate deterministic concise processing summaries and preserve full current fields for technical traceability

## 2. Workbook construction

- [x] 2.1 Build `规则协作主表` with exactly six rule columns, 163 current rule rows, filters, frozen headers, concise instructions, and readable widths
- [x] 2.2 Add blank three-option handling-level validation plus red/orange/blue conditional formatting and editable-cell styling
- [x] 2.3 Build `技术明细` with source classification, complete current rule metadata, triggers, tools, prompt, notes, and precheck fields
- [x] 2.4 Export the single final workbook to the thread-specific outputs directory

## 3. Verification

- [x] 3.1 Verify inventory count, unique IDs, six main columns, source samples, blank initial levels, exact three-option dictionary, and absence of formula errors
- [x] 3.2 Render and visually inspect every Sheet, repair clipping or readability defects, and re-export if needed
- [x] 3.3 Run strict OpenSpec validation and update this task list with the actual verified result

## 4. Persisted handling level

- [x] 4.1 Add the three-value `handling_level` Rule field, writer order, design-guide documentation, and focused schema tests
- [x] 4.2 Apply the documented initial classification to all current Rule YAML files and verify every file has exactly one valid field
- [x] 4.3 Run the focused Rule loader/writer tests and live `javert list` inventory check

## 5. Classified workbook refresh

- [x] 5.1 Regenerate the collaboration workbook from YAML `handling_level` values and include the field in technical details
- [x] 5.2 Verify per-rule YAML/workbook equality, three-level counts, data validation, formula scan, and all-Sheet visual layout
- [x] 5.3 Run strict OpenSpec validation and record the final task status
