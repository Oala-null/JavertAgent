:on error exit
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF N'$(KB_DATABASE)' <> N'知识库_work'
    THROW 51000, N'KB_DATABASE 必须精确为 知识库_work', 1;
IF DB_NAME() <> N'$(KB_DATABASE)'
    THROW 51001, N'连接数据库与 KB_DATABASE 不一致', 1;

BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'kb_meta')
    EXEC(N'CREATE SCHEMA [kb_meta] AUTHORIZATION [dbo]');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'kb_stg')
    EXEC(N'CREATE SCHEMA [kb_stg] AUTHORIZATION [dbo]');
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'kb')
    EXEC(N'CREATE SCHEMA [kb] AUTHORIZATION [dbo]');

IF OBJECT_ID(N'[kb_meta].[schema_version]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb_meta].[schema_version] (
        [version_no] int NOT NULL CONSTRAINT [PK_kb_schema_version] PRIMARY KEY,
        [applied_at] datetime2(3) NOT NULL CONSTRAINT [DF_kb_schema_version_applied] DEFAULT SYSUTCDATETIME(),
        [migration_checksum] char(71) NOT NULL,
        CONSTRAINT [CK_kb_schema_version_checksum] CHECK ([migration_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb_stg].[import_batch]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb_stg].[import_batch] (
        [import_batch_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_import_batch] PRIMARY KEY,
        [workbook_sha256] char(71) NOT NULL,
        [template_schema_version] varchar(32) NOT NULL,
        [safe_basename] nvarchar(260) NOT NULL,
        [uploaded_by] nvarchar(128) NOT NULL,
        [uploaded_at] datetime2(3) NOT NULL CONSTRAINT [DF_kb_import_batch_uploaded] DEFAULT SYSUTCDATETIME(),
        [status] varchar(32) NOT NULL,
        [expected_row_count] int NOT NULL,
        [expected_counts_json] nvarchar(max) NOT NULL,
        [reconciliation_json] nvarchar(max) NULL,
        [error_summary] nvarchar(1000) NULL,
        CONSTRAINT [UQ_kb_import_batch_workbook] UNIQUE ([workbook_sha256], [template_schema_version]),
        CONSTRAINT [CK_kb_import_batch_checksum] CHECK ([workbook_sha256] LIKE 'sha256:%'),
        CONSTRAINT [CK_kb_import_batch_status] CHECK ([status] IN ('UPLOADED','VALIDATION_FAILED','VALIDATED','MATERIALIZED','FAILED','ABORTED')),
        CONSTRAINT [CK_kb_import_batch_count] CHECK ([expected_row_count] >= 0),
        CONSTRAINT [CK_kb_import_batch_counts_json] CHECK (ISJSON([expected_counts_json]) = 1),
        CONSTRAINT [CK_kb_import_batch_reconciliation] CHECK ([reconciliation_json] IS NULL OR ISJSON([reconciliation_json]) = 1)
    );
END;

IF OBJECT_ID(N'[kb_stg].[import_row]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb_stg].[import_row] (
        [import_batch_id] nvarchar(80) NOT NULL,
        [sheet_name] nvarchar(64) NOT NULL,
        [excel_row_no] int NOT NULL,
        [stable_row_id] nvarchar(128) NOT NULL,
        [canonical_payload] nvarchar(max) NOT NULL,
        [row_checksum] char(71) NOT NULL,
        [validation_status] varchar(24) NOT NULL,
        [safe_error_code] varchar(64) NULL,
        CONSTRAINT [PK_kb_import_row] PRIMARY KEY ([import_batch_id], [sheet_name], [excel_row_no]),
        CONSTRAINT [FK_kb_import_row_batch] FOREIGN KEY ([import_batch_id]) REFERENCES [kb_stg].[import_batch]([import_batch_id]),
        CONSTRAINT [UQ_kb_import_row_stable] UNIQUE ([import_batch_id], [sheet_name], [stable_row_id]),
        CONSTRAINT [CK_kb_import_row_json] CHECK (ISJSON([canonical_payload]) = 1),
        CONSTRAINT [CK_kb_import_row_checksum] CHECK ([row_checksum] LIKE 'sha256:%'),
        CONSTRAINT [CK_kb_import_row_number] CHECK ([excel_row_no] >= 2),
        CONSTRAINT [CK_kb_import_row_status] CHECK ([validation_status] IN ('PENDING','VALID','INVALID'))
    );
END;

IF COL_LENGTH(N'kb_stg.import_batch', N'expected_counts_json') IS NULL
BEGIN
    ALTER TABLE [kb_stg].[import_batch] ADD [expected_counts_json] nvarchar(max) NULL;
    UPDATE [kb_stg].[import_batch] SET [expected_counts_json] = N'{}' WHERE [expected_counts_json] IS NULL;
    ALTER TABLE [kb_stg].[import_batch] ALTER COLUMN [expected_counts_json] nvarchar(max) NOT NULL;
END;

IF COL_LENGTH(N'kb_stg.import_batch', N'reconciliation_json') IS NULL
    ALTER TABLE [kb_stg].[import_batch] ADD [reconciliation_json] nvarchar(max) NULL;

IF OBJECT_ID(N'[kb].[source_document]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[source_document] (
        [source_document_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_source_document] PRIMARY KEY,
        [source_type] varchar(32) NOT NULL,
        [title] nvarchar(500) NOT NULL,
        [document_version] nvarchar(100) NOT NULL,
        [document_year] smallint NULL,
        [retrieval_date] date NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [UQ_kb_source_document_version] UNIQUE ([source_type], [title], [document_version]),
        CONSTRAINT [CK_kb_source_document_type] CHECK ([source_type] IN ('INSURANCE_PAYMENT','GUIDELINE_INDICATION','NMPA_LABEL')),
        CONSTRAINT [CK_kb_source_document_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb].[source_fragment]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[source_fragment] (
        [source_fragment_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_source_fragment] PRIMARY KEY,
        [source_document_id] nvarchar(80) NOT NULL,
        [anchor] nvarchar(1000) NOT NULL,
        [page_numbers_json] nvarchar(max) NOT NULL,
        [original_text] nvarchar(max) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_source_fragment_document] FOREIGN KEY ([source_document_id]) REFERENCES [kb].[source_document]([source_document_id]),
        CONSTRAINT [UQ_kb_source_fragment_anchor] UNIQUE ([source_document_id], [content_checksum]),
        CONSTRAINT [CK_kb_source_fragment_pages] CHECK (ISJSON([page_numbers_json]) = 1),
        CONSTRAINT [CK_kb_source_fragment_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb].[drug_concept]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[drug_concept] (
        [drug_concept_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_drug_concept] PRIMARY KEY,
        [canonical_name] nvarchar(300) NOT NULL,
        [normalized_name] nvarchar(300) NOT NULL,
        [lifecycle] varchar(24) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [UQ_kb_drug_concept_name] UNIQUE ([normalized_name]),
        CONSTRAINT [CK_kb_drug_concept_lifecycle] CHECK ([lifecycle] IN ('DRAFT','IN_REVIEW','CHANGES_REQUESTED','APPROVED','RELEASED','REJECTED','RETIRED')),
        CONSTRAINT [CK_kb_drug_concept_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb].[drug_class]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[drug_class] (
        [drug_class_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_drug_class] PRIMARY KEY,
        [canonical_name] nvarchar(300) NOT NULL,
        [match_terms_json] nvarchar(max) NOT NULL,
        [lifecycle] varchar(24) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [UQ_kb_drug_class_name] UNIQUE ([canonical_name]),
        CONSTRAINT [CK_kb_drug_class_terms] CHECK (ISJSON([match_terms_json]) = 1),
        CONSTRAINT [CK_kb_drug_class_lifecycle] CHECK ([lifecycle] IN ('DRAFT','IN_REVIEW','CHANGES_REQUESTED','APPROVED','RELEASED','REJECTED','RETIRED')),
        CONSTRAINT [CK_kb_drug_class_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb].[drug_product]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[drug_product] (
        [drug_product_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_drug_product] PRIMARY KEY,
        [drug_concept_id] nvarchar(80) NOT NULL,
        [product_name] nvarchar(500) NOT NULL,
        [dosage_form] nvarchar(200) NULL,
        [manufacturer] nvarchar(300) NULL,
        [source_kind] varchar(40) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_drug_product_concept] FOREIGN KEY ([drug_concept_id]) REFERENCES [kb].[drug_concept]([drug_concept_id])
    );
END;

IF OBJECT_ID(N'[kb].[drug_code_xref]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[drug_code_xref] (
        [drug_code_xref_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_drug_code_xref] PRIMARY KEY,
        [drug_product_id] nvarchar(80) NOT NULL,
        [code_system] varchar(32) NOT NULL,
        [code_value] nvarchar(200) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_drug_code_product] FOREIGN KEY ([drug_product_id]) REFERENCES [kb].[drug_product]([drug_product_id]),
        CONSTRAINT [UQ_kb_drug_code] UNIQUE ([code_system], [code_value], [drug_product_id])
    );
END;

IF OBJECT_ID(N'[kb].[curated_knowledge_atom]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[curated_knowledge_atom] (
        [atom_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_curated_atom] PRIMARY KEY,
        [source_rule_id] nvarchar(32) NOT NULL,
        [oncology] bit NOT NULL,
        [rule_status] varchar(16) NOT NULL,
        [source_field_or_test] nvarchar(300) NOT NULL,
        [atom_type] varchar(40) NOT NULL,
        [canonical_payload] nvarchar(max) NOT NULL,
        [source_checksum] char(71) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        [migration_status] varchar(16) NOT NULL,
        CONSTRAINT [CK_kb_atom_payload] CHECK (ISJSON([canonical_payload]) = 1),
        CONSTRAINT [CK_kb_atom_type] CHECK ([atom_type] IN ('SOURCE_RULE','CLINICAL_EXTENSION','EVIDENCE_POLICY','NORMALIZATION','DOCUMENTATION_GUIDANCE','REGRESSION_GOLD')),
        CONSTRAINT [CK_kb_atom_status] CHECK ([migration_status] IN ('DISCOVERED','MAPPED','VERIFIED')),
        CONSTRAINT [CK_kb_atom_rule_status] CHECK ([rule_status] IN ('ready','drafting','abandoned')),
        CONSTRAINT [CK_kb_atom_checksum] CHECK ([source_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb].[curated_knowledge_mapping]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[curated_knowledge_mapping] (
        [mapping_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_curated_mapping] PRIMARY KEY,
        [atom_id] nvarchar(80) NOT NULL,
        [target_kind] varchar(32) NOT NULL,
        [target_id] nvarchar(128) NOT NULL,
        [verification_evidence] nvarchar(1000) NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_curated_mapping_atom] FOREIGN KEY ([atom_id]) REFERENCES [kb].[curated_knowledge_atom]([atom_id]),
        CONSTRAINT [UQ_kb_curated_mapping_target] UNIQUE ([atom_id], [target_kind], [target_id]),
        CONSTRAINT [CK_kb_mapping_target] CHECK ([target_kind] IN ('CONDITION','DICTIONARY','EVALUATOR_POLICY','REVIEW_GUIDANCE','REGRESSION_CASE'))
    );
END;

IF OBJECT_ID(N'[kb].[eligibility_rule_revision]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[eligibility_rule_revision] (
        [rule_revision_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_eligibility_revision] PRIMARY KEY,
        [logical_rule_id] nvarchar(80) NOT NULL,
        [drug_concept_id] nvarchar(80) NOT NULL,
        [source_fragment_id] nvarchar(80) NOT NULL,
        [policy_scope] varchar(32) NOT NULL,
        [lifecycle] varchar(24) NOT NULL,
        [effective_from] date NOT NULL,
        [effective_to] date NOT NULL,
        [effective_date_basis] varchar(32) NOT NULL,
        [date_override_reason] nvarchar(1000) NULL,
        [date_review_comment] nvarchar(1000) NULL,
        [historical_application_policy] varchar(64) NOT NULL,
        [supersedes_revision_id] nvarchar(80) NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_rule_drug] FOREIGN KEY ([drug_concept_id]) REFERENCES [kb].[drug_concept]([drug_concept_id]),
        CONSTRAINT [FK_kb_rule_source] FOREIGN KEY ([source_fragment_id]) REFERENCES [kb].[source_fragment]([source_fragment_id]),
        CONSTRAINT [FK_kb_rule_supersedes] FOREIGN KEY ([supersedes_revision_id]) REFERENCES [kb].[eligibility_rule_revision]([rule_revision_id]),
        CONSTRAINT [UQ_kb_rule_revision_checksum] UNIQUE ([logical_rule_id], [content_checksum]),
        CONSTRAINT [CK_kb_rule_dates] CHECK ([effective_from] <= [effective_to]),
        CONSTRAINT [CK_kb_rule_scope] CHECK ([policy_scope] IN ('INSURANCE_PAYMENT','GUIDELINE_INDICATION','NMPA_LABEL')),
        CONSTRAINT [CK_kb_rule_lifecycle] CHECK ([lifecycle] IN ('DRAFT','IN_REVIEW','CHANGES_REQUESTED','APPROVED','RELEASED','REJECTED','RETIRED')),
        CONSTRAINT [CK_kb_rule_date_basis] CHECK ([effective_date_basis] IN ('SOURCE_EXPLICIT','CURRENT_FILE_ASSUMPTION','EXPERT_OVERRIDE')),
        CONSTRAINT [CK_kb_rule_date_override] CHECK ([effective_date_basis] <> 'EXPERT_OVERRIDE' OR ([date_override_reason] IS NOT NULL AND [date_review_comment] IS NOT NULL)),
        CONSTRAINT [CK_kb_rule_history_policy] CHECK ([historical_application_policy] = 'APPLY_CURRENT_RELEASE_WITH_WARNING')
    );
END;

IF OBJECT_ID(N'[kb].[eligibility_branch]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[eligibility_branch] (
        [branch_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_eligibility_branch] PRIMARY KEY,
        [rule_revision_id] nvarchar(80) NOT NULL,
        [source_fragment_id] nvarchar(80) NOT NULL,
        [ordinal_no] int NOT NULL,
        [source_text] nvarchar(max) NOT NULL,
        [source_span_start] int NOT NULL,
        [source_span_end] int NOT NULL,
        [disposition] varchar(24) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_branch_revision] FOREIGN KEY ([rule_revision_id]) REFERENCES [kb].[eligibility_rule_revision]([rule_revision_id]),
        CONSTRAINT [FK_kb_branch_source] FOREIGN KEY ([source_fragment_id]) REFERENCES [kb].[source_fragment]([source_fragment_id]),
        CONSTRAINT [UQ_kb_branch_ordinal] UNIQUE ([rule_revision_id], [ordinal_no]),
        CONSTRAINT [CK_kb_branch_span] CHECK ([source_span_start] >= 0 AND [source_span_end] >= [source_span_start]),
        CONSTRAINT [CK_kb_branch_disposition] CHECK ([disposition] IN ('approved','in_review','rejected','unsupported'))
    );
END;

IF OBJECT_ID(N'[kb].[condition_node]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[condition_node] (
        [node_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_condition_node] PRIMARY KEY,
        [branch_id] nvarchar(80) NOT NULL,
        [parent_node_id] nvarchar(80) NULL,
        [sibling_order] int NOT NULL,
        [node_kind] varchar(8) NOT NULL,
        [criterion_type] varchar(40) NULL,
        [operator] varchar(32) NULL,
        [target_kind] varchar(16) NULL,
        [target_id] nvarchar(128) NULL,
        [expected_value_json] nvarchar(max) NULL,
        [combination_requirement] varchar(24) NULL,
        [source_fragment_id] nvarchar(80) NOT NULL,
        [source_span_start] int NOT NULL,
        [source_span_end] int NOT NULL,
        [disposition] varchar(24) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_node_branch] FOREIGN KEY ([branch_id]) REFERENCES [kb].[eligibility_branch]([branch_id]),
        CONSTRAINT [FK_kb_node_parent] FOREIGN KEY ([parent_node_id]) REFERENCES [kb].[condition_node]([node_id]),
        CONSTRAINT [FK_kb_node_source] FOREIGN KEY ([source_fragment_id]) REFERENCES [kb].[source_fragment]([source_fragment_id]),
        CONSTRAINT [UQ_kb_node_order] UNIQUE ([branch_id], [parent_node_id], [sibling_order]),
        CONSTRAINT [CK_kb_node_kind] CHECK ([node_kind] IN ('ALL','ANY','LEAF')),
        CONSTRAINT [CK_kb_node_expected] CHECK ([expected_value_json] IS NULL OR ISJSON([expected_value_json]) = 1),
        CONSTRAINT [CK_kb_node_shape] CHECK (([node_kind] = 'LEAF' AND [criterion_type] IS NOT NULL AND [operator] IS NOT NULL) OR ([node_kind] IN ('ALL','ANY') AND [criterion_type] IS NULL AND [operator] IS NULL))
    );
