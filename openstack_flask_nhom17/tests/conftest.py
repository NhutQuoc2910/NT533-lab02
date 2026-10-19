import pytest
import responses

import config
from helpers import AUTH_URL, CATALOG


@pytest.fixture(autouse=True)
def fast_config(monkeypatch):
    monkeypatch.setattr(config, "OS_APP_CRED_ID", "cred-id")
    monkeypatch.setattr(config, "OS_APP_CRED_SECRET", "cred-secret")
    monkeypatch.setattr(config, "POLL_INTERVAL", 0)
    monkeypatch.setattr(config, "POLL_TIMEOUT", 0.05)


@pytest.fixture
def mocked():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.POST,
            AUTH_URL,
            json={"token": {"catalog": CATALOG}},
            headers={"X-Subject-Token": "tok-1"},
        )
        yield rsps


@pytest.fixture
def client(mocked):
    from openstack_api import OpenStackClient
    return OpenStackClient()
