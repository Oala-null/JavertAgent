## ADDED Requirements

### Requirement: Patient data is accessed through a loader interface

The system SHALL expose a `DataLoader` abstract interface with at minimum the methods `get_notes(patient_id: str) -> pd.DataFrame` and `get_fees(patient_id: str) -> pd.DataFrame`. Concrete implementations MUST return DataFrames whose columns conform to the canonical schemas defined in design.md (notes: `住院号 / 事件时间 / 阶段 / 子阶段 / 内容 / 来源文件`; fees: the 28 columns of `shi_fee.csv` led by `bah / fee_ocur_time / cnt / pric / det_item_fee_sumamt / medins_list_name / ...`). All audit-engine code MUST consume data exclusively through this interface — direct `pd.read_csv` calls in audit code are forbidden.

#### Scenario: Loader returns notes for a known patient

- **WHEN** an audit engine calls `loader.get_notes("J66252")`
- **THEN** the loader returns a non-empty DataFrame containing only rows where `住院号 == "J66252"` with the canonical columns

#### Scenario: Loader returns empty DataFrame for unknown patient

- **WHEN** an audit engine calls `loader.get_notes("Z99999")` (patient ID not present in source)
- **THEN** the loader returns an empty DataFrame with the canonical columns (no exception raised)

### Requirement: Bundled CSV implementation

The system SHALL ship a `CsvLoader` implementation that lazily loads `data/case_notes.csv` and `data/shi_fee.csv` on first call and caches them in memory for the process lifetime. The first call per file MUST log the load duration and row count.

#### Scenario: First-call load is logged

- **WHEN** `CsvLoader.get_notes("J66252")` is called for the first time in a process
- **THEN** `data/case_notes.csv` is read once into a cached DataFrame and a log line records "loaded 357418 rows in N.N s"

#### Scenario: Subsequent calls reuse cache

- **WHEN** `CsvLoader.get_notes` is called a second time (any patient)
- **THEN** no additional file I/O occurs and the lookup completes in under 100ms on the canonical dataset

### Requirement: Snapshot copy from zadig_agent

The system SHALL ship an `init` sub-operation that copies `case_notes.csv` and `shi_fee.csv` from a configurable upstream path (default `../zadig_agent/data/`) into `Javert/data/`. The copy MUST verify file size and row count and write a `data/_snapshot.json` recording source path, copy timestamp, file sizes, and row counts.

#### Scenario: Initial snapshot succeeds

- **WHEN** operator runs `javert init` with default upstream path and the source files exist
- **THEN** both csv files appear under `data/`, `data/_snapshot.json` is written with the four fields, and the log confirms row counts match between source and destination

#### Scenario: Snapshot refuses to silently overwrite

- **WHEN** operator runs `javert init` and `data/case_notes.csv` already exists
- **THEN** the system requires `--refresh-data` flag to proceed, otherwise exits with message "snapshot exists; use --refresh-data to replace"

### Requirement: Patient roster file

The system SHALL ship `data/pilot_patients.txt` listing the 50 patient IDs to be used in `javert run --pilot`. The file format MUST be one patient ID per line, lines starting with `#` treated as comments, blank lines ignored.

#### Scenario: Loading the pilot roster

- **WHEN** the system loads `data/pilot_patients.txt`
- **THEN** the loader returns a list of exactly 50 unique patient IDs from the canonical thyroid-cancer subset

#### Scenario: Comments and blanks are ignored

- **WHEN** `pilot_patients.txt` contains `# Cohort A` followed by a blank line and then 50 patient IDs
- **THEN** the loader returns the 50 IDs, ignoring the comment and blank line
