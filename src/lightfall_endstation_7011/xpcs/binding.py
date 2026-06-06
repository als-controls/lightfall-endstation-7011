"""Run binding: RunEngine subscription -> xpcs.run.bind / xpcs.run.stop.

Every run is bound while enabled; the backend opens its Tiled stream
lazily on first snapshot, so non-XPCS runs stay clean. Credentials follow
the adaptive plan's pattern (TiledService url + SessionManager api key).
"""

from __future__ import annotations

from typing import Callable

from loguru import logger


def _default_run_engine():
    from lightfall.acquire.engine import get_engine
    return get_engine().RE  # reach through the engine wrapper


def _default_credentials():
    """(tiled_url, tiled_api_key, proxy_url) — mirrors
    lightfall.acquire.plans.adaptive._get_tiled_credentials()."""
    tiled_url, api_key, proxy = "", None, None
    try:
        from lightfall.core.services import ServiceRegistry
        from lightfall.services.tiled_service import TiledService
        ts = ServiceRegistry.get_instance().get(TiledService, None)
        if ts and ts.config:
            tiled_url = ts.config.url or ""
    except Exception:
        pass
    try:
        from lightfall.auth.session import SessionManager
        api_key = SessionManager.get_instance().get_api_key("tiled")
    except Exception:
        pass
    return tiled_url, api_key, proxy


class RunBindingController:
    def __init__(
        self,
        client,
        run_engine_getter: Callable = _default_run_engine,
        credentials_getter: Callable = _default_credentials,
    ) -> None:
        self._client = client
        self._get_re = run_engine_getter
        self._get_creds = credentials_getter
        self._token = None
        self._re = None
        self._bound_uid: str | None = None

    @property
    def enabled(self) -> bool:
        return self._token is not None

    def enable(self) -> None:
        if self.enabled:
            return
        reply = self._client.enable()
        if reply is None:
            # backend unreachable / request timed out — don't subscribe the
            # RE; `enabled` stays False so a retry re-attempts cleanly
            logger.warning("xpcs enable request failed or timed out; not subscribing RunEngine")
            return
        self._re = self._get_re()
        self._token = self._re.subscribe(self._on_document)

    def disable(self) -> None:
        if not self.enabled:
            return
        try:
            self._re.unsubscribe(self._token)
        finally:
            self._token = None
            self._re = None
        self._client.disable()

    def _on_document(self, name: str, doc: dict) -> None:
        try:
            if name == "start":
                uid = doc["uid"]
                tiled_url, api_key, _proxy = self._get_creds()
                self._client.bind_run(uid, tiled_url=tiled_url, tiled_api_key=api_key)
                self._bound_uid = uid
            elif name == "stop":
                uid = doc.get("run_start") or self._bound_uid
                if uid:
                    self._client.run_stop(uid)
                self._bound_uid = None
        except Exception as ex:  # never break the RunEngine document stream
            logger.exception(ex)
