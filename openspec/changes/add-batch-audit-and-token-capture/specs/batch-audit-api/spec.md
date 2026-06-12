## ADDED Requirements

### Requirement: Patient-centric batch audit SSE endpoint

The Web API SHALL expose `GET /api/audit/run-batch?patient_id=<id>&rules=<R1,R2,...>&concurrency=<n>` that audits one patient against an explicit list of rules and streams results over Server-Sent Events. It MUST reuse the shared batch runner (the same parallel, failure-isolated logic that backs the `audit-patient` CLI), emit one `result` event per rule as it completes (in the same shape as the single-rule `/api/audit/run` result event), and emit a terminal `done` event carrying batch aggregates. The explicit rule list bypasses the router (consistent with `audit-patient --rules`).

#### Scenario: Batch run streams one result per rule

- **WHEN** a client requests `GET /api/audit/run-batch?patient_id=J66252&rules=R191,R161,R141`
- **THEN** the endpoint streams three `result` events (one per rule, as each completes), each carrying the same fields as a single-rule audit result including `completion_tokens`, followed by a `done` event

#### Scenario: Done event carries batch aggregates

- **WHEN** a batch of N rules completes with mixed verdicts
- **THEN** the terminal `done` event includes the rule count, per-verdict counts, the count of failed rules, and the summed `completion_tokens` across the batch

#### Scenario: Failure isolation does not abort the batch

- **WHEN** one rule's audit raises an LLM error mid-batch
- **THEN** that rule emits a `fail` event while the remaining rules continue to completion, and the `done` event reflects the failed count

#### Scenario: Invalid rule list is rejected

- **WHEN** `rules` is empty or contains an unknown `rule_id`
- **THEN** the endpoint returns a 400/404 error and no audits run

#### Scenario: Single-rule endpoint is unchanged

- **WHEN** an existing client calls the single-rule `GET /api/audit/run?rule_id=R191&patient_id=J66252`
- **THEN** its behavior and event stream are unchanged except that the `result` event now also carries `completion_tokens`