END;

IF OBJECT_ID(N'[kb].[regimen_revision]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[regimen_revision] (
        [regimen_revision_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_regimen_revision] PRIMARY KEY,
        [logical_regimen_id] nvarchar(80) NOT NULL,
        [canonical_name] nvarchar(300) NOT NULL,
        [lifecycle] varchar(24) NOT NULL,
        [effective_from] date NOT NULL,
        [effective_to] date NOT NULL,
        [effective_date_basis] varchar(32) NOT NULL,
        [date_override_reason] nvarchar(1000) NULL,
        [date_review_comment] nvarchar(1000) NULL,
        [historical_application_policy] varchar(64) NOT NULL,
        [source_refs_json] nvarchar(max) NOT NULL,
        [supersedes_revision_id] nvarchar(80) NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_regimen_supersedes] FOREIGN KEY ([supersedes_revision_id]) REFERENCES [kb].[regimen_revision]([regimen_revision_id]),
        CONSTRAINT [UQ_kb_regimen_revision_checksum] UNIQUE ([logical_regimen_id], [content_checksum]),
        CONSTRAINT [CK_kb_regimen_dates] CHECK ([effective_from] <= [effective_to]),
        CONSTRAINT [CK_kb_regimen_lifecycle] CHECK ([lifecycle] IN ('DRAFT','IN_REVIEW','CHANGES_REQUESTED','APPROVED','RELEASED','REJECTED','RETIRED')),
        CONSTRAINT [CK_kb_regimen_date_basis] CHECK ([effective_date_basis] IN ('SOURCE_EXPLICIT','CURRENT_FILE_ASSUMPTION','EXPERT_OVERRIDE')),
        CONSTRAINT [CK_kb_regimen_date_override] CHECK ([effective_date_basis] <> 'EXPERT_OVERRIDE' OR ([date_override_reason] IS NOT NULL AND [date_review_comment] IS NOT NULL)),
        CONSTRAINT [CK_kb_regimen_history_policy] CHECK ([historical_application_policy] = 'APPLY_CURRENT_RELEASE_WITH_WARNING'),
        CONSTRAINT [CK_kb_regimen_source_refs] CHECK (ISJSON([source_refs_json]) = 1)
    );
END;

