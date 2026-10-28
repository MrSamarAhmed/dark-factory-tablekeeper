# Tablekeeper — Stage 1 (Dark Factory submission)

A restaurant table-reservation HTTP service produced through **ORBIT**, a
three-agent software factory (Architect → Builder → Reviewer) operating through
BAND.

The factory decomposes the specification into implementation tasks, dispatches
coding work, independently reviews builds, and supports rejection → revision →
approval cycles. The factory run and supporting artifacts are preserved under
`evidence/`.

## Submission scope

This submission targets **Stage 1** of the Dark Factory Tablekeeper track.

The shipped Stage 1 artifact is contained in `stage-1/` and includes the Docker
configuration, execution guide, FastAPI service, database schema, and tests
required for Stage 1.

## Layout

```text
result/
├── README.md
├── FACTORY.md
├── pytest.ini
├── .github/
│   └── workflows/ci.yml
├── mandates/
│   ├── architect.md
│   ├── builder.md
│   └── reviewer.md
├── room.json
├── stage-1/
│   ├── Dockerfile
│   ├── RUN.md
│   ├── requirements.txt
│   ├── app/
│   └── tests/
└── evidence/
    ├── state.json
    ├── events.jsonl
    ├── builds/
    ├── reviews/
    └── superseded_tests/
```

## Quick start

### Local

```bash
cd stage-1

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m pytest            # the contract suite
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
```

