# Tablekeeper — Stage 1 Execution Guide

Commands below are setup examples; historical timings are not performance guarantees.
Latest audit: 98 tests passed after clean dependency installation on isolated Python
3.9.25 and 3.12.16 (zero skips; one Starlette deprecation warning on 3.12).
The rebuilt image served health 200, reset 204 and a nonempty catalogue on PORT=9000
with networking disabled. See README for the unresolved specification conflict.

## Build and start in one step (the submission path)

```bash
cd stage-1
docker build -t tablekeeper-stage1 .
docker run --rm -p 8080:8080 -e PORT=8080 tablekeeper-stage1
```

```
built in ~8s            # layers cached; a cold build pulls python:3.12-slim first
healthy 1s after start  # GET /health -> 200 {"status":"ok"}
```

No manual setup, no volume, no outbound network at run time — verified by
restarting the image with `--network none` and calling `/health`,
`POST /_test/reset` and `GET /restaurants` from inside the container.

`PORT` is respected (`-e PORT=9000` + `-p 127.0.0.1:9099:9000` served on 9000).

## Test suite

```bash
cd stage-1
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q      # current suite: 98 tests
```

Or without creating a local environment:

```bash
docker run --rm -v "$PWD":/work -w /work python:3.12-slim \
  sh -c "pip install -q -r requirements.txt && python -m pytest -q"
```

Both the 3.12 and the 3.9 image run the suite green, which is required because
the delivery constraints demand 3.9 compatibility. The 2026-10-28 reruns used
the container command above with `python:3.12-slim` and `python:3.9-slim`
invoked from `stage-1/`. `.github/workflows/ci.yml` now enforces both versions on
every push and pull request and additionally builds this directory's Dockerfile and
asserts `/health`; see "CI status" in the top-level README for what that workflow
does and does not cover.

One environment caveat, discovered rather than assumed: the service hashes
passwords with `hashlib.scrypt`, and the Python bundled with Xcode command-line
tools is linked against LibreSSL 2.8.3, where `hashlib` has no `scrypt`. The
suite therefore cannot run on that interpreter — use a Homebrew/pyenv/python.org
Python, or the container command above.

## Run the server locally

```bash
cd stage-1
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

## Health check

```bash
curl http://localhost:8080/health
# {"status":"ok"}
```

## Smoke round-trip

```bash
curl -s -X POST localhost:8080/_test/reset -H 'Content-Type: application/json' \
  -d '{"users":[],"restaurants":[],"reservations":[]}' -w '%{http_code}\n'   # 204
curl -s -X POST localhost:8080/auth/signup -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","password":"correct horse","display_name":"Ada"}'
```

## `/_test/*` is unauthenticated on purpose

Stage 1 §3.3 requires `POST /_test/reset` (and export/import) to be enabled and
unauthenticated in the delivered image, so reset and import destroy or replace
all state without credentials. Keep the container on the evaluation network;
gate those three routes at a proxy before exposing the service anywhere else.