IF OBJECT_ID(N'[kb].[regimen_alias]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[regimen_alias] (
        [alias_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_regimen_alias] PRIMARY KEY,
        [regimen_revision_id] nvarchar(80) NULL,
        [original_alias] nvarchar(300) NOT NULL,
        [normalized_alias] nvarchar(300) NOT NULL,
        [alias_type] varchar(32) NOT NULL,
        [language_code] varchar(16) NOT NULL,
        [aggregate_frequency] int NULL,
        [source_corpus_checksum] char(71) NULL,
        [review_status] varchar(24) NOT NULL,
        [is_ambiguous] bit NOT NULL CONSTRAINT [DF_kb_regimen_alias_ambiguous] DEFAULT 0,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_alias_revision] FOREIGN KEY ([regimen_revision_id]) REFERENCES [kb].[regimen_revision]([regimen_revision_id]),
        CONSTRAINT [CK_kb_alias_frequency] CHECK ([aggregate_frequency] IS NULL OR [aggregate_frequency] >= 0)
    );
END;

IF OBJECT_ID(N'[kb].[regimen_context]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[regimen_context] (
        [context_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_regimen_context] PRIMARY KEY,
        [regimen_revision_id] nvarchar(80) NOT NULL,
        [cancer_context] nvarchar(300) NOT NULL,
        [histology] nvarchar(300) NULL,
        [clinical_setting] nvarchar(300) NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_context_revision] FOREIGN KEY ([regimen_revision_id]) REFERENCES [kb].[regimen_revision]([regimen_revision_id]),
        CONSTRAINT [UQ_kb_context] UNIQUE ([regimen_revision_id], [cancer_context], [histology], [clinical_setting])
    );
END;

IF OBJECT_ID(N'[kb].[regimen_component]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[regimen_component] (
        [component_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_regimen_component] PRIMARY KEY,
        [regimen_revision_id] nvarchar(80) NOT NULL,
        [target_kind] varchar(16) NOT NULL,
        [target_id] nvarchar(128) NOT NULL,
        [token] nvarchar(64) NOT NULL,
        [component_role] varchar(32) NOT NULL,
        [requirement] varchar(24) NOT NULL,
        [sibling_order] int NOT NULL,
        [source_fragment_id] nvarchar(80) NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_component_revision] FOREIGN KEY ([regimen_revision_id]) REFERENCES [kb].[regimen_revision]([regimen_revision_id]),
        CONSTRAINT [FK_kb_component_source] FOREIGN KEY ([source_fragment_id]) REFERENCES [kb].[source_fragment]([source_fragment_id]),
        CONSTRAINT [UQ_kb_component_order] UNIQUE ([regimen_revision_id], [sibling_order]),
        CONSTRAINT [CK_kb_component_target] CHECK ([target_kind] IN ('CONCEPT','CLASS')),
        CONSTRAINT [CK_kb_component_requirement] CHECK ([requirement] IN ('REQUIRED','OPTIONAL','WITH_OR_WITHOUT'))
    );
END;

IF OBJECT_ID(N'[kb].[regimen_schedule_component]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[regimen_schedule_component] (
        [schedule_component_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_regimen_schedule] PRIMARY KEY,
        [regimen_revision_id] nvarchar(80) NOT NULL,
        [component_id] nvarchar(80) NOT NULL,
        [dose_value] decimal(18,6) NULL,
        [dose_unit] nvarchar(32) NULL,
        [dose_basis] nvarchar(64) NULL,
        [route] nvarchar(64) NULL,
        [administration_days] nvarchar(200) NULL,
        [cycle_length_days] int NULL,
        [max_cycles] int NULL,
        [treatment_phase] nvarchar(64) NULL,
        [sequence_no] int NULL,
        [publishing_enabled] bit NOT NULL CONSTRAINT [DF_kb_schedule_publish] DEFAULT 0,
        [inference_enabled] bit NOT NULL CONSTRAINT [DF_kb_schedule_inference] DEFAULT 0,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_schedule_revision] FOREIGN KEY ([regimen_revision_id]) REFERENCES [kb].[regimen_revision]([regimen_revision_id]),
        CONSTRAINT [FK_kb_schedule_component] FOREIGN KEY ([component_id]) REFERENCES [kb].[regimen_component]([component_id]),
        CONSTRAINT [CK_kb_schedule_phase1] CHECK ([publishing_enabled] = 0 AND [inference_enabled] = 0)
    );
END;

