import pytest


class FakeIPC:
    """Duck-type of lightfall.ipc.service.IPCService for unit tests."""

    def __init__(self):
        self.published = []      # (subject, data)
        self.requests = []       # (subject, data)
        self.replies = {}        # subject -> dict to return from request()
        self.subscriptions = {}  # subject -> callback

    def publish(self, subject, data):
        self.published.append((subject, data))

    def request(self, subject, data, timeout_ms=1000):
        self.requests.append((subject, data))
        return self.replies.get(subject)

    def subscribe(self, subject, callback, *, main_thread=True):
        self.subscriptions[subject] = callback

    def emit(self, subject, data):
        """Test hook: simulate an incoming NATS message."""
        self.subscriptions[subject](subject, data, None)


@pytest.fixture
def fake_ipc():
    return FakeIPC()