With [uv](https://docs.astral.sh/uv/) instead:
`uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements.txt`.

One caveat, verified rather than assumed: the suite needs `hashlib.scrypt`, and
the Python that ships with Xcode command-line tools is linked against LibreSSL,
which does not provide it (`hashlib` has no attribute `scrypt`). On macOS use a
Homebrew/pyenv/python.org build, or run the suite in a container:

```bash
docker run --rm -v "$PWD":/work -w /work python:3.12-slim \
  sh -c "pip install -q -r requirements.txt && python -m pytest -q"
```

### Docker

```bash
cd stage-1

docker build -t tablekeeper-stage1 .
docker run --rm -p 8080:8080 -e PORT=8080 tablekeeper-stage1

curl http://localhost:8080/health
```

## Official Stage 1 verification

The Dark Factory harness that scored this submission is external to this
repository. Its headline result is recorded here for completeness:

| Check | Claimed | Reproducible from this repo? |
|---|---|---|
| Official Stage 1 suite (6 files, 120 cases) | 120/120 | **No** — the harness is not part of the submitted artifact |

The tables below separate what was re-measured on 2026-10-28 from what was only
carried over from earlier observations. Neither establishes compliance with both
conflicting specifications described further down. Dependency versions remain
unpinned.

### Re-measured on 2026-10-28 (isolated containers, current working tree)

| Check | Command | Result |
|---|---|---|
| Contract suite, Python 3.12.16 | `python -m pytest -q` in `stage-1/` | exit 0 — **98 passed** |
| Contract suite, Python 3.9.25 | same, container `python:3.9-slim` | exit 0 — **98 passed** |
| Committed HEAD tree on Python 3.9 | `git archive HEAD` → same command | exit 1 — **19 collection errors**, `TypeError: unsupported operand type(s) for \|` from `app/idempotency.py` |

The last row is the reason the 3.9 run is listed as a repair rather than a
baseline: the committed source could not even be imported on the interpreter the
delivery constraints require.

### Earlier observations (not revalidated in full)

| Check | Command | Result |
|---|---|---|
| Contract suite on Python 3.12 at HEAD | `python -m pytest -q` in `stage-1/` | **82 passed**, reproduced by the 2026-10-28 audit before repairs |
| Image build | `docker build -t tablekeeper stage-1` | built in ~8 s |
| Boot to health | `-e PORT=9000`, `GET /health` | `200 {"status":"ok"}` 1 s after start, inside the 2 vCPU / 2 GiB / 60 s limits |
| Full HTTP round-trip | `POST /_test/reset` → login → `GET /availability` → `POST /reservations` → replay → cancel | 204, 200, 8 slots, 201, 200, 200 |
| Error envelope on 404 / 401 / 400 | unmatched route, missing token, invalid body | `{"error": {"code", "message"}}` on every one |
| Error envelope on 405 | method not allowed on a known path | `{"error": {...}}` — only reachable **after** the `app/errors.py` repair, and the `not_found` code used for 405 is not named in either spec |
| Runtime with no network | `--network none` | healthy, reset 204, catalogue served |

The 404 and 405 rows postdate the `app/errors.py` change: at HEAD those answers
used FastAPI's `{"detail": ...}` body, which the October 3 spec §9 explicitly
permits ("Unknown routes → 404 (FastAPI default is fine); unknown methods → 405")
while the October 1 spec §5 requires the envelope. Unifying them is therefore a
choice in favour of the October 1 contract, not a repair of an unambiguous defect,
and the code name `not_found` for 405 is our own.

### CI status

`.github/workflows/ci.yml` runs on every push and pull request. A `suite` job
installs `stage-1/requirements.txt` and runs `python -m pytest -q` from `stage-1`
on Python 3.9 and 3.12 (`fail-fast: false`); an `image` job builds
`stage-1/Dockerfile` and asserts `GET /health` returns exactly `{"status": "ok"}`
under the delivery limits of 2 vCPU / 2 GiB. It references no secrets.

What a green run does **not** mean:

- `pytest.ini` limits collection to `stage-1/tests` and excludes `evidence/`, so
  `evidence/superseded_tests/test_tablekeeper_stage0_spec.py` — the file that
  encodes the October 3 contract — never executes on CI. The unresolved
  October 1 vs October 3 conflict is therefore invisible to the workflow, exactly
  as it is invisible to the local suite.
- The `image` job checks the health envelope only. It does not run the contract
  suite inside the built image, so it cannot fail for a behavioral regression.
- `requirements.txt` is lower-bound pinned (`fastapi>=0.110.0`, `pytest>=8.0.0`),
  so CI installs whatever is newest; a future FastAPI/Starlette/pytest release can
  break the run for reasons unrelated to this repository.

Before it was committed, every step was replayed verbatim in isolated Linux
containers: 98 passed on Python 3.12.16 and on 3.9.25 with the newest resolvable
dependencies, and the health check returned two `200 OK` responses from the built
image. That rehearsal caught one real defect — the `image` job declares no Python,
so the bare `python` it originally called resolved to nothing on macOS and on
minimal Linux bases (exit 127, `python: command not found`) and only worked
wherever the OS happens to ship `python-is-python3`. It now calls `python3`.

The results in the table above still come from the 2026-10-28 manual reruns; the
workflow is the record for anything after that.

### The test suite was rewritten

The suite that was committed with this repository (19 tests in
`stage-1/tests/test_tablekeeper.py`) failed 19/19 against the committed app.
The transcript contains incompatible contracts. The October 1 message (zero-based
index 40) uses lowercase codes, 204 reset, `display_name`, and
`{track, format_version, state}` exports. The October 3 NEW SPEC (index 110),
associated with the delivered run, uses uppercase codes, 200 reset, `name`, and
`RES-XXXXXXXX` references; it matches the original tests.

An earlier audit incorrectly described the latter as an earlier draft and moved
those tests to `evidence/superseded_tests/test_tablekeeper_stage0_spec.py`.
That filename does not establish that their contract was superseded. Both the
archive and replacement suite are preserved pending an explicit contract decision.
The active suite targets the October 1 behavior of the shipped app; passing it
is not proof of compliance with the October 3 delivered-run requirements.
No API rewrite has been made to resolve that conflict.

Previous local changes, outside the factory run, included:

- unmatched routes and methods answered `{"detail": "Not Found"}` instead of the
  §5 envelope (`stage-1/app/errors.py`) — a §5 requirement under the October 1
  contract that the October 3 contract explicitly waives for unknown routes and
  methods, so this is a contract choice as much as a repair;
- `POST /reservation-moves` validated item by item, so a conflict in the first
  move masked an unknown reference in a later one; §11 makes ownership and
  single-restaurant batch conditions, checked before any change
  (`stage-1/app/moves.py`);
- `dict | list | None` annotations made `app/idempotency.py` unimportable on
  Python 3.9, which the delivery constraints require;
- the README's own provenance note about `room.json` was wrong — see below.

## Current audit limitations

The latest local repairs make password whitespace significant (only
`password.strip()` was dropped; email and display name are still trimmed — neither
spec addresses password whitespace, so this is a hardening choice, not a
documented requirement), take a transactional export
snapshot, reject unsafe imported numeric/timezone/opening-hours configuration,
preserve the HTTP `Allow` header, and make the uncommitted draft workflow fail
when health never becomes valid.
Sixteen regression cases cover these changes; the complete current suite has
98 passing cases on isolated Python 3.12.16.

Still requiring work: complete imported reservation/receipt semantic validation,
mixed batch-error precedence, oversized numeric inputs, dependency locking and
security scanning, and a decision on the conflicting contracts. The test endpoints
remain unsafe for public exposure. No production-readiness claim is made.

## Factory evidence

The factory run recorded:

- 3 ordered tasks
- 5 build attempts/artifacts
- 5 review records
- rejection → revision → approval recovery cycles
- independent Reviewer verification

The detailed factory history is documented in `FACTORY.md`, with supporting
records in `evidence/`.

## Honesty and provenance

`room.json` is a full export of the BAND room (`"scope": "full"`): 132
messages with their bodies, timestamps and sender identities — 131 from the
Architect, Builder and Reviewer seats plus one join event. It is the primary
transcript of the run, and it is where the Stage 1 requirements quoted above
are recorded verbatim.

An earlier version of this file claimed the BAND API exposes no message-history
endpoint and that the export held metadata and participants only. That was
false, and it mattered: it hid the document the app was built against.
Corrected here.

The development history includes two **operator-injected** task-3 rejection
verdicts through the protocol's normal verdict message path. These were used to
exercise the rejection → revision → review workflow and to route a real,
independently-diagnosed test-timestamp defect through the factory. Both
injections are disclosed in `FACTORY.md`; the resulting repair was built and
independently reviewed by the agents.

In `room.json` those two verdicts are messages 122 and 127
(`2026-10-03T07:08:39.501Z` and `08:11:00.044Z`). They carry the Reviewer
seat's identity because they were sent through that seat's message path, so the
transcript alone cannot distinguish them from a genuine Reviewer verdict —
which is the reason they are disclosed here rather than left implicit.

This submission does not claim that every development action was autonomous.
The factory workflow, agent collaboration, recovery cycle, and independent
review are documented in the supplied evidence.

No API keys, tokens, or secrets are included in this package. Factory
credentials live in the factory's own gitignored `.env`.

## Operational note on `/_test/*`

`POST /_test/reset`, `GET /_test/export` and `POST /_test/import` are
unauthenticated: reset and import destroy or replace all state and export
dumps token hashes and reservation rows. Stage 1 §3.3 requires exactly that —
"This test endpoint must be enabled in the delivered image and requires no
authentication" — because the evaluating harness drives the service through
these endpoints, in a container that has no outbound network and is discarded
after the run. The image therefore ships them open, as specified.

Do not put this image behind a public listener as-is. In any deployment beyond
the evaluation container, gate those three routes at the proxy (or rebuild with
them unregistered) and treat the SQLite file as secret-bearing.

## Reproducibility

The shipped artifact is contained in `stage-1/`.

See `stage-1/RUN.md` for the complete local and Docker execution instructions.
