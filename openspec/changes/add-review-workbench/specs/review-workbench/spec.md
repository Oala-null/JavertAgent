## ADDED Requirements

### Requirement: FastAPI app factory with mssql toggle

The system SHALL provide `create_app(with_mssql: bool = True) -> FastAPI` in `src/javert/web/app.py` that constructs the workbench application. When `with_mssql=True` (default), the app wires `SqlServerStore` via dependency injection and exposes all routes. When `with_mssql=False`, the app omits review/auth/dashboard/export/sse routes (returning 503 on those paths) and serves only sqlite-backed read-only views — for Mac dev mode without 142 access.

#### Scenario: Production mode wires SQL Server

- **WHEN** the operator runs `javert web --with-mssql --port 8090` on Linux 192.168.31.62
- **THEN** the app starts, `SqlServerStore.healthcheck()` returns True, all routes (`/login`, `/register`, `/workbench`, `/review`, `/dashboard`, `/export`, `/sse/reviews`) are mounted and respond 2xx for valid requests

#### Scenario: Dev mode skips SQL Server routes

- **WHEN** the operator runs `javert web --no-mssql --port 8090` on Mac with no 142 connectivity
- **THEN** the app starts without 142 connection attempts; `GET /login` returns 503 "工作台需要 SQL Server 连接 — 联系运维"; `GET /` redirects to a dev-mode landing page explaining the limitation; existing `/rules` and `/dry-run` endpoints (from prior `web` command) still work against sqlite

#### Scenario: SQL Server unreachable in production mode degrades gracefully

- **WHEN** the operator starts the app with `--with-mssql` but 142 is unreachable at startup
- **THEN** the app starts (does NOT crash); healthcheck logs WARN; all `/review`, `/dashboard`, `/export` routes return `503 Service Unavailable` with body `{"error": "数据库不可用, 请联系管理员"}`; `/login` and `/register` ALSO return 503 (cannot authenticate without users table); a `/healthz` route returns `{"status":"degraded", "mssql": false}` for monitoring

### Requirement: User registration with bcrypt + auto-login

The route `POST /register` SHALL accept `username` and `password` form fields, validate them, hash the password with bcrypt, insert a new user, set a session cookie, and redirect to `/workbench`. No invitation code, no email verification, no admin approval is required (per design.md D4).

#### Scenario: Valid registration creates user and logs in

- **WHEN** a browser POSTs `username=alice&password=correct-horse-battery-staple` to `/register`
- **THEN** the system calls `bcrypt.hash(password)` with cost factor 12, calls `SqlServerStore.create_user("alice", hash)`, sets a signed session cookie `javert_session={user_id: <new_id>, exp: <now+30d>}`, calls `update_last_login`, and returns HTTP 302 to `/workbench`; `javert_audit_logs` has a `register` action row

#### Scenario: Duplicate username returns 409

- **WHEN** a browser POSTs `username=alice&password=...` and "alice" already exists
- **THEN** the system catches `DuplicateUsernameError` and returns 409 with body containing `用户名已存在`; no session cookie is set; `javert_audit_logs` records `action=register_fail`, `target_id=alice`, `payload={"reason":"duplicate_username"}`

#### Scenario: Weak password is accepted (pilot)

- **WHEN** a browser POSTs `password=1234` (4 chars, weak)
- **THEN** registration succeeds (pilot phase does NOT enforce strength rules); the audit_logs entry records the password length as part of payload for later analysis; a future change can add strength rules

#### Scenario: Username validation rules

- **WHEN** a browser POSTs `username=""` (empty) OR `username` longer than 64 chars OR containing non-printable characters
- **THEN** the system returns 400 with body describing the validation failure; no user row is created

### Requirement: Login with session cookie

The route `POST /login` SHALL accept `username` and `password`, validate them against `javert_users`, and on success set a signed session cookie and redirect to `/workbench`. Failure cases MUST log to `javert_audit_logs` per design.md D3.

