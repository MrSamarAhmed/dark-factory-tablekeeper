# FACTORY — ORBIT, the three-agent software factory

ORBIT is an autonomous software factory built for the WeAreDevelopers × BAND
"Dark Factory" hackathon. It turns a written specification into a shipped
artifact using **three distinct coding-agent seats** cooperating in a real BAND
chat room, over a plain-text protocol (`ORBIT:SPEC / PLAN / TASK / REVIEW /
VERDICT / REVISE / DONE / FAIL`).

## The three seats

| Seat | Role | Writes code? | Approves? |
|---|---|---|---|
| **Architect** | Decomposes the spec into 1–3 ordered tasks with acceptance criteria, dispatches them, owns `state.json`, drives the REVISE cycle | No | No |
| **Builder** | Implements one task at a time in the run workspace, creates and runs the tests, reports via `BUILD_REPORT:` | Yes (only seat with workspace write access) | No |
| **Reviewer** | Independently verifies each build as code it did not write — re-reads files, re-runs tests, judges against the spec — and issues the gate verdict | No | Yes (only seat that can APPROVE/REJECT) |

Full seat contracts: [`mandates/architect.md`](mandates/architect.md),
[`mandates/builder.md`](mandates/builder.md),
[`mandates/reviewer.md`](mandates/reviewer.md). The mandates are generic and
track-agnostic; they are derived from the actual system prompts the three BAND
agents ran with.

Each seat is a separate BAND agent identity (see `room.json`) driven by a
headless coding CLI. The Builder runs with full write access confined to the run
workspace; the Reviewer runs read-only (file reading and shell for tests only).

## Run mechanics

