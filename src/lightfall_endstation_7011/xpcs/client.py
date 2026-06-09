"""Qt-side client for the xpcs-live NATS service.

Wraps lightfall's IPCService: request/reply for actions, subscriptions
(dispatched to the Qt main thread by IPCService) re-emitted as signals.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .shapes import RectShape


class XPCSClient(QObject):
    g2Updated = Signal(dict)
    sectionCompleted = Signal(dict)
    stateChanged = Signal(dict)
    errorReceived = Signal(dict)

    def __init__(self, ipc=None, parent=None) -> None:
        super().__init__(parent)
        if ipc is None:
            try:
                from lightfall.ipc.service import get_ipc_service
                ipc = get_ipc_service()
            except Exception:
                ipc = None
        self._ipc = ipc
        if self._ipc is not None:
            self._ipc.subscribe("xpcs.g2.updated", self._on_g2)
            self._ipc.subscribe("xpcs.section.completed", self._on_section)
            self._ipc.subscribe("xpcs.state", self._on_state)
            self._ipc.subscribe("xpcs.error", self._on_error)

    # --- incoming events (already on main thread via IPCService) ---

    def _on_g2(self, subject, data, reply):
        self.g2Updated.emit(data)

    def _on_section(self, subject, data, reply):
        self.sectionCompleted.emit(data)

    def _on_state(self, subject, data, reply):
        self.stateChanged.emit(data)

    def _on_error(self, subject, data, reply):
        self.errorReceived.emit(data)

    # --- actions ---

    def _request(self, subject: str, data: dict | None = None, timeout_ms: int = 2000):
        if self._ipc is None:
            return None
        return self._ipc.request(subject, data or {}, timeout_ms=timeout_ms)

    def discover(self):
        return self._request("_xpcs.discover", {})

    def enable(self):
        return self._request("xpcs.processing.enable")

    def disable(self):
        return self._request("xpcs.processing.disable")

    def reset(self):
        return self._request("xpcs.reset")

    def set_roi(self, roi_id: str, shape: RectShape):
        return self._request("xpcs.roi.set", {"roi_id": roi_id, "shape": shape.to_dict()})

    def remove_roi(self, roi_id: str):
        return self._request("xpcs.roi.remove", {"roi_id": roi_id})

    def clear_rois(self):
        return self._request("xpcs.roi.clear")

    def set_mask(self, shapes: list[RectShape]):
        return self._request("xpcs.mask.set", {"shapes": [s.to_dict() for s in shapes]})

    def clear_mask(self):
        return self._request("xpcs.mask.clear")

    def status(self):
        return self._request("xpcs.status")

    def get_sections(self, from_section: int = 0, limit: int = 20):
        return self._request("xpcs.sections.get",
                             {"from_section": from_section, "limit": limit})

    # bind/stop are fire-and-forget publishes (run docs must not block on replies)

    def bind_run(self, run_uid: str, tiled_url: str = "", tiled_api_key=None,
                 detector_prefix: str | None = None) -> None:
        if self._ipc is None:
            return
        payload = {
            "run_uid": run_uid, "tiled_url": tiled_url, "tiled_api_key": tiled_api_key}
        if detector_prefix:
            # backend infers file PV + frame shape from this prefix per run
            payload["detector_prefix"] = detector_prefix
        self._ipc.publish("xpcs.run.bind", payload)

    def run_stop(self, run_uid: str) -> None:
        if self._ipc is None:
            return
        self._ipc.publish("xpcs.run.stop", {"run_uid": run_uid})
