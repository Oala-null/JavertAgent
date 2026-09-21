## ADDED Requirements

### Requirement: Read only batch and workbench summaries
The tool SHALL produce both a selected LIS batch summary and latest-per-patient-rule workbench totals, with a fixed result ID watermark, without database writes or changing active code.

#### Scenario: A patient rule is rerun in the new batch
- **WHEN** older and newer results exist for the same patient and rule
- **THEN** the workbench counts only the latest result, the batch counts its latest result, and the report states the scopes overlap

### Requirement: Honest money and timing measures
The tool MUST distinguish unrecorded violation amount from current net settlement fees of affected admissions and distinguish cumulative rule time from patient processing and calendar span.

#### Scenario: There is a violation without a structured amount
- **WHEN** a VIOLATION row has no structured violation amount
- **THEN** violation amount is unavailable, never copied from total fees or inferred from text, and current fee coverage is shown separately

#### Scenario: One admission fee query fails and another has refunds
- **WHEN** current fees are collected for both admissions
- **THEN** failed fees remain unknown, refunds reduce net fees, and successes and failures are counted separately

### Requirement: Private reproducible manual export
The tool SHALL support manual execution during an active batch, produce TXT/CSV/JSON aggregate artifacts with restrictive permissions, and not export patient IDs or raw medical evidence.

#### Scenario: Batch still running before end of day
- **WHEN** user invokes eda.sh
- **THEN** report states the observation times, running and pending counts, and includes only persisted audit results without claiming all candidates completed

### Requirement: Rule and review provenance
The tool SHALL categorize from stored rule snapshots first and label fallback metadata; machine violations and unanimous or conflicting expert reviews SHALL remain separate.

#### Scenario: Experts disagree on a machine violation
- **WHEN** latest reviews contain multiple verdicts
- **THEN** the row is counted as a review conflict rather than unanimous confirmation
