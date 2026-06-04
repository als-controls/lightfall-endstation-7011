"""BlackflyAgent: discover and wire FLIR Blackfly S cameras into user PanelPlugins."""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.utils.logging import logger


def _default_bind_ip() -> str:
    """Best-effort source IP for the default-route NIC.

    Uses the same UDP-connect idiom as bfly-discover. No packet is sent — the
    kernel just performs a route lookup. Returns "0.0.0.0" if no default route
    exists (air-gapped beamline workstations); the agent can still take an
    explicit ``bind_ip`` from the user in that case.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        s.close()


class BlackflyAgent(AgentPlugin):
    """Skill telling the embedded Claude agent how to build a Blackfly live-view panel.

    Contributes a SKILL.md body teaching the public Blackfly API + workflow, and
    one MCP tool (``discover_blackfly_cameras``) that the agent uses to find
    cameras on the network. The actual panel-creation work is delegated to the
    existing ``panel_builder`` agent.
    """

    @property
    def name(self) -> str:
        return "blackfly"

    @property
    def display_name(self) -> str:
        return "Blackfly Camera"

    @property
    def description(self) -> str:
        return "Discover and wire FLIR Blackfly S cameras into user PanelPlugins"

    @property
    def category(self) -> str:
        return "devices"

    @property
    def priority(self) -> int:
        # Below the default 100 so device-specific skills sort above general utilities
        # in the settings UI.
        return 30

    def get_references_dir(self) -> Path | None:
        return Path(__file__).parent / "references"

    def get_system_prompt(self) -> str:
        return """\
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
"""

    def create_tools(self) -> list[Any]:
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, blackfly skill tools disabled")
            return []

        @tool(
            name="discover_blackfly_cameras",
            description=(
                "Scan the local subnet for FLIR Blackfly S (or any GigE Vision) cameras. "
                "Returns a list of {ip, manufacturer, model, serial, user_name} dicts. "
                "Use this when the user wants to make a Blackfly panel but hasn't given "
                "you a specific camera IP."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "bind_ip": {
                        "type": "string",
                        "description": (
                            "Local NIC IPv4 to broadcast from. Omit to auto-detect the "
                            "default-route NIC. For multi-NIC hosts on a controls subnet, "
                            "pass the IP of the NIC that reaches the cameras."
                        ),
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": "Seconds to listen for ACKs. Default 1.0.",
                        "default": 1.0,
                    },
                },
                "required": [],
            },
        )
        async def discover_blackfly_cameras(args: dict) -> dict[str, Any]:
            import asyncio

            from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result

            from lightfall_endstation_7011.observers.blackfly.discovery import discover

            bind_ip = args.get("bind_ip") or _default_bind_ip()
            timeout_s = float(args.get("timeout_s", 1.0))
            try:
                # discover() does blocking UDP I/O for ~timeout_s; off-thread it so
                # the SDK event loop stays responsive.
                results = await asyncio.to_thread(
                    discover, bind_ip=bind_ip, timeout=timeout_s
                )
            except (OSError, ValueError, RuntimeError) as e:
                logger.error("Blackfly discovery failed: {}", e)
                return mcp_error(f"Discovery failed: {e!r}")

            return mcp_result(
                [
                    {
                        "ip": d.ip,
                        "manufacturer": d.manufacturer,
                        "model": d.model,
                        "serial": d.serial,
                        "user_name": d.user_name,
                    }
                    for d in results
                ]
            )

        return [discover_blackfly_cameras]