IF OBJECT_ID(N'[kb].[term_dictionary_entry]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[term_dictionary_entry] (
        [term_entry_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_term_dictionary] PRIMARY KEY,
        [dictionary_name] nvarchar(128) NOT NULL,
        [term_value] nvarchar(300) NOT NULL,
        [description] nvarchar(1000) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [UQ_kb_term_dictionary_value] UNIQUE ([dictionary_name], [term_value]),
        CONSTRAINT [CK_kb_term_dictionary_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb].[authoring_qa_issue]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[authoring_qa_issue] (
        [qa_issue_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_authoring_qa] PRIMARY KEY,
        [import_batch_id] nvarchar(80) NOT NULL,
        [workbook_kind] varchar(24) NOT NULL,
        [qa_code] varchar(64) NOT NULL,
        [entity_id] nvarchar(128) NULL,
        [severity] varchar(24) NOT NULL,
        [detail] nvarchar(2000) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [FK_kb_authoring_qa_batch] FOREIGN KEY ([import_batch_id]) REFERENCES [kb_stg].[import_batch]([import_batch_id]),
        CONSTRAINT [CK_kb_authoring_qa_kind] CHECK ([workbook_kind] IN ('eligibility','regimen')),
        CONSTRAINT [CK_kb_authoring_qa_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

/* 兼容早期草案 DDL；authority 字段禁止猜测回填，既有行未显式分类时整次事务失败。 */
IF COL_LENGTH(N'kb.drug_concept', N'content_checksum') IS NULL
    ALTER TABLE [kb].[drug_concept] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.drug_product', N'content_checksum') IS NULL
    ALTER TABLE [kb].[drug_product] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.drug_code_xref', N'content_checksum') IS NULL
    ALTER TABLE [kb].[drug_code_xref] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.curated_knowledge_atom', N'content_checksum') IS NULL
    ALTER TABLE [kb].[curated_knowledge_atom] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.curated_knowledge_atom', N'oncology') IS NULL
    ALTER TABLE [kb].[curated_knowledge_atom] ADD [oncology] bit NULL;
IF COL_LENGTH(N'kb.curated_knowledge_atom', N'rule_status') IS NULL
    ALTER TABLE [kb].[curated_knowledge_atom] ADD [rule_status] varchar(16) NULL;
IF EXISTS (
    SELECT 1
    FROM sys.columns
    WHERE [object_id] = OBJECT_ID(N'[kb].[curated_knowledge_atom]')
      AND [name] IN (N'oncology', N'rule_status')
      AND [is_nullable] = 1
)
BEGIN
    EXEC sys.sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM [kb].[curated_knowledge_atom]
            WHERE [oncology] IS NULL OR [rule_status] IS NULL
        )
            THROW 51002, N''既有 curated knowledge atom 必须先显式分类，禁止自动回填 authority 字段'', 1;
        IF EXISTS (
            SELECT 1 FROM sys.columns
            WHERE [object_id] = OBJECT_ID(N''[kb].[curated_knowledge_atom]'')
              AND [name] = N''oncology'' AND [is_nullable] = 1
        )
            ALTER TABLE [kb].[curated_knowledge_atom] ALTER COLUMN [oncology] bit NOT NULL;
        IF EXISTS (
            SELECT 1 FROM sys.columns
            WHERE [object_id] = OBJECT_ID(N''[kb].[curated_knowledge_atom]'')
              AND [name] = N''rule_status'' AND [is_nullable] = 1
        )
            ALTER TABLE [kb].[curated_knowledge_atom] ALTER COLUMN [rule_status] varchar(16) NOT NULL;
    ';
END;
IF OBJECT_ID(N'[kb].[CK_kb_atom_rule_status]', N'C') IS NULL
    ALTER TABLE [kb].[curated_knowledge_atom] WITH CHECK
        ADD CONSTRAINT [CK_kb_atom_rule_status]
        CHECK ([rule_status] IN ('ready','drafting','abandoned'));
IF COL_LENGTH(N'kb.curated_knowledge_mapping', N'content_checksum') IS NULL
    ALTER TABLE [kb].[curated_knowledge_mapping] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.eligibility_branch', N'content_checksum') IS NULL
    ALTER TABLE [kb].[eligibility_branch] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.condition_node', N'disposition') IS NULL
    ALTER TABLE [kb].[condition_node] ADD [disposition] varchar(24) NULL;
IF COL_LENGTH(N'kb.condition_node', N'content_checksum') IS NULL
    ALTER TABLE [kb].[condition_node] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.regimen_revision', N'effective_date_basis') IS NULL
    ALTER TABLE [kb].[regimen_revision] ADD [effective_date_basis] varchar(32) NULL;
IF COL_LENGTH(N'kb.regimen_revision', N'date_override_reason') IS NULL
    ALTER TABLE [kb].[regimen_revision] ADD [date_override_reason] nvarchar(1000) NULL;
IF COL_LENGTH(N'kb.regimen_revision', N'date_review_comment') IS NULL
    ALTER TABLE [kb].[regimen_revision] ADD [date_review_comment] nvarchar(1000) NULL;
IF COL_LENGTH(N'kb.regimen_revision', N'historical_application_policy') IS NULL
    ALTER TABLE [kb].[regimen_revision] ADD [historical_application_policy] varchar(64) NULL;
IF COL_LENGTH(N'kb.regimen_revision', N'source_refs_json') IS NULL
    ALTER TABLE [kb].[regimen_revision] ADD [source_refs_json] nvarchar(max) NULL;
IF COL_LENGTH(N'kb.regimen_alias', N'content_checksum') IS NULL
    ALTER TABLE [kb].[regimen_alias] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.regimen_context', N'content_checksum') IS NULL
    ALTER TABLE [kb].[regimen_context] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.regimen_component', N'content_checksum') IS NULL
    ALTER TABLE [kb].[regimen_component] ADD [content_checksum] char(71) NULL;
IF COL_LENGTH(N'kb.regimen_schedule_component', N'content_checksum') IS NULL
    ALTER TABLE [kb].[regimen_schedule_component] ADD [content_checksum] char(71) NULL;

IF OBJECT_ID(N'[kb].[review_event]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[review_event] (
        [review_event_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_review_event] PRIMARY KEY,
        [entity_type] varchar(40) NOT NULL,
        [entity_id] nvarchar(128) NOT NULL,
        [field_name] nvarchar(128) NOT NULL,
        [decision] varchar(32) NOT NULL,
        [expert_value_json] nvarchar(max) NULL,
        [comment] nvarchar(2000) NOT NULL,
        [evidence_reference] nvarchar(1000) NULL,
        [reviewer_id] nvarchar(128) NOT NULL,
        [reviewed_at] datetime2(3) NOT NULL,
        [reviewed_content_checksum] char(71) NOT NULL,
        [previous_event_id] nvarchar(80) NULL,
        CONSTRAINT [FK_kb_review_previous] FOREIGN KEY ([previous_event_id]) REFERENCES [kb].[review_event]([review_event_id]),
        CONSTRAINT [CK_kb_review_decision] CHECK ([decision] IN ('APPROVE','APPROVE_WITH_EDIT','REJECT','UNABLE_TO_DETERMINE')),
        CONSTRAINT [CK_kb_review_value] CHECK ([expert_value_json] IS NULL OR ISJSON([expert_value_json]) = 1),
        CONSTRAINT [CK_kb_reviewed_content_checksum] CHECK (
            LEN([reviewed_content_checksum]) = 71
            AND LEFT([reviewed_content_checksum], 7) = 'sha256:'
            AND SUBSTRING([reviewed_content_checksum], 8, 64)
                COLLATE Latin1_General_100_BIN2 NOT LIKE '%[^0-9a-f]%'
        )
    );
END;

/*
 * 旧 review_event 的被审核内容无法安全猜测。首次升级若存在历史行，必须由 DBA/领域人员
 * 依据当时实体内容显式补齐 checksum；脚本只在全部非 NULL 后收紧为 NOT NULL。
 */
IF COL_LENGTH(N'kb.review_event', N'reviewed_content_checksum') IS NULL
    EXEC sys.sp_executesql N'
        ALTER TABLE [kb].[review_event]
            ADD [reviewed_content_checksum] char(71) NULL;
    ';
IF EXISTS (
    SELECT 1
    FROM sys.columns
    WHERE [object_id] = OBJECT_ID(N'[kb].[review_event]')
      AND [name] = N'reviewed_content_checksum'
      AND [is_nullable] = 1
)
BEGIN
    EXEC sys.sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM [kb].[review_event]
            WHERE [reviewed_content_checksum] IS NULL
        )
            THROW 51003, N''既有 review_event 必须人工补齐 reviewed_content_checksum，禁止自动猜测'', 1;
        ALTER TABLE [kb].[review_event]
            ALTER COLUMN [reviewed_content_checksum] char(71) NOT NULL;
    ';
END;
IF OBJECT_ID(N'[kb].[CK_kb_reviewed_content_checksum]', N'C') IS NULL
    EXEC sys.sp_executesql N'
        ALTER TABLE [kb].[review_event] WITH CHECK
            ADD CONSTRAINT [CK_kb_reviewed_content_checksum] CHECK (
                LEN([reviewed_content_checksum]) = 71
                AND LEFT([reviewed_content_checksum], 7) = ''sha256:''
                AND SUBSTRING([reviewed_content_checksum], 8, 64)
                    COLLATE Latin1_General_100_BIN2 NOT LIKE ''%[^0-9a-f]%''
            );
    ';

IF OBJECT_ID(N'[kb].[knowledge_release]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[knowledge_release] (
        [release_id] nvarchar(80) NOT NULL CONSTRAINT [PK_kb_knowledge_release] PRIMARY KEY,
        [status] varchar(24) NOT NULL,
        [source_snapshot_checksum] char(71) NOT NULL,
        [created_by] nvarchar(128) NOT NULL,
        [domain_reviewer_id] nvarchar(128) NOT NULL,
        [published_by] nvarchar(128) NULL,
        [created_at] datetime2(3) NOT NULL CONSTRAINT [DF_kb_release_created] DEFAULT SYSUTCDATETIME(),
        [published_at] datetime2(3) NULL,
        [release_checksum] char(71) NOT NULL,
        [previous_release_id] nvarchar(80) NULL,
        CONSTRAINT [FK_kb_release_previous] FOREIGN KEY ([previous_release_id]) REFERENCES [kb].[knowledge_release]([release_id]),
        CONSTRAINT [CK_kb_release_status] CHECK ([status] IN ('CANDIDATE','PUBLISHED','ROLLED_BACK','RETIRED')),
        CONSTRAINT [CK_kb_release_separation] CHECK ([published_by] IS NULL OR [published_by] <> [domain_reviewer_id])
    );
END;

IF OBJECT_ID(N'[kb].[release_item]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb].[release_item] (
        [release_id] nvarchar(80) NOT NULL,
        [entity_type] varchar(40) NOT NULL,
        [revision_id] nvarchar(80) NOT NULL,
        [content_checksum] char(71) NOT NULL,
        CONSTRAINT [PK_kb_release_item] PRIMARY KEY ([release_id], [entity_type], [revision_id]),
        CONSTRAINT [FK_kb_release_item_release] FOREIGN KEY ([release_id]) REFERENCES [kb].[knowledge_release]([release_id]),
        CONSTRAINT [CK_kb_release_item_checksum] CHECK ([content_checksum] LIKE 'sha256:%')
    );
END;

IF OBJECT_ID(N'[kb_meta].[deployment_pointer]', N'U') IS NULL
BEGIN
    CREATE TABLE [kb_meta].[deployment_pointer] (
        [pointer_name] varchar(32) NOT NULL CONSTRAINT [PK_kb_deployment_pointer] PRIMARY KEY,
        [active_release_id] nvarchar(80) NOT NULL,
        [previous_release_id] nvarchar(80) NULL,
        [changed_by] nvarchar(128) NOT NULL,
        [changed_at] datetime2(3) NOT NULL CONSTRAINT [DF_kb_deployment_pointer_changed] DEFAULT SYSUTCDATETIME(),
        CONSTRAINT [FK_kb_pointer_active] FOREIGN KEY ([active_release_id]) REFERENCES [kb].[knowledge_release]([release_id]),
        CONSTRAINT [FK_kb_pointer_previous] FOREIGN KEY ([previous_release_id]) REFERENCES [kb].[knowledge_release]([release_id]),
        CONSTRAINT [CK_kb_pointer_name] CHECK ([pointer_name] = 'oncology')
    );
END;

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'[kb].[eligibility_rule_revision]') AND name = N'IX_kb_rule_logical_dates')
    CREATE INDEX [IX_kb_rule_logical_dates] ON [kb].[eligibility_rule_revision]([logical_rule_id], [effective_from], [effective_to], [lifecycle]);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'[kb].[regimen_alias]') AND name = N'IX_kb_alias_normalized')
    CREATE INDEX [IX_kb_alias_normalized] ON [kb].[regimen_alias]([normalized_alias], [review_status], [is_ambiguous]);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'[kb_stg].[import_row]') AND name = N'IX_kb_import_row_stable')
    CREATE INDEX [IX_kb_import_row_stable] ON [kb_stg].[import_row]([stable_row_id], [row_checksum]);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id = OBJECT_ID(N'[kb].[review_event]') AND name = N'IX_kb_review_entity_time')
    CREATE INDEX [IX_kb_review_entity_time] ON [kb].[review_event]([entity_type], [entity_id], [reviewed_at]);

GO

/*
 * 供知识管理员与领域专家直接浏览的只读视图。视图只展开 typed authoring 数据，
 * 不把 staging canonical_payload 当作业务真相，也不改变审核或发布状态。
 */
CREATE OR ALTER VIEW [kb].[vw_eligibility_rule_overview]
AS
SELECT
    concept.[canonical_name] AS [药品名称],
    revision.[policy_scope] AS [政策范围],
    revision.[lifecycle] AS [规则版本状态],
    revision.[effective_from] AS [生效开始日],
    revision.[effective_to] AS [生效结束日],
    revision.[effective_date_basis] AS [生效期依据],
    document.[title] AS [来源文件],
    document.[document_version] AS [来源版本],
    fragment.[anchor] AS [来源定位],
    branch.[ordinal_no] AS [分支序号],
    branch.[source_text] AS [分支原文],
    branch.[disposition] AS [分支审核状态],
    (SELECT COUNT_BIG(*) FROM [kb].[condition_node] AS node WHERE node.[branch_id] = branch.[branch_id]) AS [条件节点数],
    (SELECT COUNT_BIG(*) FROM [kb].[condition_node] AS node WHERE node.[branch_id] = branch.[branch_id] AND node.[node_kind] = 'LEAF') AS [叶子条件数],
    revision_review.[decision] AS [规则最新审核决定],
    revision_review.[reviewer_id] AS [规则最新审核人],
    revision_review.[reviewed_at] AS [规则最新审核时间],
    branch_review.[decision] AS [分支最新审核决定],
    branch_review.[reviewer_id] AS [分支最新审核人],
    branch_review.[reviewed_at] AS [分支最新审核时间],
    revision.[logical_rule_id] AS [逻辑规则ID],
    revision.[rule_revision_id] AS [规则版本ID],
    branch.[branch_id] AS [分支ID],
    revision.[content_checksum] AS [规则内容校验和],
    branch.[content_checksum] AS [分支内容校验和]
