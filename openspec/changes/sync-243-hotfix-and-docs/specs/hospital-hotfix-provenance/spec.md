## ADDED Requirements

### Requirement: Preserve delivered hospital runtime bytes
The gnome-243 maintenance tree SHALL recover the shipped LIS files and dependencies by verified SHA256 without changing the FP8 branch or immutable delivery archives.

#### Scenario: Recovery completes
- **WHEN** the ten runtime files and four prerequisites are compared with the delivered manifest
- **THEN** all hashes match and unrelated user files and the FP8 branch ref remain unchanged

### Requirement: Read only onsite verification
The checker SHALL report missing, changed, unreadable and symlinked files separately, reject unsafe manifest paths, and distinguish disk checks from live process evidence.

#### Scenario: Remote access is unavailable
- **WHEN** local code matches the delivery but the hospital cannot be reached
- **THEN** onsite state remains UNKNOWN and the report provides a runnable read-only checker rather than claiming synchronization

#### Scenario: Baseline stamp matches but a file differs
- **WHEN** DEPLOY_COMMIT equals8645a2d but a controlled file hash differs
- **THEN** disk verification fails with the affected path and does not print file content or change anything

### Requirement: Consistent operational documentation
Current documentation SHALL identify the hospital maintenance branch, DETAIL/STFSJ source, LIS medical-record path, batch resume and manual EDA commands, with historical deployment instructions clearly separated.

#### Scenario: Reader follows current runbook
- **WHEN** a maintainer needs to inspect an installed v1 hotfix
- **THEN** they use the readonly checker and current background helpers, not an old demo tar overwrite, FS query or broad process kill
