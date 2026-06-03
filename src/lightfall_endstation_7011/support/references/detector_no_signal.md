# Detector shows no signal

User symptom: "I can't see anything on the detector" — the CCD image is blank or
shows no counts.

## Safety rules (repeat)

- Reads auto-run; writes/moves are propose-then-confirm: state the exact device
  and target value, get an explicit OK, then call `ncs_set_device` /
  `ncs_move_motor`.
- Catalog-or-manual: resolve named devices via `ncs_list_devices` /
  `ncs_read_device`; if missing or not readable that way, ask the user to
  check/change it in CSS-Phoebus, citing the PV and expected value.

Each step is tagged AUTO (you read it), MANUAL (the user does/observes it), or
CONFIRM (you propose a write/move and execute only on explicit OK).

## Step 0 — Identify the detector (ask)

Ask the user:
- Which detector are you using — PICAM (`13PICAM1`) or ANDOR (`BL7ANDOR1`)?
- Are you using the Diode? If yes, the expected shutter mode below is
  `Always Open`.

## Step 1 — Is there beam? (AUTO)

Call `ncs_get_beam_status`. Confirm ring current is roughly 500 mA and
beam/shutter is available. If the ring current is near zero or beam is down,
that is the likely cause — report it and stop; tell the user to check with the
floor/operators. Downstream checks are moot without beam.

## Step 2 — Vacuum valves (MANUAL)

Ask the user to confirm that all vacuum valves are open/green up to the
endstation on the CSS-Phoebus panel.

## Step 3 — Optical saturation (MANUAL)

Ask the user: are all chamber viewports covered, and is the chamber light off?
Stray optical light can saturate the CCD so it shows nothing useful.

## Step 4 — Camera shutter mode (AUTO read -> CONFIRM write)

Expected (TV mode):
- PICAM: `13PICAM1:cam1:ShutterTimingMode` = `Normal` (or `Always Open` if using
  the Diode).
- ANDOR: `BL7ANDOR1:cam1:AndorShutterMode` = `FullyAuto` (or `Always Open` if
  using the Diode).

Read the current mode (via the catalog if the camera is registered). If it is
wrong, propose setting it to the expected value and set on OK. If it is not
reachable via the catalog, ask the user to set it in Phoebus (cite the PV and
expected value).

## Step 5 — Is it acquiring? (CONFIRM, else MANUAL)

Ensure the camera is acquiring: `13PICAM1:cam1:Acquire` (PICAM) or
`BL7ANDOR1:cam1:Acquire` (ANDOR) = `1`. Propose starting acquisition and set on
OK, or ask the user to start it in Phoebus.

## Step 6 — Is a diagnostic blocking the beam? (AUTO where catalog-resolvable, else MANUAL)

The values below are the nominal positions for a clear beampath. A reading that
differs means that component may be intercepting the beam — confirm with the
user before concluding it is the cause.

- `DIAG111` position = 0 mm — motor readback (AUTO).
- `CAL111` = 60 mm — motor readback (AUTO).
- `DIAG101` = 0 mm — motor readback (AUTO).
- Manual YAG before `M112` rotated **out** of the beampath (MANUAL — physical).
- `DIAG111` physically retracted — confirm via its manual insertion switch
  (MANUAL). This is a second, independent indicator of the same `DIAG111`
  diagnostic; if it disagrees with the 0 mm readback above, ask the user.

Read the motor positions via the catalog where possible; ask the user for the
manual switch and YAG state.

## Step 7 — Is there light on the YAG? (CONFIRM move + MANUAL observe)

Propose moving `PinholeX` and `PinholeY` to the YAG position (0, 0); move on OK
(or ask the user to). Then ask the user to check the chamber cam — is there
light on the YAG? This localizes whether beam reaches the endstation YAG.

## Step 8 — Localize upstream of M112 (MANUAL)

Ask the user to manually rotate the YAG before `M112` into the beam and check
the window viewport — is beam visible there? This distinguishes a problem
upstream vs downstream of `M112`.

## If all checks pass

If beam is present, valves are open, there is no optical saturation, the shutter
is in TV mode, the camera is acquiring, no diagnostic is blocking, and light
reaches the YAG but the detector still shows nothing, escalate to beamline staff
(e.g. Sophie) with the collected results.