#### Scenario: Correct credentials log in successfully

- **WHEN** a browser POSTs `username=alice&password=...` with credentials matching the stored bcrypt hash
- **THEN** `bcrypt.verify` returns True; `SqlServerStore.update_last_login(user_id)` is called; a signed session cookie is set; response is 302 → `/workbench`; `javert_audit_logs` records `action=login`

#### Scenario: Wrong password returns 401

- **WHEN** a browser POSTs `password=wrong` and `bcrypt.verify` returns False
- **THEN** the response is 401 with body `用户名或密码错误`; no session cookie is set; `javert_audit_logs` records `action=login_fail, user_id=NULL, target_id="alice", payload={"reason":"bad_password"}`

#### Scenario: Unknown username returns 401 same body

- **WHEN** a browser POSTs `username=nobody&password=...` for a user that doesn't exist
- **THEN** the response is 401 with body identical to the wrong-password case (to prevent user enumeration); `javert_audit_logs` records `action=login_fail, target_id="nobody", payload={"reason":"no_such_user"}`

#### Scenario: Rate limiting on login

- **WHEN** the same IP submits 6+ POST /login requests within 60 seconds
- **THEN** requests 6+ are rejected with HTTP 429 (rate limited via `slowapi`); rate limit applies independently of success/failure

### Requirement: Session middleware enforces auth

The system SHALL register a session middleware that, for any non-public route (`/workbench/*`, `/review`, `/dashboard`, `/export`, `/sse/reviews`), validates the `javert_session` cookie and rejects requests without a valid session by redirecting to `/login`.

#### Scenario: Protected route without cookie redirects to /login

- **WHEN** a browser without `javert_session` cookie GETs `/workbench`
- **THEN** the response is 302 to `/login?next=/workbench` (so post-login redirects back)

#### Scenario: Expired session cookie redirects to /login

- **WHEN** a browser presents `javert_session={user_id:5, exp: <yesterday>}` (expired)
- **THEN** the middleware detects expiration via `itsdangerous` and clears the cookie + redirects to `/login`

#### Scenario: Tampered cookie is rejected

- **WHEN** a browser presents a `javert_session` cookie whose signature does not validate (modified payload, wrong secret)
- **THEN** the middleware rejects, clears the cookie, and redirects to `/login`; `javert_audit_logs` records `action=session_invalid` with the IP and user_agent

#### Scenario: Public routes bypass middleware

- **WHEN** a browser without any cookie GETs `/login`, `/register`, `/healthz`, `/static/*`
- **THEN** the middleware does not intervene; these routes are served normally

### Requirement: Workbench main view

The route `GET /workbench` SHALL render the main workbench page using Jinja2: left sidebar showing all 50 patients (filtered by the current `?filter=` param, default `v_and_i`), right panel empty (waiting for patient selection). The page MUST include the filter dropdown, the logged-in user's name, and a logout link.

#### Scenario: Default V+I filter shows patients with V or I

- **WHEN** a logged-in user GETs `/workbench` with no `?filter=` param
- **THEN** the sidebar lists patients sorted by v_count DESC; patient cards show counts like `J66252  18V 0I` for the V+I filter; J40485 (0V 0I 17C) is hidden in V+I view

#### Scenario: filter=all shows everyone including all-clean patients

- **WHEN** a user GETs `/workbench?filter=all`
- **THEN** all 50 patients are listed, including J40485 (0V 0I 17C); the URL query is preserved in subsequent navigation

#### Scenario: Sidebar shows per-patient review progress

- **WHEN** the user has reviewed 5 of the 18 V violations for J66252 and the page renders
- **THEN** the J66252 sidebar card shows progress text like "已审 5/18" alongside the count badges; progress is computed from `javert_vio_review WHERE is_latest=1 AND user_id=<current>`

#### Scenario: Filter dropdown changes URL and updates sidebar