FROM [kb].[eligibility_rule_revision] AS revision
JOIN [kb].[drug_concept] AS concept
  ON concept.[drug_concept_id] = revision.[drug_concept_id]
JOIN [kb].[source_fragment] AS fragment
  ON fragment.[source_fragment_id] = revision.[source_fragment_id]
JOIN [kb].[source_document] AS document
  ON document.[source_document_id] = fragment.[source_document_id]
JOIN [kb].[eligibility_branch] AS branch
  ON branch.[rule_revision_id] = revision.[rule_revision_id]
OUTER APPLY (
    SELECT TOP (1) event.[decision], event.[reviewer_id], event.[reviewed_at]
    FROM [kb].[review_event] AS event
    WHERE event.[entity_type] = 'eligibility_revision'
      AND event.[entity_id] = revision.[rule_revision_id]
    ORDER BY event.[reviewed_at] DESC, event.[review_event_id] DESC
) AS revision_review
OUTER APPLY (
    SELECT TOP (1) event.[decision], event.[reviewer_id], event.[reviewed_at]
    FROM [kb].[review_event] AS event
    WHERE event.[entity_type] = 'branch'
      AND event.[entity_id] = branch.[branch_id]
    ORDER BY event.[reviewed_at] DESC, event.[review_event_id] DESC
) AS branch_review;
GO

CREATE OR ALTER VIEW [kb].[vw_eligibility_condition_detail]
AS
SELECT
    concept.[canonical_name] AS [药品名称],
    revision.[policy_scope] AS [政策范围],
    revision.[lifecycle] AS [规则版本状态],
    branch.[ordinal_no] AS [分支序号],
    branch.[source_text] AS [分支原文],
    node.[sibling_order] AS [同级顺序],
    node.[node_kind] AS [节点类型],
    node.[criterion_type] AS [条件类型],
    node.[operator] AS [比较符],
    node.[target_kind] AS [目标类型],
    COALESCE(target_concept.[canonical_name], target_class.[canonical_name], target_regimen.[canonical_name], node.[target_id]) AS [目标名称],
    node.[expected_value_json] AS [期望值],
    node.[combination_requirement] AS [联合要求],
    node.[disposition] AS [节点审核状态],
    node_fragment.[anchor] AS [节点来源定位],
    latest_review.[decision] AS [最新审核决定],
    latest_review.[reviewer_id] AS [最新审核人],
    latest_review.[reviewed_at] AS [最新审核时间],
    latest_review.[comment] AS [最新审核意见],
    revision.[rule_revision_id] AS [规则版本ID],
    branch.[branch_id] AS [分支ID],
    node.[node_id] AS [节点ID],
    node.[parent_node_id] AS [父节点ID],
    node.[content_checksum] AS [节点内容校验和]
FROM [kb].[condition_node] AS node
JOIN [kb].[eligibility_branch] AS branch
  ON branch.[branch_id] = node.[branch_id]
JOIN [kb].[eligibility_rule_revision] AS revision
  ON revision.[rule_revision_id] = branch.[rule_revision_id]
JOIN [kb].[drug_concept] AS concept
  ON concept.[drug_concept_id] = revision.[drug_concept_id]
JOIN [kb].[source_fragment] AS node_fragment
  ON node_fragment.[source_fragment_id] = node.[source_fragment_id]
LEFT JOIN [kb].[drug_concept] AS target_concept
  ON node.[target_kind] = 'CONCEPT' AND target_concept.[drug_concept_id] = node.[target_id]
LEFT JOIN [kb].[drug_class] AS target_class
  ON node.[target_kind] = 'CLASS' AND target_class.[drug_class_id] = node.[target_id]
OUTER APPLY (
    SELECT TOP (1) candidate.[canonical_name]
    FROM [kb].[regimen_revision] AS candidate
    WHERE node.[target_kind] = 'REGIMEN'
      AND candidate.[logical_regimen_id] = node.[target_id]
    ORDER BY
        CASE candidate.[lifecycle]
            WHEN 'RELEASED' THEN 0
            WHEN 'APPROVED' THEN 1
            WHEN 'IN_REVIEW' THEN 2
            WHEN 'DRAFT' THEN 3
            WHEN 'CHANGES_REQUESTED' THEN 4
            ELSE 5
        END,
        candidate.[effective_from] DESC,
        candidate.[regimen_revision_id] DESC
) AS target_regimen
OUTER APPLY (
    SELECT TOP (1) event.[decision], event.[reviewer_id], event.[reviewed_at], event.[comment]
    FROM [kb].[review_event] AS event
    WHERE event.[entity_type] = 'condition_node'
      AND event.[entity_id] = node.[node_id]
    ORDER BY event.[reviewed_at] DESC, event.[review_event_id] DESC
) AS latest_review;
GO

CREATE OR ALTER VIEW [kb].[vw_regimen_composition]
AS
SELECT
    revision.[canonical_name] AS [方案名称],
    revision.[lifecycle] AS [方案版本状态],
    revision.[effective_from] AS [生效开始日],
    revision.[effective_to] AS [生效结束日],
    revision.[effective_date_basis] AS [生效期依据],
    (
        SELECT STRING_AGG(CONVERT(nvarchar(max), alias.[original_alias]), N'；')
        FROM [kb].[regimen_alias] AS alias
        WHERE alias.[regimen_revision_id] = revision.[regimen_revision_id]
    ) AS [方案别名],
    (
        SELECT STRING_AGG(
            CONVERT(nvarchar(max), CONCAT(context.[cancer_context],
                CASE WHEN context.[histology] IS NULL THEN N'' ELSE CONCAT(N' / ', context.[histology]) END,
                CASE WHEN context.[clinical_setting] IS NULL THEN N'' ELSE CONCAT(N' / ', context.[clinical_setting]) END)),
            N'；'
        )
        FROM [kb].[regimen_context] AS context
        WHERE context.[regimen_revision_id] = revision.[regimen_revision_id]
    ) AS [适用场景],
    (
        SELECT STRING_AGG(
            CONVERT(nvarchar(max), CONCAT(component.[sibling_order], N'. ', component.[token], N'：',
                COALESCE(target_concept.[canonical_name], target_class.[canonical_name], component.[target_id]),
                N' [', component.[requirement], N']')),
            N'；'
        )
        FROM [kb].[regimen_component] AS component
        LEFT JOIN [kb].[drug_concept] AS target_concept
          ON component.[target_kind] = 'CONCEPT' AND target_concept.[drug_concept_id] = component.[target_id]
        LEFT JOIN [kb].[drug_class] AS target_class
          ON component.[target_kind] = 'CLASS' AND target_class.[drug_class_id] = component.[target_id]
        WHERE component.[regimen_revision_id] = revision.[regimen_revision_id]
    ) AS [方案组成],
    (SELECT COUNT_BIG(*) FROM [kb].[regimen_alias] AS alias WHERE alias.[regimen_revision_id] = revision.[regimen_revision_id]) AS [别名数],
    (SELECT COUNT_BIG(*) FROM [kb].[regimen_context] AS context WHERE context.[regimen_revision_id] = revision.[regimen_revision_id]) AS [场景数],
    (SELECT COUNT_BIG(*) FROM [kb].[regimen_component] AS component WHERE component.[regimen_revision_id] = revision.[regimen_revision_id]) AS [组分数],
    latest_review.[decision] AS [最新审核决定],
    latest_review.[reviewer_id] AS [最新审核人],
    latest_review.[reviewed_at] AS [最新审核时间],
    revision.[logical_regimen_id] AS [逻辑方案ID],
    revision.[regimen_revision_id] AS [方案版本ID],
    revision.[content_checksum] AS [方案内容校验和]
FROM [kb].[regimen_revision] AS revision
OUTER APPLY (
    SELECT TOP (1) event.[decision], event.[reviewer_id], event.[reviewed_at]
    FROM [kb].[review_event] AS event
    WHERE event.[entity_type] = 'regimen'
      AND event.[entity_id] = revision.[regimen_revision_id]
    ORDER BY event.[reviewed_at] DESC, event.[review_event_id] DESC
) AS latest_review;
GO

CREATE OR ALTER VIEW [kb].[vw_latest_review_status]
AS
WITH ranked_review AS (
    SELECT
        event.*,
        ROW_NUMBER() OVER (
            PARTITION BY event.[entity_type], event.[entity_id], event.[field_name]
            ORDER BY event.[reviewed_at] DESC, event.[review_event_id] DESC
        ) AS [review_rank]
    FROM [kb].[review_event] AS event
)
SELECT
    review.[entity_type] AS [对象类型],
    review.[entity_id] AS [对象ID],
    COALESCE(
        product.[product_name],
        drug_class_entity.[canonical_name],
        source_fragment.[anchor],
        branch.[source_text],
        CONCAT(condition_entity.[criterion_type], N' ', condition_entity.[operator]),
        eligibility_drug.[canonical_name],
        atom.[source_rule_id],
        regimen.[canonical_name],
        alias.[original_alias],
        context.[cancer_context],
        component.[token],
        schedule.[schedule_component_id],
        review.[entity_id]
    ) AS [对象名称],
    review.[field_name] AS [审核字段],
    review.[decision] AS [最新审核决定],
    review.[reviewer_id] AS [审核人],
    review.[reviewed_at] AS [审核时间],
    review.[comment] AS [审核意见],
    review.[evidence_reference] AS [证据引用],
    review.[expert_value_json] AS [专家修订值],
    review.[reviewed_content_checksum] AS [被审核内容校验和],
    review.[review_event_id] AS [审核事件ID],
    review.[previous_event_id] AS [前序审核事件ID]
