"""A stale renewal failure cannot be attributed to a newly claimed job."""
import threading
from types import SimpleNamespace
import pytest
from tracebridge.local_runner import GatewayError, LocalRunner

@pytest.mark.parametrize("replace_job", [False, True])
def test_heartbeat_failure_is_scoped_to_the_job_it_renewed(replace_job):
    runner = LocalRunner.__new__(LocalRunner)
    runner.active = {"job_id": "previous", "epoch": 1}
    runner.lease_failure = None
    runner.stop = threading.Event()
    stop_event = threading.Event()
    renewed = []
    def request(method, path, payload):
        if path.endswith("/renew"):
            renewed.append((path, payload))
            if replace_job:
                runner.active = {"job_id": "successor", "epoch": 1}
            stop_event.set()
            raise GatewayError("Lease has completed", 409)
        return {}
    runner.api = SimpleNamespace(request=request)
    runner._heartbeat_loop(stop_event)
    assert renewed == [("/v1/runner/jobs/previous/renew", {"epoch": 1})]
    assert runner.lease_failure == (None if replace_job else "GatewayError")
