import base64
import json

import pytest
import responses

from helpers import COMPUTE, NETWORK
from openstack_api import OpenStackError, encode_user_data, vm_index


def last_body(mocked):
    return json.loads(mocked.calls[-1].request.body)


def server_json(sid="srv1", name="nhom17_vm_1", status="ACTIVE", fixed="192.168.17.101", floating=None):
    addrs = []
    if fixed:
        addrs.append({"addr": fixed, "version": 4, "OS-EXT-IPS:type": "fixed"})
    if floating:
        addrs.append({"addr": floating, "version": 4, "OS-EXT-IPS:type": "floating"})
    return {"id": sid, "name": name, "status": status, "addresses": {"nhom17_net": addrs}}


def test_vm_index():
    assert vm_index("nhom17_vm_3") == 3
    assert vm_index("nhom17_vm_") is None
    assert vm_index("other") is None


def test_list_servers_simplified(mocked, client):
    mocked.add(
        responses.GET,
        f"{COMPUTE}/v2.1/servers/detail",
        json={"servers": [server_json(floating="10.0.0.9")]},
    )
    assert client.list_servers() == [
        {
            "id": "srv1",
            "name": "nhom17_vm_1",
            "status": "ACTIVE",
            "fixed_ips": ["192.168.17.101"],
            "floating_ips": ["10.0.0.9"],
        }
    ]


def test_create_server_payload_without_key_name(mocked, client):
    mocked.add(responses.POST, f"{COMPUTE}/v2.1/servers", json={"server": {"id": "srv1"}})
    client.create_server("nhom17_vm_1", "img1", "flv1", "n1")
    server = last_body(mocked)["server"]
    assert server == {
        "name": "nhom17_vm_1",
        "imageRef": "img1",
        "flavorRef": "flv1",
        "networks": [{"uuid": "n1"}],
    }


def test_create_server_with_user_data_and_key_name(mocked, client):
    mocked.add(responses.POST, f"{COMPUTE}/v2.1/servers", json={"server": {"id": "srv1"}})
    client.create_server("v", "img1", "flv1", "n1", user_data="QUJD", key_name="k1")
    server = last_body(mocked)["server"]
    assert server["user_data"] == "QUJD"
    assert server["key_name"] == "k1"


def test_wait_for_server_active_returns_ip(mocked, client):
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv1", json={"server": server_json(status="BUILD", fixed=None)})
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv1", json={"server": server_json()})
    assert client.wait_for_server_active("srv1") == "192.168.17.101"


def test_wait_for_server_active_error_raises(mocked, client):
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv1", json={"server": server_json(status="ERROR")})
    with pytest.raises(OpenStackError) as exc:
        client.wait_for_server_active("srv1")
    assert "ERROR" in exc.value.message


def test_wait_for_server_active_timeout(mocked, client):
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv1", json={"server": server_json(status="BUILD", fixed=None)})
    with pytest.raises(OpenStackError) as exc:
        client.wait_for_server_active("srv1")
    assert "Quá thời gian" in exc.value.message


def test_create_vm_web_sends_base64_user_data(mocked, client):
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/networks",
        json={"networks": [{"id": "n1", "name": "nhom17_net"}]},
    )
    mocked.add(responses.POST, f"{COMPUTE}/v2.1/servers", json={"server": {"id": "srv1"}})
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv1", json={"server": server_json()})
    logs = []
    vm = client.create_vm("nhom17_vm_1", "img1", "flv1", web=True, logs=logs)
    assert vm == {"server_id": "srv1", "name": "nhom17_vm_1", "ip": "192.168.17.101"}
    post_call = [c for c in mocked.calls if c.request.method == "POST" and c.request.url.endswith("/servers")][0]
    user_data = json.loads(post_call.request.body)["server"]["user_data"]
    script = base64.b64decode(user_data).decode("utf-8")
    assert "nginx" in script
    assert "Nhóm 17 - NT533" in script
    assert "hostname -I" in script
    assert "/var/www/html/index.html" in script
    assert logs


def test_create_vm_plain_has_no_user_data(mocked, client):
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/networks",
        json={"networks": [{"id": "n1", "name": "nhom17_net"}]},
    )
    mocked.add(responses.POST, f"{COMPUTE}/v2.1/servers", json={"server": {"id": "srv1"}})
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/srv1", json={"server": server_json()})
    client.create_vm("x", "img1", "flv1")
    post_call = [c for c in mocked.calls if c.request.method == "POST" and c.request.url.endswith("/servers")][0]
    assert "user_data" not in json.loads(post_call.request.body)["server"]


def test_create_vm_requires_network(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": []})
    with pytest.raises(OpenStackError) as exc:
        client.create_vm("x", "img1", "flv1")
    assert "nhom17_net" in exc.value.message


def test_delete_server(mocked, client):
    mocked.add(responses.DELETE, f"{COMPUTE}/v2.1/servers/srv1", status=204)
    client.delete_server("srv1")
    assert mocked.calls[-1].request.method == "DELETE"


def test_next_vm_name(mocked, client):
    mocked.add(
        responses.GET,
        f"{COMPUTE}/v2.1/servers/detail",
        json={"servers": [server_json("a", "nhom17_vm_1"), server_json("c", "nhom17_vm_3"), server_json("o", "other")]},
    )
    assert client.next_vm_name() == "nhom17_vm_4"


def test_next_vm_name_when_empty(mocked, client):
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/detail", json={"servers": []})
    assert client.next_vm_name() == "nhom17_vm_1"


def test_assign_floating_ip(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/ports", json={"ports": [{"id": "p1"}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": [{"id": "ext1"}]})
    mocked.add(
        responses.POST, f"{NETWORK}/v2.0/floatingips",
        json={"floatingip": {"floating_ip_address": "10.0.0.5"}},
    )
    logs = []
    result = client.assign_floating_ip("srv1", logs)
    assert result == {"server_id": "srv1", "floating_ip": "10.0.0.5"}
    assert last_body(mocked) == {"floatingip": {"floating_network_id": "ext1", "port_id": "p1"}}
    assert logs


def test_assign_floating_ip_without_port_raises(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/ports", json={"ports": []})
    with pytest.raises(OpenStackError):
        client.assign_floating_ip("srv1")


def test_encode_user_data_roundtrip():
    assert base64.b64decode(encode_user_data("héllo")).decode("utf-8") == "héllo"