FROM ranked_review AS review
LEFT JOIN [kb].[drug_product] AS product
  ON review.[entity_type] = 'drug_product' AND product.[drug_product_id] = review.[entity_id]
LEFT JOIN [kb].[drug_class] AS drug_class_entity
  ON review.[entity_type] = 'drug_class' AND drug_class_entity.[drug_class_id] = review.[entity_id]
LEFT JOIN [kb].[source_fragment] AS source_fragment
  ON review.[entity_type] = 'source_fragment' AND source_fragment.[source_fragment_id] = review.[entity_id]
LEFT JOIN [kb].[eligibility_branch] AS branch
  ON review.[entity_type] = 'branch' AND branch.[branch_id] = review.[entity_id]
LEFT JOIN [kb].[condition_node] AS condition_entity
  ON review.[entity_type] = 'condition_node' AND condition_entity.[node_id] = review.[entity_id]
LEFT JOIN [kb].[eligibility_rule_revision] AS eligibility
  ON review.[entity_type] = 'eligibility_revision' AND eligibility.[rule_revision_id] = review.[entity_id]
LEFT JOIN [kb].[drug_concept] AS eligibility_drug
  ON eligibility_drug.[drug_concept_id] = eligibility.[drug_concept_id]
LEFT JOIN [kb].[curated_knowledge_atom] AS atom
  ON review.[entity_type] = 'curated_knowledge_atom' AND atom.[atom_id] = review.[entity_id]
LEFT JOIN [kb].[regimen_revision] AS regimen
  ON review.[entity_type] = 'regimen' AND regimen.[regimen_revision_id] = review.[entity_id]
LEFT JOIN [kb].[regimen_alias] AS alias
  ON review.[entity_type] = 'alias' AND alias.[alias_id] = review.[entity_id]
LEFT JOIN [kb].[regimen_context] AS context
  ON review.[entity_type] = 'context' AND context.[context_id] = review.[entity_id]
LEFT JOIN [kb].[regimen_component] AS component
  ON review.[entity_type] = 'component' AND component.[component_id] = review.[entity_id]
LEFT JOIN [kb].[regimen_schedule_component] AS schedule
  ON review.[entity_type] = 'schedule_component' AND schedule.[schedule_component_id] = review.[entity_id]
WHERE review.[review_rank] = 1;
GO

CREATE OR ALTER VIEW [kb].[vw_import_reconciliation]
AS
SELECT
    batch.[safe_basename] AS [工作簿名],
    batch.[status] AS [导入状态],
    batch.[template_schema_version] AS [模板版本],
    batch.[uploaded_by] AS [上传人],
    batch.[uploaded_at] AS [上传时间],
    batch.[expected_row_count] AS [预期行数],
    (SELECT COUNT_BIG(*) FROM [kb_stg].[import_row] AS staged_row WHERE staged_row.[import_batch_id] = batch.[import_batch_id]) AS [暂存行数],
    (SELECT COUNT_BIG(*) FROM [kb_stg].[import_row] AS staged_row WHERE staged_row.[import_batch_id] = batch.[import_batch_id] AND staged_row.[validation_status] = 'VALID') AS [校验通过行数],
    (SELECT COUNT_BIG(*) FROM [kb_stg].[import_row] AS staged_row WHERE staged_row.[import_batch_id] = batch.[import_batch_id] AND staged_row.[validation_status] = 'INVALID') AS [校验失败行数],
    (SELECT COUNT_BIG(*) FROM [kb].[authoring_qa_issue] AS issue WHERE issue.[import_batch_id] = batch.[import_batch_id]) AS [QA问题数],
    batch.[expected_counts_json] AS [预期分类计数],
    batch.[reconciliation_json] AS [物化对账],
    batch.[error_summary] AS [错误摘要],
    batch.[import_batch_id] AS [导入批次ID],
    batch.[workbook_sha256] AS [工作簿校验和]
FROM [kb_stg].[import_batch] AS batch;
GO

CREATE OR ALTER VIEW [kb].[vw_release_overview]
AS
SELECT
    knowledge_release.[release_id] AS [发布版本ID],
    knowledge_release.[status] AS [发布状态],
    CASE WHEN pointer.[active_release_id] = knowledge_release.[release_id] THEN CAST(1 AS bit) ELSE CAST(0 AS bit) END AS [当前生效],
    knowledge_release.[created_by] AS [构建人],
    knowledge_release.[domain_reviewer_id] AS [领域审核人],
    knowledge_release.[published_by] AS [发布人],
    knowledge_release.[created_at] AS [构建时间],
    knowledge_release.[published_at] AS [发布时间],
    (SELECT COUNT_BIG(*) FROM [kb].[release_item] AS item WHERE item.[release_id] = knowledge_release.[release_id]) AS [发布对象数],
    knowledge_release.[previous_release_id] AS [上一发布版本ID],
    knowledge_release.[source_snapshot_checksum] AS [来源快照校验和],
    knowledge_release.[release_checksum] AS [发布校验和],
    pointer.[changed_by] AS [指针操作人],
    pointer.[changed_at] AS [指针变更时间]
FROM [kb].[knowledge_release] AS knowledge_release
LEFT JOIN [kb_meta].[deployment_pointer] AS pointer
  ON pointer.[pointer_name] = 'oncology';
GO

CREATE OR ALTER TRIGGER [kb].[tr_review_event_append_only]
ON [kb].[review_event]
INSTEAD OF UPDATE, DELETE
AS
    THROW 51010, N'review_event is append-only', 1;
GO

CREATE OR ALTER TRIGGER [kb].[tr_release_immutable]
ON [kb].[release_item]
INSTEAD OF UPDATE, DELETE
AS
    THROW 51011, N'release_item is immutable', 1;
GO

CREATE OR ALTER TRIGGER [kb].[tr_eligibility_revision_no_overlap]
ON [kb].[eligibility_rule_revision]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM [kb].[eligibility_rule_revision] a
        JOIN [kb].[eligibility_rule_revision] b
          ON a.[logical_rule_id] = b.[logical_rule_id]
         AND a.[rule_revision_id] < b.[rule_revision_id]
         AND a.[effective_from] <= b.[effective_to]
         AND b.[effective_from] <= a.[effective_to]
        WHERE a.[lifecycle] IN ('APPROVED','RELEASED')
          AND b.[lifecycle] IN ('APPROVED','RELEASED')
          AND (a.[rule_revision_id] IN (SELECT [rule_revision_id] FROM inserted)
               OR b.[rule_revision_id] IN (SELECT [rule_revision_id] FROM inserted))
    ) THROW 51012, N'approved eligibility revision windows overlap', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_revision_no_overlap]
ON [kb].[regimen_revision]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM [kb].[regimen_revision] a
        JOIN [kb].[regimen_revision] b
          ON a.[logical_regimen_id] = b.[logical_regimen_id]
         AND a.[regimen_revision_id] < b.[regimen_revision_id]
         AND a.[effective_from] <= b.[effective_to]
         AND b.[effective_from] <= a.[effective_to]
        WHERE a.[lifecycle] IN ('APPROVED','RELEASED')
          AND b.[lifecycle] IN ('APPROVED','RELEASED')
          AND (a.[regimen_revision_id] IN (SELECT [regimen_revision_id] FROM inserted)
               OR b.[regimen_revision_id] IN (SELECT [regimen_revision_id] FROM inserted))
    ) THROW 51013, N'approved regimen revision windows overlap', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_eligibility_revision_immutable]
ON [kb].[eligibility_rule_revision]
AFTER UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1 FROM deleted d
        LEFT JOIN inserted i ON i.[rule_revision_id] = d.[rule_revision_id]
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
          AND i.[rule_revision_id] IS NULL
    ) THROW 51014, N'approved eligibility revision cannot be deleted', 1;

    IF EXISTS (
        SELECT 1 FROM deleted d
        JOIN inserted i ON i.[rule_revision_id] = d.[rule_revision_id]
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
          AND (
              i.[logical_rule_id] <> d.[logical_rule_id]
              OR i.[drug_concept_id] <> d.[drug_concept_id]
              OR i.[source_fragment_id] <> d.[source_fragment_id]
              OR i.[policy_scope] <> d.[policy_scope]
              OR i.[effective_from] <> d.[effective_from]
              OR i.[effective_to] <> d.[effective_to]
              OR i.[effective_date_basis] <> d.[effective_date_basis]
              OR ISNULL(i.[date_override_reason], N'') <> ISNULL(d.[date_override_reason], N'')
              OR ISNULL(i.[date_review_comment], N'') <> ISNULL(d.[date_review_comment], N'')
              OR i.[historical_application_policy] <> d.[historical_application_policy]
              OR ISNULL(i.[supersedes_revision_id], N'') <> ISNULL(d.[supersedes_revision_id], N'')
              OR i.[content_checksum] <> d.[content_checksum]
          )
    ) THROW 51014, N'approved eligibility revision content is immutable', 1;

    IF EXISTS (
        SELECT 1 FROM deleted d
        JOIN inserted i ON i.[rule_revision_id] = d.[rule_revision_id]
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
          AND (
              (d.[lifecycle] = 'APPROVED' AND i.[lifecycle] NOT IN ('APPROVED','RELEASED','RETIRED'))
              OR (d.[lifecycle] = 'RELEASED' AND i.[lifecycle] NOT IN ('RELEASED','RETIRED'))
              OR (d.[lifecycle] = 'RETIRED' AND i.[lifecycle] <> 'RETIRED')
          )
    ) THROW 51014, N'eligibility revision lifecycle cannot regress', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_revision_immutable]