- **WHEN** the user changes the filter from `v_and_i` to `all` via the top dropdown
- **THEN** the page navigates to `/workbench?filter=all` (client-side simple form action, no SPA); the filter selection is also saved to a `javert_filter` cookie for next session

### Requirement: Patient detail view

The route `GET /workbench/{patient_id}` SHALL render the patient detail panel: header (patient_id, total counts), rule cards for each violation in the current filter, each card containing javert verdict + reasoning + evidence + the expert review form (or read-only display if already reviewed).

#### Scenario: Rule card shows javert verdict and reasoning

- **WHEN** the user GETs `/workbench/J66252?filter=v_and_i`
- **THEN** the page renders 18 rule cards (one per V or I run for J66252); each card includes `<div class="javert-verdict V|I" data-run-id="...">` with verdict badge (red/yellow), confidence number, reasoning paragraph, and a `<details>` for evidence

#### Scenario: Card has review form when current user has not reviewed

- **WHEN** the current user has no `javert_vio_review` row with is_latest=1 for this run_id
- **THEN** the card embeds a form: 3 radio buttons `认同(V) / 改判不明(I) / 驳回(C)`, a `<textarea name="comment">`, and a `<button type="submit">提交</button>`; form action is `POST /review` with hidden field `run_id`

#### Scenario: Card shows read-only review when current user already reviewed

- **WHEN** the current user has an `is_latest=1` review for this run_id (verdict=V, comment="符合规则")
- **THEN** the card shows the review in a styled box (e.g. green-bordered if V) with text "您的审核: 认同 — 符合规则 · 2分钟前"; a small "修改" button reveals the form populated with previous values for re-submission (triggering the insert-only update path)

#### Scenario: Other reviewers' opinions are shown below

- **WHEN** users alice and bob have submitted reviews for this run_id (alice=V, bob=C)
- **THEN** the card has a "其他专家审核" section listing both: "alice: 认同 - <comment first 80 chars>... [展开]" and "bob: 驳回 - ..."; clicking [展开] reveals full comment via simple JS toggle

#### Scenario: 404 for non-existent patient_id

- **WHEN** a user GETs `/workbench/INVALID`
- **THEN** the response is 404 "未找到患者 INVALID"; no exception leaks

### Requirement: Review submission

The route `POST /review` SHALL accept JSON body `{run_id, verdict, comment}` from authenticated users, call `SqlServerStore.submit_review()`, and respond with the new review record + SSE-broadcast notification to all connected clients.

#### Scenario: Valid review is persisted and broadcast

- **WHEN** a user POSTs `{run_id: "abc123", verdict: "V", comment: "符合规则"}` and is authenticated
- **THEN** the system calls `submit_review(run_id="abc123", user_id=5, verdict="V", comment="符合规则", ip=..., user_agent=...)` which performs the transaction; on success returns 201 with body `{id, run_id, user_id, verdict, created_at}`; the SSE EventBus pushes `event: review_submitted\ndata: {"run_id":"abc123","reviewer_username":"alice","verdict":"V"}` to all subscribed clients

#### Scenario: Invalid verdict is rejected

- **WHEN** a user POSTs `{verdict: "X"}` (not in V/I/C set)
- **THEN** the response is 422 with pydantic validation error; no DB write occurs

#### Scenario: Re-submission for same (run_id, user) updates is_latest

- **WHEN** the user submits a second review for run_id="abc123" (e.g. changed mind from V to C)
- **THEN** the system performs the transactional update: old row's is_latest=0, new row is_latest=1, audit_logs records action=review_update with `previous_verdict: "V"` in payload; the SSE broadcast carries the new verdict

#### Scenario: Missing run_id returns 400

- **WHEN** a user POSTs `{verdict: "V", comment: "..."}` with no run_id
- **THEN** the response is 400 "Missing run_id"

#### Scenario: run_id not in javert_audit_runs returns 404

