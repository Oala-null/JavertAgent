## ADDED Requirements

### Requirement: Source provenance distinguishes guideline, insurance, and future labels

Every knowledge rule SHALL reference a versioned source document and source fragment with source type, title, document version/year, retrieval date, content checksum, page or stable anchor, and original text. Current insurance clauses MUST use `INSURANCE_PAYMENT`; current clinical-application-guideline indications MUST use `GUIDELINE_INDICATION`; `NMPA_LABEL` MUST remain reserved until a product-specific legal label is actually ingested and reviewed.

#### Scenario: Guideline indication is published

- **WHEN** an indication comes from the 2025 clinical application guideline
- **THEN** its source type is `GUIDELINE_INDICATION`, its document year remains 2025, and its evidence displays the guideline title
- **AND** no field or UI labels it as a legal product label

#### Scenario: Insurance clause cites use according to label

- **WHEN** an insurance restriction says to use according to the product label but no legal label source exists
- **THEN** the insurance source text is preserved and `label_source_missing=true` is recorded
- **AND** the system MUST NOT synthesize an `NMPA_LABEL` rule from the guideline

### Requirement: Effective dates are editable only on mutable revisions

Rule and regimen revisions SHALL store inclusive `effective_from` and `effective_to`, an effective-date basis, optional override reason, date-review comment, and historical-application policy. Draft or changes-requested revisions MAY be edited manually. Approved or released revisions MUST be immutable; any later change MUST create a new revision linked through `supersedes_revision_id`.

#### Scenario: Expert changes a draft effective date

- **WHEN** an expert changes either default date in a draft workbook
- **THEN** the importer requires `effective_date_basis=EXPERT_OVERRIDE` and a non-empty override reason
- **AND** the change is stored in a new or mutable draft revision with a review event

#### Scenario: Released date is edited

- **WHEN** an import attempts to modify the dates of an approved or released revision in place
- **THEN** the import is rejected with an immutable-revision error
- **AND** the operator is directed to clone a superseding draft revision

### Requirement: Current source snapshot uses an explicit assumed window

All initial rules derived from the current files SHALL default to `effective_from=2026-01-01`, `effective_to=2027-12-31`, `effective_date_basis=CURRENT_FILE_ASSUMPTION`, and `historical_application_policy=APPLY_CURRENT_RELEASE_WITH_WARNING`, unless an expert-approved override creates a new revision. The source document year MUST remain separate from this policy window.

#### Scenario: Initial candidate is created from the 2025 guideline

- **WHEN** the initial guideline candidate is generated without an expert date override
- **THEN** it records source document year 2025 and effective window 2026-01-01 through 2027-12-31
- **AND** the two dates MUST NOT be inferred to be the same concept

#### Scenario: Invalid date interval is imported

- **WHEN** `effective_from` is later than `effective_to`
- **THEN** validation fails before authoring materialization

### Requirement: Effective versions do not overlap for one logical rule

Approved or release-candidate revisions of the same logical rule MUST NOT have overlapping inclusive effective intervals. Alternative indication branches within one rule revision MAY share the rule interval. Overlap detection MUST run on local validation, server materialization, and release creation.

#### Scenario: Superseding revision overlaps its predecessor

- **WHEN** a new revision for the same logical rule starts before the predecessor's effective end without retiring or shortening the predecessor through a valid supersession plan
- **THEN** release validation fails and reports both revision IDs and intervals

#### Scenario: Alternative branches share dates

- **WHEN** two OR branches belong to the same rule revision and inherit the same effective interval
- **THEN** they are accepted and MUST NOT be reported as competing rule versions

### Requirement: Knowledge review and release lifecycle is auditable

Knowledge revisions SHALL follow controlled states including draft, in-review, changes-requested, approved, released, rejected, and retired. Review events MUST be append-only. A release MUST contain only approved immutable revisions, MUST identify its creator and approver, and MUST be published by an operator distinct from the domain reviewer who approved the content.

#### Scenario: Uploaded workbook is valid

- **WHEN** a workbook passes structural validation and materialization
- **THEN** its revisions remain draft or in-review
- **AND** upload alone MUST NOT approve, release, publish, or deploy them

#### Scenario: Release contains a needs-review item

- **WHEN** a release candidate references any unsupported, unable-to-determine, rejected, or non-approved revision
- **THEN** release creation fails and identifies every blocking item

### Requirement: Release compilation is deterministic and complete

The publisher SHALL compile approved release revisions into canonical JSON assets with schema version, release ID, source snapshot checksum, entry revision IDs, effective dates, review status, and asset checksum. Repeated compilation of the same release MUST be byte-identical. The release report MUST partition all current source items and list additions, modifications, retirements, source changes, and date changes relative to the previous release. Every curated oncology knowledge atom used by or relevant to the release MUST be `VERIFIED`; non-oncology atoms may remain pending only as an explicit drafting backlog and MUST NOT be reported as retired or fully migrated.

#### Scenario: Same release is compiled twice

- **WHEN** the same immutable release is compiled twice in clean environments
- **THEN** every generated JSON asset and checksum is byte-identical

#### Scenario: Source item is missing from the partition

- **WHEN** an insurance restriction or guideline indication is neither included in the release nor present as an explicit pending/rejected/unsupported item
- **THEN** release validation fails with a coverage-gap error

#### Scenario: Curated oncology knowledge is not verified

- **WHEN** a release candidate contains an oncology rule whose curated knowledge coverage has a discovered or mapped-but-unverified atom
- **THEN** release validation fails with the source rule, atom, proposed target, and missing verification evidence

#### Scenario: Non-oncology migration remains pending

- **WHEN** the oncology release is complete but a non-oncology curated rule is still in the preservation backlog
- **THEN** the release report keeps that rule explicitly drafting/migration-pending without blocking unrelated oncology publication
- **AND** it MUST NOT describe that rule's knowledge as abandoned, retired, or fully covered

### Requirement: Release rollback preserves history

Rollback SHALL switch the deployed or active release pointer to a previously published release without deleting later revisions, review events, import-batch records, or historical audit references. Old audit rows MUST continue to deserialize using their recorded release and MUST NOT be silently re-evaluated.

#### Scenario: Published release is rolled back

- **WHEN** a newly published release causes a production regression
- **THEN** operations can restore the prior JSON assets or active release pointer
- **AND** all knowledge and audit history remains available for investigation