ON [kb].[regimen_revision]
AFTER UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1 FROM deleted d
        LEFT JOIN inserted i ON i.[regimen_revision_id] = d.[regimen_revision_id]
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
          AND i.[regimen_revision_id] IS NULL
    ) THROW 51015, N'approved regimen revision cannot be deleted', 1;

    IF EXISTS (
        SELECT 1 FROM deleted d
        JOIN inserted i ON i.[regimen_revision_id] = d.[regimen_revision_id]
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
          AND (
              i.[logical_regimen_id] <> d.[logical_regimen_id]
              OR i.[canonical_name] <> d.[canonical_name]
              OR i.[effective_from] <> d.[effective_from]
              OR i.[effective_to] <> d.[effective_to]
              OR i.[effective_date_basis] <> d.[effective_date_basis]
              OR ISNULL(i.[date_override_reason], N'') <> ISNULL(d.[date_override_reason], N'')
              OR ISNULL(i.[date_review_comment], N'') <> ISNULL(d.[date_review_comment], N'')
              OR i.[historical_application_policy] <> d.[historical_application_policy]
              OR i.[source_refs_json] <> d.[source_refs_json]
              OR ISNULL(i.[supersedes_revision_id], N'') <> ISNULL(d.[supersedes_revision_id], N'')
              OR i.[content_checksum] <> d.[content_checksum]
          )
    ) THROW 51015, N'approved regimen revision content is immutable', 1;

    IF EXISTS (
        SELECT 1 FROM deleted d
        JOIN inserted i ON i.[regimen_revision_id] = d.[regimen_revision_id]
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
          AND (
              (d.[lifecycle] = 'APPROVED' AND i.[lifecycle] NOT IN ('APPROVED','RELEASED','RETIRED'))
              OR (d.[lifecycle] = 'RELEASED' AND i.[lifecycle] NOT IN ('RELEASED','RETIRED'))
              OR (d.[lifecycle] = 'RETIRED' AND i.[lifecycle] <> 'RETIRED')
          )
    ) THROW 51015, N'regimen revision lifecycle cannot regress', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_eligibility_branch_parent_immutable]
ON [kb].[eligibility_branch]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [rule_revision_id] FROM inserted
            UNION
            SELECT [rule_revision_id] FROM deleted
        ) changed
        JOIN [kb].[eligibility_rule_revision] r
          ON r.[rule_revision_id] = changed.[rule_revision_id]
        WHERE r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
    ) THROW 51016, N'frozen eligibility revision blocks branch mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_condition_node_parent_immutable]
ON [kb].[condition_node]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [branch_id] FROM inserted
            UNION
            SELECT [branch_id] FROM deleted
        ) changed
        JOIN [kb].[eligibility_branch] b ON b.[branch_id] = changed.[branch_id]
        JOIN [kb].[eligibility_rule_revision] r
          ON r.[rule_revision_id] = b.[rule_revision_id]
        WHERE r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
    ) THROW 51017, N'frozen eligibility revision blocks condition node mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_alias_parent_immutable]
ON [kb].[regimen_alias]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [regimen_revision_id] FROM inserted WHERE [regimen_revision_id] IS NOT NULL
            UNION
            SELECT [regimen_revision_id] FROM deleted WHERE [regimen_revision_id] IS NOT NULL
        ) changed
        JOIN [kb].[regimen_revision] r
          ON r.[regimen_revision_id] = changed.[regimen_revision_id]
        WHERE r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
    ) THROW 51018, N'frozen regimen revision blocks alias mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_context_parent_immutable]
ON [kb].[regimen_context]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [regimen_revision_id] FROM inserted
            UNION
            SELECT [regimen_revision_id] FROM deleted
        ) changed
        JOIN [kb].[regimen_revision] r
          ON r.[regimen_revision_id] = changed.[regimen_revision_id]
        WHERE r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
    ) THROW 51019, N'frozen regimen revision blocks context mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_component_parent_immutable]
ON [kb].[regimen_component]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [regimen_revision_id] FROM inserted
            UNION
            SELECT [regimen_revision_id] FROM deleted
        ) changed
        JOIN [kb].[regimen_revision] r
          ON r.[regimen_revision_id] = changed.[regimen_revision_id]
        WHERE r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
    ) THROW 51020, N'frozen regimen revision blocks component mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_component_target_authority]
ON [kb].[regimen_component]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1 FROM inserted i
        WHERE (i.[target_kind] = 'CONCEPT' AND NOT EXISTS (
            SELECT 1 FROM [kb].[drug_concept] d
            WHERE d.[drug_concept_id] = i.[target_id]
        )) OR (i.[target_kind] = 'CLASS' AND NOT EXISTS (
            SELECT 1 FROM [kb].[drug_class] c
            WHERE c.[drug_class_id] = i.[target_id]
        ))
    ) THROW 51025, N'regimen component target authority missing', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_condition_node_target_authority]
ON [kb].[condition_node]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1 FROM inserted i
        WHERE i.[node_kind] = 'LEAF'
          AND i.[criterion_type] = 'combination_requirement'
          AND (
            (i.[target_kind] = 'CONCEPT' AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_concept] d
                WHERE d.[drug_concept_id] = i.[target_id]
            )) OR
            (i.[target_kind] = 'CLASS' AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_class] c
                WHERE c.[drug_class_id] = i.[target_id]
            )) OR
            (i.[target_kind] = 'REGIMEN' AND NOT EXISTS (
                SELECT 1 FROM [kb].[regimen_revision] r
                WHERE r.[logical_regimen_id] = i.[target_id]
            ))
          )
    ) THROW 51026, N'combination condition target authority missing', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_eligibility_revision_target_authority]
ON [kb].[eligibility_rule_revision]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN [kb].[eligibility_branch] b
          ON b.[rule_revision_id] = i.[rule_revision_id]
        JOIN [kb].[condition_node] n
          ON n.[branch_id] = b.[branch_id]
        WHERE i.[lifecycle] IN ('APPROVED','RELEASED')
          AND n.[node_kind] = 'LEAF'
          AND n.[criterion_type] = 'combination_requirement'
          AND (
            n.[target_kind] NOT IN ('CONCEPT','CLASS','REGIMEN')
            OR n.[target_id] IS NULL
            OR (n.[target_kind] = 'CONCEPT' AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_concept] d
                WHERE d.[drug_concept_id] = n.[target_id]
                  AND d.[lifecycle] NOT IN ('REJECTED','RETIRED')
            ))
            OR (n.[target_kind] = 'CLASS' AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_class] c
                WHERE c.[drug_class_id] = n.[target_id]
                  AND c.[lifecycle] IN ('APPROVED','RELEASED')
            ))
            OR (n.[target_kind] = 'REGIMEN' AND NOT EXISTS (
                SELECT 1 FROM [kb].[regimen_revision] r
                WHERE r.[logical_regimen_id] = n.[target_id]
                  AND r.[lifecycle] IN ('APPROVED','RELEASED')
            ))
          )
    ) THROW 51032, N'approved eligibility revision has unresolved combination target authority', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_revision_target_authority]
ON [kb].[regimen_revision]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN [kb].[regimen_component] c
          ON c.[regimen_revision_id] = i.[regimen_revision_id]
        WHERE i.[lifecycle] IN ('APPROVED','RELEASED')
          AND (
            (c.[target_kind] = 'CONCEPT' AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_concept] d
                WHERE d.[drug_concept_id] = c.[target_id]
                  AND d.[lifecycle] NOT IN ('REJECTED','RETIRED')
            ))
            OR (c.[target_kind] = 'CLASS' AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_class] dc
                WHERE dc.[drug_class_id] = c.[target_id]
                  AND dc.[lifecycle] IN ('APPROVED','RELEASED')
            ))
          )
    ) THROW 51033, N'approved regimen revision has unresolved component target authority', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_regimen_schedule_parent_immutable]
ON [kb].[regimen_schedule_component]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [regimen_revision_id] FROM inserted
            UNION
            SELECT [regimen_revision_id] FROM deleted
        ) changed
        JOIN [kb].[regimen_revision] r
          ON r.[regimen_revision_id] = changed.[regimen_revision_id]
        WHERE r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
    ) THROW 51021, N'frozen regimen revision blocks schedule mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_source_fragment_frozen_reference]