- **WHEN** a user POSTs `{run_id: "doesnotexist", verdict: "V", ...}` and that run_id has no row
- **THEN** the response is 404 "未找到审计记录"; no review is inserted

### Requirement: Raw patient data lookup

The route `GET /api/patient/{patient_id}/raw` SHALL return the original fees + case notes for the patient, formatted as JSON, by reading from the local `data/shi_fee.csv` and `data/case_notes.csv` files (NOT from 142). Used by the workbench UI to let experts click "查看原始病历" on a violation card and inspect underlying data.

#### Scenario: Returns structured fee + notes payload

- **WHEN** an authenticated user GETs `/api/patient/J66252/raw`
- **THEN** the response is 200 JSON with shape `{patient_id, main_diagnosis, fees: [...], notes: [...]}` where `fees` is the list of fee rows from `data/shi_fee.csv` matching this patient_id (each row a dict with `medins_list_name, spec, quantity, unit_price, total, charge_type`), and `notes` is the list of case_notes rows (each dict with `section, content`)

#### Scenario: Patient not in CSVs returns 404

- **WHEN** a user GETs `/api/patient/UNKNOWN/raw`
- **THEN** the response is 404 "未找到患者原始数据"

#### Scenario: Reuses clerk_report rendering logic

- **WHEN** the system renders raw data, it MUST reuse helper functions from `scripts/build_clerk_report.py` (e.g. fee classification by `medins_chrgitm_type` col 31) to ensure consistency with the existing v0.4 病案资料员 HTML output

### Requirement: SSE broadcast endpoint

The route `GET /sse/reviews` SHALL maintain a long-lived `text/event-stream` connection that pushes **two event types** to the client: `review_submitted` (expert reviews) and `new_audit_run` (Javert audits writing new rows to `javert_audit_runs`). Implementation uses `sse-starlette` and an asyncio Queue per client. The endpoint name `/sse/reviews` is retained for backwards-naming-compatibility even though it now carries both event types.

#### Scenario: Client connects and receives heartbeat

- **WHEN** a browser opens `EventSource("/sse/reviews")` with valid session
- **THEN** the server returns 200 with `Content-Type: text/event-stream`; every 30 seconds the server sends `event: heartbeat\ndata: {"ts": "..."}` to keep the connection alive through HTTP proxies

#### Scenario: Review submission triggers broadcast to all connected clients

