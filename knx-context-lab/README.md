# KNX DeviceUpdate context lab

Review lab, updated 2026-09-30 for core PR #183272.

## Run

Docker Desktop (or Docker Engine with Compose) is required. From this directory:

```sh
docker compose -p knx-context-lab up --build -d
docker compose -p knx-context-lab exec lab python /opt/lab/context_smoke.py
docker compose -p knx-context-lab exec lab python /opt/lab/smoke.py
```

Open http://localhost:18124/lovelace/simulation. Login: `reviewer` / `knx-lab-only`.
The simulator sends real KNX/IP routing packets over UDP inside the container. There is no physical KNX connection. Only HA's HTTP port is published, bound to localhost.

To demonstrate manually, turn Lab trigger off, reset the cover to open, and press Wall switch. The automation moves the cover to 50% and stops after eight seconds. Activity should identify **KNX switch moves cover**. Direct cover down instead identifies sender **1.1.24**. Use the Activity page or the cover's more-info Activity tab to inspect the cause.

## Persistence

After running both tests:

```sh
docker compose -p knx-context-lab restart
docker compose -p knx-context-lab exec lab python /opt/lab/context_smoke.py --history-only
docker compose -p knx-context-lab exec lab python /opt/lab/smoke.py --history-only
```

Wait for the container to become healthy before testing after startup/restart.

Stop with `docker compose -p knx-context-lab stop`; start again with `docker compose -p knx-context-lab start`. `down` removes the container while retaining the data volume. `down -v` also deletes this lab's test data.

## Reproducibility and source

The Dockerfile is standalone: it embeds the runtime scripts, configuration, dependency lock, and prototype source overlay. It can be copied into an empty directory and built with `docker build -t knx-context-lab .`. Run with `docker run --init -p 127.0.0.1:18124:8123 -v knx-context-data:/config knx-context-lab`.

- HA source archive: `82ee85de578cb51a2304df419434f76385455b67`, plus the embedded source overlay (includes subsequent PR changes).
- HA adaptation: `f28862c6133d588c99b08f495d097403d8d1a32e`, branch `knx-light-activity-source`.
- XKNX draft source: `4180b8a38c4448ef0ac968dbb1bcbc051b2e2d38` from PR #1952. **This is an unreleased draft, not a new published XKNX release.**
- Python base image uses an immutable digest; runtime requirements are version-pinned. Building still requires access to public source/package servers and system build packages; it is not a bit-for-bit hermetic build.
- `prototype-files.json` contains readable full source files matching the embedded overlay.
- `prototype.patch` shows the adaptation relative to the earlier published head `3874aa79b1ad124d85a430af83fa1bc9df95e63c`. `test_context.py` contains the additional regression tests.

## What this prototype covers

Shared incoming context handling in `_KnxEntityBase`, using explicit XKNX `DeviceUpdate`. Incoming telegram metadata is reused; a single KNX Activity event is emitted lazily for a relevant state change. Lights, switches, and covers preserve HA service context through outgoing operations. Other callbacks are adapted to the new API. Context handling and outgoing scopes are limited to lights, switches, and covers; other platforms retain their existing behavior.

The HA test executes a real automation triggered by a KNX telegram. The Docker test additionally exercises real KNX/IP UDP, live WebSocket Activity, recorder queries, and restart persistence. The simulator uses synthetic project names, not a real ETS project. This does not validate physical hardware or KNX Secure.

## Validation

- Complete KNX integration suite: **611 passed**, including 24 snapshots.
- Two new HA regression tests: automation-to-cover and direct-cover context.
- Docker automation test: delayed cover updates beyond five seconds, outgoing automatic stop, correct automation context, no duplicate KNX attribution, direct sender context, live and recorded Activity.
- Docker light tests: eight live/recorded attribution checks, including user context and avoiding stale attribution on brightness updates.
- Recorded Activity survives restart.
- Ruff lint and formatting pass for the KNX integration and tests.
- Mypy passes for all nine changed production Python modules checked. Integration-wide mypy reports three draft-library compatibility errors in unchanged button.py, text.py and notify.py (optional DPT types). These remain for the dependency upgrade.
- Repository-wide `prek run --all-files` could not complete because this is a sparse checkout with omitted tracked files. It is not claimed as passing.

Two existing tests needed compatibility updates for the draft: `Devices` no longer supports numeric indexing; DPT9's maximum is now `670433.28`. These adaptations are isolated in commit `bbcea0cd21`, separate from the Activity implementation.

Screenshots: `automation-cover-activity.png` shows the trigger → automation → cover chain, including the automatic stop; `direct-cover-activity.png` shows sender 1.1.24. Both were captured from recorded Activity after restart.

KNX hassfest validation passes with the pinned draft installed as a regular package. Pylint passes for the changed implementation and Activity tests. The HA manifest still pins released XKNX 3.20.0: normal PR CI cannot validate the new API until its release and dependency update.

The simulator delays actuator feedback by 100 ms to exercise HA processing its own outgoing command first. If physical actuator feedback arrives first and causes the state change, Activity attributes that change to the actuator; it does not infer the preceding user action. Cover/trigger traffic maintains separate state from the simulated light.