ON [kb].[source_fragment]
AFTER UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM deleted d
        WHERE EXISTS (
            SELECT 1
            FROM [kb].[eligibility_rule_revision] r
            WHERE r.[source_fragment_id] = d.[source_fragment_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        ) OR EXISTS (
            SELECT 1
            FROM [kb].[eligibility_branch] b
            JOIN [kb].[eligibility_rule_revision] r
              ON r.[rule_revision_id] = b.[rule_revision_id]
            WHERE b.[source_fragment_id] = d.[source_fragment_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        ) OR EXISTS (
            SELECT 1
            FROM [kb].[condition_node] n
            JOIN [kb].[eligibility_branch] b ON b.[branch_id] = n.[branch_id]
            JOIN [kb].[eligibility_rule_revision] r
              ON r.[rule_revision_id] = b.[rule_revision_id]
            WHERE n.[source_fragment_id] = d.[source_fragment_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        ) OR EXISTS (
            SELECT 1
            FROM [kb].[regimen_component] c
            JOIN [kb].[regimen_revision] r
              ON r.[regimen_revision_id] = c.[regimen_revision_id]
            WHERE c.[source_fragment_id] = d.[source_fragment_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        )
    ) THROW 51022, N'frozen revision blocks source fragment mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_drug_concept_frozen_reference]
ON [kb].[drug_concept]
AFTER UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM deleted d
        WHERE EXISTS (
            SELECT 1
            FROM [kb].[eligibility_rule_revision] r
            WHERE r.[drug_concept_id] = d.[drug_concept_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        ) OR EXISTS (
            SELECT 1
            FROM [kb].[regimen_component] c
            JOIN [kb].[regimen_revision] r
              ON r.[regimen_revision_id] = c.[regimen_revision_id]
            WHERE c.[target_kind] = 'CONCEPT'
              AND c.[target_id] = d.[drug_concept_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        )
    ) THROW 51023, N'frozen revision blocks drug concept mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_drug_class_frozen_reference]
ON [kb].[drug_class]
AFTER UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM deleted d
        WHERE d.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        OR EXISTS (
            SELECT 1
            FROM [kb].[regimen_component] c
            JOIN [kb].[regimen_revision] r
              ON r.[regimen_revision_id] = c.[regimen_revision_id]
            WHERE c.[target_kind] = 'CLASS'
              AND c.[target_id] = d.[drug_class_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        ) OR EXISTS (
            SELECT 1
            FROM [kb].[condition_node] n
            JOIN [kb].[eligibility_branch] b ON b.[branch_id] = n.[branch_id]
            JOIN [kb].[eligibility_rule_revision] r
              ON r.[rule_revision_id] = b.[rule_revision_id]
            WHERE n.[criterion_type] = 'combination_requirement'
              AND n.[target_kind] = 'CLASS'
              AND n.[target_id] = d.[drug_class_id]
              AND r.[lifecycle] IN ('APPROVED','RELEASED','RETIRED')
        )
    ) THROW 51027, N'frozen revision blocks drug class mutation', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_curated_atom_verified_immutable]
ON [kb].[curated_knowledge_atom]
AFTER UPDATE, DELETE
AS
BEGIN
    IF EXISTS (SELECT 1 FROM deleted WHERE [migration_status] = 'VERIFIED')
        THROW 51028, N'verified curated atom is immutable', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_curated_mapping_verified_immutable]
ON [kb].[curated_knowledge_mapping]
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT [atom_id] FROM inserted
            UNION
            SELECT [atom_id] FROM deleted
        ) changed
        JOIN [kb].[curated_knowledge_atom] atom
          ON atom.[atom_id] = changed.[atom_id]
        WHERE atom.[migration_status] = 'VERIFIED'
    ) THROW 51029, N'verified curated mapping is immutable', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_curated_mapping_target_authority]
ON [kb].[curated_knowledge_mapping]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN [kb].[curated_knowledge_atom] atom
          ON atom.[atom_id] = i.[atom_id]
        WHERE atom.[migration_status] = 'VERIFIED'
          AND (
          (
            i.[verification_evidence] IS NULL
            OR LTRIM(RTRIM(i.[verification_evidence])) = N''
          ) OR (
            i.[target_kind] = 'CONDITION'
            AND NOT EXISTS (
                SELECT 1 FROM [kb].[eligibility_rule_revision] r
                WHERE r.[rule_revision_id] = i.[target_id]
            )
            AND NOT EXISTS (
                SELECT 1 FROM [kb].[eligibility_branch] b
                WHERE b.[branch_id] = i.[target_id]
            )
            AND NOT EXISTS (
                SELECT 1 FROM [kb].[condition_node] n
                WHERE n.[node_id] = i.[target_id]
            )
        ) OR (
            i.[target_kind] = 'DICTIONARY'
            AND NOT EXISTS (
                SELECT 1 FROM [kb].[term_dictionary_entry] t
                WHERE t.[term_entry_id] = i.[target_id]
            )
            AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_concept] d
                WHERE d.[drug_concept_id] = i.[target_id]
            )
            AND NOT EXISTS (
                SELECT 1 FROM [kb].[drug_class] c
                WHERE c.[drug_class_id] = i.[target_id]
            )
          ))
    ) THROW 51030, N'curated mapping target authority missing', 1;
END;
GO

CREATE OR ALTER TRIGGER [kb].[tr_curated_atom_target_authority]
ON [kb].[curated_knowledge_atom]
AFTER INSERT, UPDATE
AS
BEGIN
    IF EXISTS (
        SELECT 1
        FROM inserted atom
        WHERE atom.[migration_status] = 'VERIFIED'
          AND (
            (SELECT COUNT_BIG(*)
             FROM [kb].[curated_knowledge_mapping] m
             WHERE m.[atom_id] = atom.[atom_id]) <> 1
            OR EXISTS (
                SELECT 1
                FROM [kb].[curated_knowledge_mapping] m
                WHERE m.[atom_id] = atom.[atom_id]
                  AND (
                    m.[verification_evidence] IS NULL
                    OR LTRIM(RTRIM(m.[verification_evidence])) = N''
                    OR (m.[target_kind] = 'CONDITION'
                        AND NOT EXISTS (
                            SELECT 1 FROM [kb].[eligibility_rule_revision] r
                            WHERE r.[rule_revision_id] = m.[target_id]
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM [kb].[eligibility_branch] b
                            WHERE b.[branch_id] = m.[target_id]
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM [kb].[condition_node] n
                            WHERE n.[node_id] = m.[target_id]
                        ))
                    OR (m.[target_kind] = 'DICTIONARY'
                        AND NOT EXISTS (
                            SELECT 1 FROM [kb].[term_dictionary_entry] t
                            WHERE t.[term_entry_id] = m.[target_id]
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM [kb].[drug_concept] d
                            WHERE d.[drug_concept_id] = m.[target_id]
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM [kb].[drug_class] c
                            WHERE c.[drug_class_id] = m.[target_id]
                        ))
                  )
            )
          )
    ) THROW 51034, N'verified curated atom target authority missing', 1;
END;
GO

/*
 * schema_version 是完整 capability 的完成标记。所有必需触发器必须已创建在预期表上且启用，
 * 才能登记版本并提交；任一 CREATE OR ALTER TRIGGER/核验失败时，整次事务回滚。
 */
IF EXISTS (
    SELECT 1
    FROM (VALUES
        (N'tr_review_event_append_only', N'review_event'),
        (N'tr_release_immutable', N'release_item'),
        (N'tr_eligibility_revision_no_overlap', N'eligibility_rule_revision'),
        (N'tr_regimen_revision_no_overlap', N'regimen_revision'),
        (N'tr_eligibility_revision_immutable', N'eligibility_rule_revision'),
        (N'tr_regimen_revision_immutable', N'regimen_revision'),
        (N'tr_eligibility_branch_parent_immutable', N'eligibility_branch'),
        (N'tr_condition_node_parent_immutable', N'condition_node'),
        (N'tr_regimen_alias_parent_immutable', N'regimen_alias'),
        (N'tr_regimen_context_parent_immutable', N'regimen_context'),
        (N'tr_regimen_component_parent_immutable', N'regimen_component'),
        (N'tr_regimen_component_target_authority', N'regimen_component'),
        (N'tr_condition_node_target_authority', N'condition_node'),
        (N'tr_eligibility_revision_target_authority', N'eligibility_rule_revision'),
        (N'tr_regimen_revision_target_authority', N'regimen_revision'),
        (N'tr_regimen_schedule_parent_immutable', N'regimen_schedule_component'),
        (N'tr_source_fragment_frozen_reference', N'source_fragment'),
        (N'tr_drug_concept_frozen_reference', N'drug_concept'),
        (N'tr_drug_class_frozen_reference', N'drug_class'),
        (N'tr_curated_atom_verified_immutable', N'curated_knowledge_atom'),
        (N'tr_curated_mapping_verified_immutable', N'curated_knowledge_mapping'),
        (N'tr_curated_mapping_target_authority', N'curated_knowledge_mapping'),
        (N'tr_curated_atom_target_authority', N'curated_knowledge_atom')
    ) AS required([trigger_name], [parent_table])
    WHERE NOT EXISTS (
        SELECT 1
        FROM sys.triggers AS installed
        JOIN sys.tables AS parent_table
          ON parent_table.[object_id] = installed.[parent_id]
        JOIN sys.schemas AS parent_schema
          ON parent_schema.[schema_id] = parent_table.[schema_id]
        WHERE installed.[name] = required.[trigger_name]
          AND parent_schema.[name] = N'kb'
          AND parent_table.[name] = required.[parent_table]
          AND installed.[is_disabled] = 0
    )
)
    THROW 51024, N'知识库必需触发器缺失、挂载表错误或未启用', 1;

IF EXISTS (
    SELECT 1
    FROM (VALUES
        (N'vw_eligibility_rule_overview'),
        (N'vw_eligibility_condition_detail'),
        (N'vw_regimen_composition'),
        (N'vw_latest_review_status'),
        (N'vw_import_reconciliation'),
        (N'vw_release_overview')
    ) AS required([view_name])
    WHERE NOT EXISTS (
        SELECT 1
        FROM sys.views AS installed
        JOIN sys.schemas AS view_schema
          ON view_schema.[schema_id] = installed.[schema_id]
        WHERE installed.[name] = required.[view_name]
          AND view_schema.[name] = N'kb'
    )
)
    THROW 51031, N'知识库必需人工审核视图缺失', 1;

IF NOT EXISTS (SELECT 1 FROM [kb_meta].[schema_version] WHERE [version_no] = 1)
    INSERT INTO [kb_meta].[schema_version]([version_no], [migration_checksum])
    VALUES (1, 'sha256:managed-by-create_oncology_kb_schema.sql');

COMMIT TRANSACTION;
GO
