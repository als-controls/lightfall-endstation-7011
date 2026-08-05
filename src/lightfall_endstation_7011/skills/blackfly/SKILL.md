---
name: blackfly
description: "Discover and wire FLIR Blackfly S cameras into user PanelPlugins"
---

## Blackfly Camera Skill

Use this skill when the user asks for a panel that shows a FLIR Blackfly S
(or any GigE Vision Blackfly) camera, or mentions making a viewer for one of
those cameras.

### Public API

```python
from lightfall.ui.widgets.observers import CameraImageView
from lightfall_endstation_7011.observers.blackfly import BlackflyCamera
```

`BlackflyCamera(device_ip, bind_ip)` takes two strings: `device_ip` is the
camera's IPv4 address; `bind_ip` is the host NIC IP the camera should send
GVSP packets to. These two are easy to confuse, so the workflow below walks
through them explicitly.

### MCP tool

`discover_blackfly_cameras(bind_ip=None, timeout_s=1.0)` — scans the local
subnet for GigE Vision cameras and returns a list of
`{ip, manufacturer, model, serial, user_name}` entries. The `user_name` is
the operator-set label (often something like "tomo-front") and is the
friendliest way to disambiguate two cameras of the same model on one
subnet. If the user has not given you an explicit `device_ip`, call this
tool first. If `bind_ip` is omitted, the tool auto-detects the
default-route NIC.

### Workflow

1. If the user did not provide a `device_ip`, call `discover_blackfly_cameras`.
2. If multiple cameras are returned, ask the user which one to use.
3. If the user did not provide a `bind_ip` (the host NIC), ask for it.
4. Read `references/panel_template.py` for the canonical PanelPlugin source.
5. Substitute `<IP>` with the chosen `device_ip` and `<HOST>` with the chosen
   `bind_ip` in the template's text.
6. Call `mcp__panel_builder__ncs_create_user_plugin` with the substituted
   source as the `code` argument and a name like `blackfly_<short-id>`.
7. Confirm to the user: the new panel will appear under View > User > Blackfly S Live View.
