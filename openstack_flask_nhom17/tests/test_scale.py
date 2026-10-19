import json

import pytest
import responses
from responses import matchers

import config
from helpers import COMPUTE, LB, NETWORK, calls_summary, mutating_calls
from openstack_api import OpenStackError

LB_URL = f"{LB}/v2.0/lbaas/loadbalancers"


def vm(sid, name, ip):
    return {"id": sid, "name": name, "status": "ACTIVE",
            "addresses": {"n": [{"addr": ip, "OS-EXT-IPS:type": "fixed"}]}}


def mock_lb_context(mocked):
    mocked.add(responses.GET, LB_URL, match=[matchers.query_param_matcher({"name": config.LB_NAME})],
               json={"loadbalancers": [{"id": "lb1", "name": config.LB_NAME,
                                        "vip_subnet_id": "s1", "pools": [{"id": "p1"}]}]})
    mocked.add(responses.GET, f"{LB_URL}/lb1",
               json={"loadbalancer": {"id": "lb1", "provisioning_status": "ACTIVE"}})


@pytest.fixture
def defaults(monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_IMAGE_ID", "img1")
    monkeypatch.setattr(config, "DEFAULT_FLAVOR_ID", "flv1")


def mock_scale_out(mocked, member_status=200):
    mock_lb_context(mocked)
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/detail",
               json={"servers": [vm("a", "nhom17_vm_1", "192.168.17.101")]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks",
               json={"networks": [{"id": "n1", "name": config.NET_NAME}]})
    mocked.add(responses.POST, f"{COMPUTE}/v2.1/servers", json={"server": {"id": "srv2"}})
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv2",
               json={"server": vm("srv2", "nhom17_vm_2", "192.168.17.102")})
    mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools/p1/members", status=member_status,
               json={"member": {"id": "m2"}} if member_status < 400 else {"faultstring": "boom"})


def test_scale_out_creates_vm_and_adds_member(mocked, client, defaults):
    mock_scale_out(mocked)
    logs = []
    result = client.scale_out(logs)

    assert result == {"server_id": "srv2", "name": "nhom17_vm_2", "ip": "192.168.17.102"}
    server_body = json.loads(
        [c for c in mocked.calls if c.request.method == "POST" and c.request.url.endswith("/servers")][0].request.body
    )["server"]
    assert server_body["name"] == "nhom17_vm_2"
    assert server_body["imageRef"] == "img1"
    assert server_body["flavorRef"] == "flv1"
    assert "user_data" in server_body

    member_body = json.loads(
        [c for c in mocked.calls if c.request.url.endswith("/members")][0].request.body
    )["member"]
    assert member_body == {"address": "192.168.17.102", "protocol_port": 80, "subnet_id": "s1"}

    assert not any("floatingips" in u for _, u in mutating_calls(mocked))
    calls = calls_summary(mocked)
    index = calls.index(("POST", f"{LB}/v2.0/lbaas/pools/p1/members"))
    assert calls[index - 1] == ("GET", f"{LB_URL}/lb1")
    assert calls[index + 1] == ("GET", f"{LB_URL}/lb1")
    assert logs


def test_scale_out_requires_default_ids(mocked, client, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_IMAGE_ID", "")
    monkeypatch.setattr(config, "DEFAULT_FLAVOR_ID", "flv1")
    with pytest.raises(OpenStackError) as exc:
        client.scale_out([])
    assert "DEFAULT_IMAGE_ID" in exc.value.message
    assert mutating_calls(mocked) == []


def test_scale_out_member_failure_keeps_vm_and_reports(mocked, client, defaults):
    mock_scale_out(mocked, member_status=500)
    logs = []
    with pytest.raises(OpenStackError) as exc:
        client.scale_out(logs)
    assert "nhom17_vm_2" in exc.value.message
    assert not any(m == "DELETE" for m, _ in mutating_calls(mocked))
    assert any("giữ lại" in line for line in logs)


def mock_scale_in(mocked, servers):
    mock_lb_context(mocked)
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/detail", json={"servers": servers})
    mocked.add(responses.GET, f"{LB}/v2.0/lbaas/pools/p1/members", json={"members": [
        {"id": "m1", "address": "192.168.17.101"},
        {"id": "m2", "address": "192.168.17.102"},
    ]})
    mocked.add(responses.DELETE, f"{LB}/v2.0/lbaas/pools/p1/members/m1", status=204)
    mocked.add(responses.DELETE, f"{LB}/v2.0/lbaas/pools/p1/members/m2", status=204)
    mocked.add(responses.DELETE, f"{COMPUTE}/v2.1/servers/srv1", status=204)
    mocked.add(responses.DELETE, f"{COMPUTE}/v2.1/servers/srv2", status=204)


TWO_VMS = [vm("srv1", "nhom17_vm_1", "192.168.17.101"), vm("srv2", "nhom17_vm_2", "192.168.17.102")]


def test_scale_in_removes_member_then_last_vm(mocked, client):
    mock_scale_in(mocked, TWO_VMS)
    logs = []
    result = client.scale_in(logs)
    assert result == {"deleted": "nhom17_vm_2", "member_removed": True}
    deletes = [u for m, u in mutating_calls(mocked) if m == "DELETE"]
    assert deletes == [
        f"{LB}/v2.0/lbaas/pools/p1/members/m2",
        f"{COMPUTE}/v2.1/servers/srv2",
    ]
    assert logs


def test_scale_in_specific_server(mocked, client):
    mock_scale_in(mocked, TWO_VMS)
    result = client.scale_in([], server_id="srv1")
    assert result["deleted"] == "nhom17_vm_1"
    deletes = [u for m, u in mutating_calls(mocked) if m == "DELETE"]
    assert deletes[-1] == f"{COMPUTE}/v2.1/servers/srv1"


def test_scale_in_refuses_last_vm(mocked, client):
    mock_scale_in(mocked, [TWO_VMS[0]])
    with pytest.raises(OpenStackError) as exc:
        client.scale_in([])
    assert "1 VM" in exc.value.message
    assert mutating_calls(mocked) == []


def test_scale_in_unknown_server_id(mocked, client):
    mock_scale_in(mocked, TWO_VMS)
    with pytest.raises(OpenStackError):
        client.scale_in([], server_id="nope")


def test_scale_in_vm_not_in_pool_still_deletes_server(mocked, client):
    mock_lb_context(mocked)
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/detail", json={"servers": TWO_VMS})
    mocked.add(responses.GET, f"{LB}/v2.0/lbaas/pools/p1/members", json={"members": []})
    mocked.add(responses.DELETE, f"{COMPUTE}/v2.1/servers/srv2", status=204)
    result = client.scale_in([])
    assert result == {"deleted": "nhom17_vm_2", "member_removed": False}
