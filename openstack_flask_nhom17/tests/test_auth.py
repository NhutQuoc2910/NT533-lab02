import json

import pytest
import responses

from helpers import AUTH_URL, COMPUTE, LB, NETWORK
from openstack_api import OpenStackClient, OpenStackError, normalize_base_url


def test_authenticate_uses_application_credential_without_scope(client, mocked):
    client.authenticate()
    body = json.loads(mocked.calls[0].request.body)
    identity = body["auth"]["identity"]
    assert identity["methods"] == ["application_credential"]
    assert identity["application_credential"] == {"id": "cred-id", "secret": "cred-secret"}
    assert "scope" not in body["auth"]
    assert client.token == "tok-1"


def test_catalog_extracts_public_endpoints(client):
    client.authenticate()
    assert client.endpoints["compute"] == COMPUTE
    assert client.endpoints["network"] == NETWORK
    assert client.endpoints["load-balancer"] == LB


def test_authenticate_failure_raises(mocked):
    mocked.replace(responses.POST, AUTH_URL, status=401, json={"error": {"message": "bad credential"}})
    with pytest.raises(OpenStackError) as exc:
        OpenStackClient().authenticate()
    assert exc.value.status == 401
    assert "bad credential" in exc.value.message


def test_normalize_base_url():
    assert normalize_base_url("https://h/v2.1") == "https://h"
    assert normalize_base_url("https://h:9696/") == "https://h:9696"
    assert normalize_base_url("https://h/v2.1/abc123") == "https://h"
    assert normalize_base_url("https://h/load-balancer") == "https://h/load-balancer"
    assert normalize_base_url("https://v2.example.com") == "https://v2.example.com"


def test_request_refreshes_token_on_401(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", status=401, json={})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": []})
    assert client.list_networks() == []
    auth_calls = [c for c in mocked.calls if c.request.url == AUTH_URL]
    assert len(auth_calls) == 2


def test_request_sends_token_header(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": []})
    client.list_networks()
    assert mocked.calls[-1].request.headers["X-Auth-Token"] == "tok-1"


def test_error_message_extracted(mocked, client):
    mocked.add(
        responses.GET,
        f"{NETWORK}/v2.0/networks",
        status=409,
        json={"NeutronError": {"message": "quota exceeded"}},
    )
    with pytest.raises(OpenStackError) as exc:
        client.list_networks()
    assert exc.value.status == 409
    assert "quota exceeded" in exc.value.message


def test_missing_octavia_endpoint_raises(client):
    client.token = "t"
    client.endpoints = {}
    with pytest.raises(OpenStackError) as exc:
        client._request("GET", "load-balancer", "/v2.0/lbaas/loadbalancers")
    assert "load-balancer" in exc.value.message


def test_fallback_endpoint_used_when_catalog_lacks_service(mocked, client):
    client.token = "t"
    client.endpoints = {}
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/flavors/detail", json={"flavors": []})
    assert client.list_flavors() == []
