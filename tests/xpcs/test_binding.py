from unittest.mock import MagicMock

from lightfall_endstation_7011.xpcs.binding import RunBindingController


def _controller(fake_ipc):
    from lightfall_endstation_7011.xpcs.client import XPCSClient
    client = XPCSClient(ipc=fake_ipc)
    # enable() requires a backend ack before subscribing the RunEngine
    fake_ipc.replies.setdefault("xpcs.processing.enable", {"status": "ok"})
    re = MagicMock()
    re.subscribe.return_value = 7  # token
    creds = lambda: ("http://t", "key", None)
    ctl = RunBindingController(client=client, run_engine_getter=lambda: re,
                               credentials_getter=creds)
    return ctl, re, fake_ipc


def test_enable_without_backend_does_not_subscribe(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ipc.replies.pop("xpcs.processing.enable")  # backend unreachable -> None reply
    ctl.enable()
    re.subscribe.assert_not_called()
    assert not ctl.enabled


def test_enable_subscribes_and_calls_backend(fake_ipc):
    fake_ipc.replies["xpcs.processing.enable"] = {"status": "ok"}
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    re.subscribe.assert_called_once()
    assert ("xpcs.processing.enable", {}) in ipc.requests


def test_start_doc_publishes_bind(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("start", {"uid": "runX"})
    assert ipc.published == [("xpcs.run.bind",
        {"run_uid": "runX", "tiled_url": "http://t", "tiled_api_key": "key"})]


def test_stop_doc_publishes_stop(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("start", {"uid": "runX"})
    callback("stop", {"run_start": "runX"})
    assert ipc.published[-1] == ("xpcs.run.stop", {"run_uid": "runX"})


def test_disable_unsubscribes_and_calls_backend(fake_ipc):
    fake_ipc.replies["xpcs.processing.disable"] = {"status": "ok"}
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    ctl.disable()
    re.unsubscribe.assert_called_once_with(7)
    assert ("xpcs.processing.disable", {}) in ipc.requests


def test_other_docs_ignored(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("descriptor", {"uid": "d1"})
    callback("event", {"uid": "e1"})
    assert ipc.published == []


def test_start_doc_sends_detector_prefix(fake_ipc):
    from lightfall_endstation_7011.xpcs.client import XPCSClient
    fake_ipc.replies["xpcs.processing.enable"] = {"status": "ok"}
    client = XPCSClient(ipc=fake_ipc)
    re = MagicMock()
    re.subscribe.return_value = 7
    ctl = RunBindingController(
        client=client,
        run_engine_getter=lambda: re,
        credentials_getter=lambda: ("http://t", "key", None),
        detector_prefix_getter=lambda doc: "13PICAM1:",
    )
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("start", {"uid": "runX", "detectors": ["PI_MTE3"]})
    assert fake_ipc.published == [("xpcs.run.bind", {
        "run_uid": "runX", "tiled_url": "http://t", "tiled_api_key": "key",
        "detector_prefix": "13PICAM1:"})]