- **WHEN** user alice submits a review and 5 clients (including alice's) are subscribed to `/sse/reviews`
- **THEN** all 5 clients receive `event: review_submitted\ndata: {"run_id":"abc","reviewer_username":"alice","verdict":"V"}` within 500ms; the event payload does NOT include the comment full text (only verdict + reviewer name + run_id)

#### Scenario: Unauthenticated SSE connection is rejected

- **WHEN** a request without valid session GETs `/sse/reviews`
- **THEN** the response is 302 redirect to `/login` (EventSource automatically fails on non-200 with most clients)

#### Scenario: Disconnected client is cleaned up

- **WHEN** a browser closes the tab or loses network for >60 seconds
- **THEN** the server detects the broken pipe on next heartbeat write, removes that client from the broadcast set, and closes the queue; no memory leak

### Requirement: Audit watcher background task pushes new_audit_run

The system SHALL run a background `asyncio` task (started on app startup) that polls `javert_audit_runs` every 1 second for rows with `created_at > last_seen`, and publishes a `new_audit_run` event to the EventBus for each new row. This is the IPC mechanism connecting the CLI `audit-patient` process to the web SSE clients (see design.md D13).

#### Scenario: Watcher task starts on app startup

- **WHEN** the FastAPI app starts (lifespan event `startup`) with `with_mssql=True`
- **THEN** the system queries `SELECT MAX(created_at) FROM javert_audit_runs` for the initial `last_seen` timestamp, then schedules `audit_watcher_task()` via `asyncio.create_task()`; the task runs in the same event loop as request handlers; the task is cancelled cleanly on `shutdown` event

#### Scenario: New row triggers SSE broadcast within 1.5 seconds

- **WHEN** an external CLI process (`javert audit-patient J88888`) writes a new row to `javert_audit_runs` with `created_at = T` and a web client is subscribed to `/sse/reviews`
- **THEN** within `T + 1.5s` the client receives `event: new_audit_run\ndata: {"run_id":"...", "patient_id":"J88888", "rule_id":"R191", "verdict":"VIOLATION", "is_new_patient":true}`; payload does NOT include reasoning or evidence (those are fetched on-demand when the user clicks the new card)

#### Scenario: Multiple new rows are pushed in order

- **WHEN** the audit-patient run produces 30 new rows in 60 seconds and a client is subscribed throughout
- **THEN** the client receives 30 `new_audit_run` events ordered by `created_at` ascending; no events are dropped; no events are duplicated even if the watcher polls 60 times

#### Scenario: is_new_patient flag distinguishes new vs existing patients

- **WHEN** the watcher publishes a row for `patient_id=J66252` which already has 110 existing rows in `javert_audit_runs`
- **THEN** the event payload has `is_new_patient: false`; the frontend updates the existing sidebar card's badges instead of inserting a new card

#### Scenario: Restart does not re-broadcast old rows

- **WHEN** the web app is stopped and restarted
- **THEN** on restart, `last_seen` is initialized to the current `MAX(created_at)`; clients connecting after restart do NOT receive `new_audit_run` events for any rows that existed before the restart; only rows inserted AFTER restart are broadcast

#### Scenario: Watcher survives transient DB error

- **WHEN** the polling query fails (e.g. SQL Server briefly disconnects)
- **THEN** the watcher catches the exception, logs WARN with the error message, sleeps 5 seconds (longer than normal poll interval), then retries on the next iteration; `last_seen` is preserved so no rows are missed; the task does NOT crash the app

#### Scenario: Watcher detects rows pushed via sync-to-mssql --pending-only

- **WHEN** an audit row failed double-write at audit time (sqlite has it, 142 doesn't, `_sync_pending=1`), then `javert sync-to-mssql --pending-only` is run later, pushing the row to 142 with `created_at` reflecting the ORIGINAL audit time (not the sync time)
- **THEN** if the original `created_at` is older than the watcher's `last_seen`, the row is NOT broadcast (already past the watcher's frontier); the frontend will see this row on next page refresh or on welcome banner deltas instead — this is a known limitation, acceptable since sync-to-mssql is the catch-up path not the realtime path

### Requirement: Frontend handles new_audit_run events

The workbench `app.js` SHALL register an event listener for `new_audit_run` and update the UI accordingly: insert a new patient card in sidebar (if `is_new_patient=true`) OR update existing card badges (if `is_new_patient=false`), AND show a brief toast notification.

#### Scenario: New patient card inserted into sidebar

- **WHEN** a client subscribed to `/sse/reviews` receives `new_audit_run` with `is_new_patient: true, patient_id: "J88888"`
- **THEN** the JS prepends a new `<div class="patient-card" data-patient-id="J88888">` to the sidebar (so it appears at the top); the card shows preliminary counts `1V 0I 0C` (based on this event's verdict); subsequent events for J88888 update the same card via the existing-patient code path

#### Scenario: Existing patient card badges updated

- **WHEN** a client receives `new_audit_run` with `is_new_patient: false, patient_id: "J66252", verdict: "VIOLATION"`
- **THEN** the JS finds `[data-patient-id="J66252"]` in the sidebar and increments its V badge by 1 (e.g. "18V" → "19V"); the "已审 5/18" denominator also updates to "5/19"; card sort order may re-shuffle if v_count ranking changes

#### Scenario: Toast notification

- **WHEN** a `new_audit_run` event is received
- **THEN** a small toast appears at top-right (CSS-positioned, no library) with text `新增审计: J88888 R191 → V` and auto-dismisses after 4 seconds; multiple rapid events stack vertically (max 5 visible, older ones FIFO out)

#### Scenario: Sidebar filter affects which events trigger UI update

- **WHEN** the workbench has `filter=v_only` active and a `new_audit_run` event arrives with `verdict: "CLEAN"`
- **THEN** the badge does NOT update (the C count isn't shown in v_only mode); the toast still appears so user knows audit happened; sidebar card may or may not appear depending on whether patient now has at least one V

#### Scenario: Subscribed but not currently on /workbench page

- **WHEN** a user is on `/workbench/J66252` (patient detail subpage) when a `new_audit_run` event arrives for J88888 (different patient)
- **THEN** the event is still received (SSE is page-global) but the patient_detail page does not render a new patient card (no sidebar on subpages); the toast still appears; on navigating back to `/workbench`, the page query re-runs and the new patient appears naturally

### Requirement: Dashboard

The route `GET /dashboard` SHALL render an aggregated view of review progress and rule-level statistics. Sections: overall progress (X/Y reviewed), per-reviewer leaderboard, per-rule violation distribution, agreement rate between javert and experts.

#### Scenario: Overall progress card

- **WHEN** a logged-in user GETs `/dashboard` and the database has 395 V+I runs with 187 having at least one is_latest review
- **THEN** the page renders a progress card showing "总进度: 187/395 (47.3%)" with a progress bar

#### Scenario: Per-reviewer leaderboard

- **WHEN** the dashboard renders and reviewers `alice`, `bob`, `carol` have 92, 65, 30 latest reviews respectively
- **THEN** a leaderboard table shows them in descending order with name + count + percentage; current user's row is highlighted

#### Scenario: Per-rule violation table

- **WHEN** the dashboard renders
- **THEN** a table lists each rule_id that has any V or I judgments by javert, with columns: rule_id, javert_v_count, javert_i_count, expert_agreed (when expert review_verdict == javert verdict), expert_overturned (when expert flipped V→C), expert_pending (no review yet); sortable by any column

#### Scenario: Javert vs expert agreement rate

- **WHEN** the dashboard renders and 80 of 100 V verdicts from javert have at least one expert who agreed (review_verdict="V") in latest reviews
- **THEN** a summary card shows "Javert V → 专家 V: 80% (80/100)" with a colored bar; same for I and C

### Requirement: Excel export

The route `GET /export` SHALL stream an Excel workbook containing the review results, with optional filters and history inclusion.

#### Scenario: Default export of V+I latest reviews

- **WHEN** a logged-in user GETs `/export?format=xlsx&scope=v_and_i`
- **THEN** the response streams an `.xlsx` file (Content-Disposition: attachment; filename="javert_reviews_<YYYYMMDD_HHMMSS>.xlsx") generated by openpyxl with sheets: Sheet1=审核结果 (one row per latest review for V+I runs), Sheet2=审核进度 (per-reviewer summary), Sheet3=规则维度 (per-rule summary); `javert_audit_logs` records `action=export, target_id=v_and_i, payload={row_count, format}`

#### Scenario: include_history=true adds Sheet 4

- **WHEN** a user GETs `/export?include_history=true`
- **THEN** the workbook has Sheet 4=审核历史 with ALL `javert_vio_review` rows (including is_latest=0 superseded ones), columns include `is_latest` so the consumer can filter

#### Scenario: Export with scope=all

- **WHEN** a user GETs `/export?scope=all`
- **THEN** Sheet 1 includes runs that had no review yet (verdict=C with no expert opinion); those rows have empty reviewer columns

#### Scenario: CSV format alternative

- **WHEN** a user GETs `/export?format=csv`
- **THEN** the response is a single CSV file (Sheet1 only, UTF-8 with BOM for Excel compatibility); the path used by users who don't want xlsx

### Requirement: Logout

The route `POST /logout` SHALL clear the session cookie and redirect to `/login`, plus log the action.

#### Scenario: Logout clears cookie and redirects

- **WHEN** a logged-in user POSTs `/logout` (typically from a header link with a form)
- **THEN** the response sets `javert_session=` with Max-Age=0 (cookie cleared), redirects 302 to `/login`; `javert_audit_logs` records `action=logout, user_id=<current>`

### Requirement: Static assets and templates with blue business theme

The system SHALL ship Jinja2 templates and static CSS/JS files under `src/javert/web/templates/` and `src/javert/web/static/`. Templates extend a base layout. CSS implements the **unified blue business theme** specified in design.md D9 — NOT the green/red/yellow of `router_v2_50patients.html`. Verdict badges (V/I/C) DO retain semantic colors (deep red / deep amber / deep green) since they convey functional meaning beyond decoration.

#### Scenario: Template files exist

- **WHEN** the system installs
- **THEN** files exist at `src/javert/web/templates/` for: `base.html`, `login.html`, `register.html`, `workbench.html`, `patient_detail.html`, `dashboard.html`, `welcome_banner.html` (partial); under `static/` for `style.css`, `app.js` (≤ 250 lines of plain JS for SSE + form submission + filter + banner dismiss), `favicon.svg`

#### Scenario: Base layout uses blue business theme tokens

- **WHEN** any page renders with the base layout
- **THEN** the top nav bar background is `#1e40af` (deep blue), the primary buttons use `#1e40af` with `#1d4ed8` hover, page background is white, card backgrounds use `#eff6ff` (very light blue) with `#e2e8f0` border; text is `#1e293b` (slate-800); link color is `#1e40af`

#### Scenario: Verdict badges retain semantic colors

- **WHEN** a violation card renders verdict=V
- **THEN** the badge background is `#b91c1c` (deep red) with white text — NOT the bright `#dc2626` from router_v2_50patients.html; verdict=I uses `#b45309` (deep amber); verdict=C uses `#047857` (deep green); these three colors are the ONLY non-blue elements on the page (excluding text)

#### Scenario: Favicon is simple "J" SVG

- **WHEN** a browser loads any page
- **THEN** the favicon (linked via `<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">`) is a 32x32 SVG of a white capital "J" letter on a `#1e40af` square background, no external icon library dependency

#### Scenario: Chinese font stack first

- **WHEN** the base CSS loads
- **THEN** the `body` font-family is `'PingFang SC', 'Microsoft YaHei', system-ui, -apple-system, sans-serif` — Chinese fonts first to render 中文 medical text cleanly on both macOS (PingFang) and Windows (YaHei)

### Requirement: Login welcome banner with since-last-login deltas

The workbench `GET /workbench` SHALL render a **dismissible welcome banner** at the top of the page on first request after login, showing deltas since the user's previous `last_login`. The banner reads from a stat method `SqlServerStore.get_since_last_login_stats(user_id, since)` and uses the `prev_last_login` value stashed in the session by the login route (NOT the current `last_login` which was just updated to "now").

#### Scenario: Login route stashes prev_last_login in session

- **WHEN** `POST /login` is processed and the user authenticates successfully
- **THEN** the route SELECTs the existing `last_login` value FIRST (before UPDATE), stores it as `session["prev_last_login"]` (ISO-8601 string), THEN runs `update_last_login(user_id)` to bump to now; if prev value was NULL (first login), session stores `"first_login"` sentinel

#### Scenario: Banner shows incremental counts

- **WHEN** user "alice" logged in 2 days ago (`prev_last_login = 2026-05-18T10:00:00Z`), and between that time and now 3 new patients were audited, contributing 15 new V rows + 7 new I rows
- **THEN** the workbench top banner renders text:
  ```
  欢迎回来, alice
  自上次登录 (2 天前): +3 病人 / +15 违规 / +7 不明
  你上次提交批复: 1 天前               [立即开始审核] [✕]
  ```
- The `[立即开始审核]` button scrolls to / focuses the first unreviewed V card in the sidebar
- Time strings ("2 天前", "1 天前") are generated server-side via a Chinese humanize helper

#### Scenario: First login shows different message

- **WHEN** a freshly-registered user "newbie" loads `/workbench` for the very first time (session has `prev_last_login = "first_login"` sentinel)
- **THEN** the banner reads:
  ```
  首次登录, 欢迎使用 Javert 审核工作台
  当前工作区: 50 病人 / 275 违规 / 120 不明 待审
  尚未提交批复                         [立即开始审核] [✕]
  ```

#### Scenario: User who has never submitted a review

- **WHEN** a returning user has `prev_last_login` set but `MAX(javert_vio_review.created_at WHERE user_id=current AND is_latest=1)` is NULL (logged in before but never reviewed)
- **THEN** the banner's "上次提交批复" line reads `尚未提交批复` (not a time)

#### Scenario: Dismissed banner does not reappear in same session

- **WHEN** the user clicks the `[✕]` button on the banner
- **THEN** the JS fetches `POST /api/banner/dismiss` with the current login timestamp; the server sets a cookie `welcome_dismissed=<login_ts>` (expires when session ends); subsequent navigations to `/workbench` in this session see the cookie and skip rendering the banner; on next login (new session), the cookie is overwritten with the new login ts and the banner shows again

#### Scenario: Banner renders only on /workbench, not on subpages

- **WHEN** a user logs in and navigates directly to `/workbench/J66252` (patient detail) without visiting `/workbench` first
- **THEN** the banner does NOT render on the patient detail page (it's a workbench-index-only feature); when they go back to `/workbench`, banner shows

#### Scenario: Banner numbers come from one efficient query

- **WHEN** the workbench page renders the banner
- **THEN** the server calls `SqlServerStore.get_since_last_login_stats(user_id=5, since=prev_last_login)` exactly once; the method runs at most 3 SQL queries (counts of new patients/V/I + last review timestamp) and returns a single typed `SinceLastLoginStats` dataclass; total banner render adds < 100ms to page load

### Requirement: Sidebar shows per-user review progress

The workbench sidebar SHALL show each patient's review progress for the current logged-in user, formatted as "已审 N/M" (reviewed N of M relevant runs). The denominator M depends on the active filter (V+I default counts both V and I runs; V-only counts V; ALL counts all runs).

#### Scenario: Sidebar progress text format

- **WHEN** the user "alice" loads `/workbench?filter=v_and_i` and has reviewed 5 of J66252's 18 V-or-I runs (5 reviews with `is_latest=1, user_id=alice.id, run_id IN J66252's V+I runs`)
- **THEN** the J66252 sidebar card renders text "已审 5/18" alongside the count badges; visually styled with progress color (green if 100%, blue if partial, gray if 0)

#### Scenario: Progress denominator follows active filter

- **WHEN** user switches from `filter=v_and_i` to `filter=v_only` for J66252 (which has 18 V and some I)
- **THEN** the denominator changes to "已审 X/15" (where 15 is the V-only count); the numerator changes to count only reviews on V runs

#### Scenario: Progress is per-user, not global

- **WHEN** user alice has reviewed 5 of J66252's 18 V+I runs, AND user bob has reviewed 10 different runs of J66252's 18 V+I runs
- **THEN** alice's sidebar shows "已审 5/18", bob's shows "已审 10/18"; they are independent (single audit, two opinions per design.md D5)

#### Scenario: Fully-reviewed patient is visually indicated

- **WHEN** a patient has all V+I runs reviewed by the current user (e.g. "已审 18/18")
- **THEN** the sidebar card gets a checkmark icon (✓) and a slightly muted background; the card is still clickable for re-review
