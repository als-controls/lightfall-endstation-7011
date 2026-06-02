# Notebook-pipelines next steps

Handoff document for the work that got paused on 2026-05-18. The
variance plugin in this directory is one piece of a cross-repo feature;
this doc tracks what's done, what's left, and how to pick it back up.

## State at pause

| Repo | Branch / HEAD | What's there |
|---|---|---|
| `lightfall-pipelines` | `master` @ `4783d96` | SDK + executor + papermill runner + env_cache (kernel-registration fix landed). 35/35 tests pass. **Not pushed.** |
| `lightfall-endstation-7011` | `master` @ `693d89a` | This: `VariancePipeline` + `compute_variance.ipynb` + 5 unit tests. **Not pushed.** |
| `ncs/ncs` (Lightfall) | `master` @ `8d0ea09` | Wire-up MR !14 merged (jobs/triggers panels, `PipelineClient`, dialogs); T18 e2e scaffolding. **Not pushed.** MR !14 still open upstream until master gets pushed. |
| `als-tiled` | `origin/master` | Mint endpoint + role-bootstrap + identity fix all deployed to bcgtiled. **MR !3 (`TILED_EXTRA_RW_PATHS`) open**, not yet merged or deployed. |

The auth-v2 chain (Keycloak → mint Tiled apikey → embed in NATS
JobMessage) is live and verified end-to-end. The `als-tiled-device`
Keycloak client is configured. bcgtiled has the `openid` role scope and
the `/data` ReadWritePaths in place (live-SQL until MR !3 redeploys).

Memory entries with the full historical context:
- `project_notebook_pipelines_status.md` — Stage 1-4 timeline, plan-drift
  corrections, deferred items.
- `project_lightfall_auth_v2.md` — auth-v2 rollout, hotfixes #1-#6.

## What to do next, in priority order

### 1. Run T18 against bcgtiled

The opt-in e2e test at
`~/PycharmProjects/ncs/ncs/tests/integration/test_pipelines_e2e.py`
has never been executed for real. Structural code is in place but
the first run will surface 2-3 small issues. Prep:

```powershell
~/PycharmProjects/lightfall-pipelines/.venv/Scripts/pip install ipykernel
$env:Lightfall_INTEGRATION = "1"
cd ~/PycharmProjects/ncs/ncs
.venv/Scripts/python -m pytest tests/integration/test_pipelines_e2e.py -v -s
```

`-s` is important on first run so the Keycloak device-flow URL prints
to your terminal. The 7-day apikey is cached at
`~/.cache/lightfall-pipelines/integration-key.json`; subsequent runs within
the week skip the prompt.

Expected hiccups:
- `from_uri(httpx_client=...)` kwarg may not be the real Tiled API.
- Tiled metadata layout for derived runs (where `tiled_access_tags`
  actually lives — `metadata.start` vs `nodes.access_blob` JSONB
  column) may need the assertion adjusted.
- The Keycloak `als-tiled-device` client may need its allowed scopes
  or grant types tweaked.

### 2. Lucid-pipelines deploy story

No `deploy/setup.sh` for `lightfall-pipelines` yet, so no beamline can run
the executor as a systemd service. Mirror als-tiled's pattern:
`/opt/lightfall-pipelines` install, systemd unit pointing at the beamline's
NATS URL, configured `--notebook-store` and `--env-cache` paths,
ExecStartPre if any reconciliation is needed. Without this, the
variance plugin sits at the bench but doesn't actually run on hardware.

### 3. Deploy variance on a 7011 host

Pick a 7011 VM (with bluesky / RunEngine running), install
`lightfall-pipelines` + this package, run the executor as a service, fire
the variance pipeline on a real run via Lightfall's "Run pipeline..."
context menu in the Tiled browser. This is the highest-signal test of
feature readiness — the synthetic-data unit tests don't catch
schema-shape mismatches against bluesky V3 events.

The variance notebook has a try/except fallback for
`stream.read()` vs direct node read; real data will pick one path and
the other can be deleted.

### 4. Polish (small, can be batched into one MR)

- Wrap `PipelineClient.list_available` in `QThreadFuture` (same pattern
  as `_submit`) so the Run Pipeline dialog opening doesn't block.
- Triggers panel pre-populates pipeline names by calling
  `list_available` at first show.
- `AddTriggerDialog` accepts a `pipelines=` list when constructed from
  inside the docked Triggers panel (already supports it from the
  dialog side; just needs the panel to pass it).

### 5. Push everything, close MRs

```powershell
cd ~/PycharmProjects/ncs/ncs;             git push upstream master
cd ~/PycharmProjects/lightfall-pipelines;     git push origin master
cd ~/PycharmProjects/ncs/lightfall-endstation-7011; git push origin master
cd ~/PycharmProjects/als-tiled;           # review MR !3, merge, redeploy bcgtiled
```

Pushing ncs/ncs master auto-closes MR !14.

## Known gaps in lightfall-pipelines

### Install-from-local-source

`env_cache.build` does `pip install <pkg>==<ver>` which assumes the
plugin is on PyPI. For T18 and for any non-published beamline plugin
this fails. The T18 fixture works around it by pre-seeding the
env_cache directory by hand. The proper fix is a
`Lightfall_PIPELINES_LOCAL_PLUGINS` env var (or `--plugin-source
pkg=/path/to/source` CLI arg) that lets `env_cache` pip-install from a
directory when the package isn't on PyPI. Until that lands, every
beamline plugin needs to be on PyPI or have a published wheel.

### list_available is synchronous

`PipelineClient.list_available` does a blocking NATS request. Until
item 4 above lands, opening the Run Pipeline dialog freezes Qt for
~1 second per pipeline list call.

## Resuming

1. Read this file.
2. Read `project_notebook_pipelines_status.md` in memory.
3. Run the T18 prep block above.
4. Iterate.