- One run = one directory: `state.json` (Architect-owned single source of truth),
  `events.jsonl` (append-only protocol timeline), `builds/` (builder reports per
  attempt), `reviews/` (reviewer records per attempt), and `workspace/` (the
  git-init'd Builder working directory that becomes the shipped artifact).
- Retry policy: up to 3 attempts per task. A Reviewer REJECTED verdict sends the
  task back through an `ORBIT:REVISE` re-dispatch with the rejection evidence
  attached. Exhausting the cap fails the task (`ORBIT:FAIL`).
- The Reviewer's verdict is the only gate: the Builder can never approve its own
  work, and the Architect can never overrule a rejection.

## The delivered run: `run-1791009577173`

**Spec:** Tablekeeper — Stage 1 (restaurant table-reservation HTTP service:
auth, catalogue, availability, reservations with create/list/get/cancel/amend/
move, strict opening-hours, idempotency and concurrency semantics). Delivered
to the Architect seat over BAND REST.

**Plan:** 3 ordered tasks — `task-1` Skeleton & Contracts, `task-2` Core Domain
& Availability, `task-3` Hardening & Concurrency.

**Timeline (UTC, from `evidence/events.jsonl`):**

| Time | Event |
|---|---|
| 06:39:37 | Spec received by Architect seat |
| 06:39:44 | `plan_created` — 3 tasks |
| 06:39:46 – 06:54:07 | task-1: build (12.4 min) → review APPROVED, attempt 1 |
| 06:54:08 – 06:58:29 | task-2: build (2.4 min) → review APPROVED, attempt 1 |
| 06:58:29 – 07:02:32 | task-3 attempt 1: build (3.7 min) → review APPROVED; run completed |
| 07:08:40 | task-3 REJECTED (attempt 1) — *operator-injected verdict, see disclosure* |
| 07:08:41 – 07:24:01 | task-3 attempt 2: REVISE build → review APPROVED; run re-completed |
| 08:11:01 | task-3 REJECTED (attempt 2) — *operator-injected verdict carrying a real defect report, see disclosure* |
| 08:11:03 – 08:13:19 | task-3 attempt 3: REVISE build (17 s) → independent review (2 min) APPROVED; run completed |

Room `updated_at` (`08:13:19.311270Z`, see `room.json`) matches `run_completed`
to the second — the factory's last protocol message is the last message in the
real BAND room.

### What each attempt actually changed

Backed by `evidence/builds/` and `evidence/reviews/` plus workspace file mtimes:

- **task-1 / task-2 (attempt 1 each):** service skeleton, contracts, core
  domain, availability engine. Both approved on first review.
- **task-3 attempt 1:** hardening — idempotency receipts with replay headers and
  reuse-conflict handling, atomic reservation moves, `BEGIN IMMEDIATE`
  concurrency safety, export/import persistence. Approved by the Reviewer after
  independently running the suite.
- **task-3 attempt 2:** the Builder session revised `reservations.py` (07:14:12Z)
  and `moves.py` (07:16:03Z) but hit the 15-minute session cap at 07:23:41Z and
  was terminated before emitting its `BUILD_REPORT` (the build artifact
  `task-3-a2.md` is empty). The Reviewer was dispatched with
  "(builder produced no report)", independently inspected the workspace as it
  stood, ran the suite (19 passed), and APPROVED. These partial changes are part
  of the final, fully re-verified artifact.
- **task-3 attempt 3 (the shipped repair):** the only file changed is
  `tests/test_tablekeeper.py` (mtime 08:11:11Z): the `test_idempotency_behavior`
  case built its reservation time from an unanchored `now + 4 days + 12 hours`,
  which lands at ~22:06 Europe/Berlin — after the 23:00 close — so the service
  correctly returned `409 NO_AVAILABILITY` and the test's expected `201` failed
  at certain hours of the day. The one-line fix anchors the timestamp to
  17:30 UTC (`replace(hour=17, minute=30, …)`), which under both CET and CEST
  puts the 90-minute window fully inside the 18:00–23:00 opening window. The
  Builder ran the suite twice (19/19 both runs); the Reviewer independently
  inspected six implementation/test files, re-ran the suite (19 passed), and
  APPROVED.

### Disclosures

- **Operator-injected verdicts.** The factory's protocol carries rejections as
  ordinary `ORBIT:VERDICT … verdict=REJECTED` messages. The two task-3
  rejections were sent by the operator through that same message path (not by
  the Reviewer seat itself): the first (07:08:40) to exercise the
  rejection → REVISE → re-approve recovery cycle; the second (08:11:01) to route
  a genuinely diagnosed defect — the time-of-day-flaky test above — through the
  factory for repair. Everything downstream of each injection is real agent
  work: the Architect re-dispatched, the Builder changed real files, and the
  Reviewer independently inspected and re-ran the suite before approving.
  All three task-1/task-2 reviews and the attempt-1/attempt-3 task-3 reviews
  were genuine Reviewer-seat verdicts, unprompted.
- **`room.json` holds the full message history.** The export was taken with
  `"scope": "full"` and contains 132 messages, each with its `content`,
  `insertedAt`, `senderName` and `senderType`: by sender, 54 Architect, 48
  Builder, 29 Reviewer and 1 operator message; by type, 129 `text` messages and
  3 `participant` join events (the two agent seats and the operator). An earlier
  version of this file stated that the BAND REST API exposes no message-history
  endpoint and that the export carried metadata and participants only. That was
  wrong — the transcript is in the repository, including the two injected
  verdicts above (messages 122 and 127) and the Stage 1 requirements the
  service was built against. `evidence/state.json`, `evidence/events.jsonl`
  and the per-attempt artifacts in `evidence/builds/` and `evidence/reviews/`
  remain the factory's own structured records and are the cross-check for the
  timeline above.
- **Builder timeout semantics.** A Builder session killed at the 15-minute cap
  is recorded by the factory as `build_finished` with whatever partial output
  exists — attempt 2 above is the visible instance (empty build artifact). This
  is stated as observed; it is factory-infrastructure behavior and was not
  modified for this submission.

## Reproducibility

- Commit `9a56d71` is the imported artifact; its byte-for-byte identity with the
  Builder workspace has not been independently established. Subsequent local
  changes are outside the factory loop: archived original tests, a replacement
  suite, and application repairs. The October 1 contract (room message index 40)
  differs from the October 3 NEW SPEC (index 110) tied to the delivered run.
  The original tests match the later contract, not an earlier draft. Their
  archive name `evidence/superseded_tests/` does not resolve this conflict.
  See README for current repair evidence and unresolved requirements; current
  passing tests do not verify the factory's historical approval claims.
- The suite and the image are reproducible; the factory run is not replayable
  from this package (it needs the BAND service and the seat credentials).
- Factory credentials are NOT included. The factory runs from its own
  repository with a gitignored `.env` holding per-seat BAND agent keys; nothing
  in this package depends on them.
