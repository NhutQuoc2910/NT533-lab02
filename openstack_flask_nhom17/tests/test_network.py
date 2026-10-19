import json

import pytest
import responses

from helpers import COMPUTE, IMAGE, NETWORK, calls_summary
from openstack_api import OpenStackError


def last_body(mocked):
    return json.loads(mocked.calls[-1].request.body)


def test_list_flavors(mocked, client):
    mocked.add(
        responses.GET,
        f"{COMPUTE}/v2.1/flavors/detail",
        json={"flavors": [{"id": "f1", "name": "m1.small", "vcpus": 1, "ram": 2048, "disk": 20}]},
    )
    assert client.list_flavors()[0]["name"] == "m1.small"


def test_list_images(mocked, client):
    mocked.add(
        responses.GET,
        f"{IMAGE}/v2/images",
        json={"images": [{"id": "i1", "name": "ubuntu", "status": "active", "size": 1048576}]},
    )
    assert client.list_images()[0]["status"] == "active"


def test_create_network_payload(mocked, client):
    mocked.add(responses.POST, f"{NETWORK}/v2.0/networks", json={"network": {"id": "n1", "name": "nhom17_net"}})
    net = client.create_network("nhom17_net")
    assert net["id"] == "n1"
    assert last_body(mocked) == {"network": {"name": "nhom17_net", "admin_state_up": True}}


def test_create_subnet_payload(mocked, client):
    mocked.add(responses.POST, f"{NETWORK}/v2.0/subnets", json={"subnet": {"id": "s1"}})
    client.create_subnet(
        "n1",
        "nhom17_subnet",
        "192.168.17.0/24",
        gateway_ip="192.168.17.1",
        allocation_pools=[{"start": "192.168.17.101", "end": "192.168.17.200"}],
        dns_nameservers=["8.8.8.8", "1.1.1.1"],
    )
    assert last_body(mocked) == {
        "subnet": {
            "network_id": "n1",
            "name": "nhom17_subnet",
            "ip_version": 4,
            "cidr": "192.168.17.0/24",
            "enable_dhcp": True,
            "gateway_ip": "192.168.17.1",
            "allocation_pools": [{"start": "192.168.17.101", "end": "192.168.17.200"}],
            "dns_nameservers": ["8.8.8.8", "1.1.1.1"],
        }
    }


def test_create_subnet_omits_optional_fields(mocked, client):
    mocked.add(responses.POST, f"{NETWORK}/v2.0/subnets", json={"subnet": {"id": "s1"}})
    client.create_subnet("n1", "s", "10.0.0.0/24")
    assert set(last_body(mocked)["subnet"]) == {"network_id", "name", "ip_version", "cidr", "enable_dhcp"}


def test_delete_network_removes_subnets_first(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/subnets", json={"subnets": [{"id": "s1"}]})
    mocked.add(responses.DELETE, f"{NETWORK}/v2.0/subnets/s1", status=204)
    mocked.add(responses.DELETE, f"{NETWORK}/v2.0/networks/n1", status=204)
    client.delete_network("n1")
    assert calls_summary(mocked) == [
        ("GET", f"{NETWORK}/v2.0/subnets"),
        ("DELETE", f"{NETWORK}/v2.0/subnets/s1"),
        ("DELETE", f"{NETWORK}/v2.0/networks/n1"),
    ]


def test_get_external_network_id(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": [{"id": "ext1"}]})
    assert client.get_external_network_id() == "ext1"
    assert "router%3Aexternal=true" in mocked.calls[-1].request.url


def test_get_external_network_missing_raises(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": []})
    with pytest.raises(OpenStackError):
        client.get_external_network_id()


def test_create_router_with_gateway(mocked, client):
    mocked.add(responses.POST, f"{NETWORK}/v2.0/routers", json={"router": {"id": "r1"}})
    client.create_router("nhom17_router", external_network_id="ext1")
    assert last_body(mocked) == {
        "router": {
            "name": "nhom17_router",
            "admin_state_up": True,
            "external_gateway_info": {"network_id": "ext1"},
        }
    }


def test_create_router_without_gateway(mocked, client):
    mocked.add(responses.POST, f"{NETWORK}/v2.0/routers", json={"router": {"id": "r1"}})
    client.create_router("r")
    assert "external_gateway_info" not in last_body(mocked)["router"]


def test_add_and_remove_router_interface(mocked, client):
    mocked.add(responses.PUT, f"{NETWORK}/v2.0/routers/r1/add_router_interface", json={})
    mocked.add(responses.PUT, f"{NETWORK}/v2.0/routers/r1/remove_router_interface", json={})
    client.add_router_interface("r1", "s1")
    assert last_body(mocked) == {"subnet_id": "s1"}
    client.remove_router_interface("r1", subnet_id="s1")
    assert last_body(mocked) == {"subnet_id": "s1"}
    client.remove_router_interface("r1", port_id="p1")
    assert last_body(mocked) == {"port_id": "p1"}


def test_delete_router_detaches_interfaces_first(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/ports", json={"ports": [{"id": "p1"}]})
    mocked.add(responses.PUT, f"{NETWORK}/v2.0/routers/r1/remove_router_interface", json={})
    mocked.add(responses.DELETE, f"{NETWORK}/v2.0/routers/r1", status=204)
    client.delete_router("r1")
    assert calls_summary(mocked) == [
        ("GET", f"{NETWORK}/v2.0/ports"),
        ("PUT", f"{NETWORK}/v2.0/routers/r1/remove_router_interface"),
        ("DELETE", f"{NETWORK}/v2.0/routers/r1"),
    ]
