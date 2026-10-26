import json

import pytest
import responses
from responses import matchers

import config
from helpers import COMPUTE, LB, NETWORK, calls_summary
from openstack_api import OpenStackError

LB_URL = f"{LB}/v2.0/lbaas/loadbalancers"


def lb_json(status="ACTIVE"):
    return {
        "loadbalancer": {
            "id": "lb1",
            "provisioning_status": status,
            "vip_port_id": "vp1",
            "vip_address": "192.168.17.50",
            "vip_subnet_id": "s1",
        }
    }


def test_wait_for_lb_active_immediately(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json())
    assert client.wait_for_lb_active("lb1")["id"] == "lb1"


def test_wait_for_lb_active_after_pending(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json("PENDING_UPDATE"))
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json("ACTIVE"))
    assert client.wait_for_lb_active("lb1")["provisioning_status"] == "ACTIVE"


def test_wait_for_lb_active_error_status_raises(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json("ERROR"))
    with pytest.raises(OpenStackError) as exc:
        client.wait_for_lb_active("lb1")
    assert "ERROR" in exc.value.message
    assert "lb1" in exc.value.message


def test_wait_for_lb_active_timeout(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json("PENDING_UPDATE"))
    with pytest.raises(OpenStackError) as exc:
        client.wait_for_lb_active("lb1")
    assert "Quá thời gian" in exc.value.message
    assert "lb1" in exc.value.message
    assert "PENDING_UPDATE" in exc.value.message


def mock_create_flow(mocked, ovn=False):
    mocked.add(responses.GET, LB_URL, match=[matchers.query_param_matcher({"name": config.LB_NAME})],
               json={"loadbalancers": []})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks",
               match=[matchers.query_param_matcher({"name": config.NET_NAME})],
               json={"networks": [{"id": "n1", "name": config.NET_NAME}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks",
               match=[matchers.query_param_matcher({"router:external": "true"})],
               json={"networks": [{"id": "ext1"}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/subnets",
               json={"subnets": [{"id": "s1", "name": config.SUBNET_NAME}]})
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/detail", json={"servers": [
        {"id": "a", "name": "nhom17_vm_1", "status": "ACTIVE",
         "addresses": {"n": [{"addr": "192.168.17.101", "OS-EXT-IPS:type": "fixed"}]}},
        {"id": "b", "name": "nhom17_vm_2", "status": "ACTIVE",
         "addresses": {"n": [{"addr": "192.168.17.102", "OS-EXT-IPS:type": "fixed"}]}},
        {"id": "c", "name": "unrelated", "status": "ACTIVE",
         "addresses": {"n": [{"addr": "192.168.17.150", "OS-EXT-IPS:type": "fixed"}]}},
    ]})
    mocked.add(responses.POST, LB_URL, json=lb_json("PENDING_CREATE"))
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json())
    if ovn:
        mocked.add(
            responses.POST, f"{LB}/v2.0/lbaas/listeners",
            status=501, json={"NeutronError": {"message": "Provider 'ovn' does not support HTTP protocol"}},
        )
        mocked.add(responses.POST, f"{LB}/v2.0/lbaas/listeners", json={"listener": {"id": "l_tcp"}})
        mocked.add(
            responses.POST, f"{LB}/v2.0/lbaas/pools",
            status=501, json={"NeutronError": {"message": "Provider 'ovn' does not support ROUND_ROBIN algorithm"}},
        )
        mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools", json={"pool": {"id": "p_ovn"}})
        mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools/p_ovn/members", json={"member": {"id": "m1"}})
    else:
        mocked.add(responses.POST, f"{LB}/v2.0/lbaas/listeners", json={"listener": {"id": "l1"}})
        mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools", json={"pool": {"id": "p1"}})
        mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools/p1/members", json={"member": {"id": "m1"}})
    mocked.add(responses.POST, f"{NETWORK}/v2.0/floatingips",
               json={"floatingip": {"floating_ip_address": "10.0.0.5"}})



def bodies_for(mocked, suffix):
    return [json.loads(c.request.body) for c in mocked.calls
            if c.request.method == "POST" and c.request.url.endswith(suffix)]


def test_create_load_balancer_full_flow(mocked, client):
    mock_create_flow(mocked)
    logs = []
    result = client.create_load_balancer(logs)

    assert result == {
        "lb_id": "lb1",
        "pool_id": "p1",
        "vip_address": "192.168.17.50",
        "floating_ip": "10.0.0.5",
        "members": ["192.168.17.101", "192.168.17.102"],
    }
    assert bodies_for(mocked, "/loadbalancers")[0]["loadbalancer"]["vip_subnet_id"] == "s1"
    listener = bodies_for(mocked, "/listeners")[0]["listener"]
    assert (listener["protocol"], listener["protocol_port"], listener["loadbalancer_id"]) == ("HTTP", 80, "lb1")
    pool = bodies_for(mocked, "/pools")[0]["pool"]
    assert (pool["protocol"], pool["lb_algorithm"], pool["listener_id"]) == ("HTTP", "ROUND_ROBIN", "l1")
    members = bodies_for(mocked, "/members")
    assert [m["member"]["address"] for m in members] == ["192.168.17.101", "192.168.17.102"]
    assert all(m["member"]["protocol_port"] == 80 and m["member"]["subnet_id"] == "s1" for m in members)
    assert bodies_for(mocked, "/floatingips")[0]["floatingip"] == {
        "floating_network_id": "ext1", "port_id": "vp1"
    }
    assert logs


def test_lb_steps_wait_for_active_before_each_create(mocked, client):
    mock_create_flow(mocked)
    client.create_load_balancer([])
    calls = calls_summary(mocked)
    for target in (f"{LB}/v2.0/lbaas/listeners", f"{LB}/v2.0/lbaas/pools",
                   f"{LB}/v2.0/lbaas/pools/p1/members"):
        index = calls.index(("POST", target))
        assert calls[index - 1] == ("GET", f"{LB_URL}/lb1")


def test_create_load_balancer_refuses_when_exists(mocked, client):
    mocked.add(responses.GET, LB_URL, json={"loadbalancers": [{"id": "lb1", "name": config.LB_NAME}]})
    with pytest.raises(OpenStackError) as exc:
        client.create_load_balancer([])
    assert "đã tồn tại" in exc.value.message


def test_add_pool_member_waits_before_and_after(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json())
    mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools/p1/members", json={"member": {"id": "m9"}})
    member = client.add_pool_member("lb1", "p1", "192.168.17.103", "s1", [])
    assert member["id"] == "m9"
    calls = calls_summary(mocked)
    assert [c[0] for c in calls] == ["GET", "POST", "GET"]


def test_remove_pool_member_waits_before_and_after(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json())
    mocked.add(responses.DELETE, f"{LB}/v2.0/lbaas/pools/p1/members/m9", status=204)
    client.remove_pool_member("lb1", "p1", "m9", [])
    assert [c[0] for c in calls_summary(mocked)] == ["GET", "DELETE", "GET"]


def test_create_load_balancer_ovn_fallback_to_tcp_and_source_ip_port(mocked, client):
    mock_create_flow(mocked, ovn=True)
    logs = []
    res = client.create_load_balancer(logs)
    assert res["lb_id"] == "lb1"
    assert res["pool_id"] == "p_ovn"
    assert any("TCP" in l for l in logs)
    assert any("SOURCE_IP_PORT" in l for l in logs)


def test_list_loadbalancers(mocked, client):
    mocked.add(responses.GET, LB_URL, json={"loadbalancers": [
        {
            "id": "lb1",
            "name": "nhom17_lb",
            "provisioning_status": "ACTIVE",
            "operating_status": "ONLINE",
            "vip_address": "192.168.17.50",
            "vip_port_id": "vp1",
            "provider": "ovn",
            "listeners": [{"id": "l1"}],
            "pools": [{"id": "p1"}],
        }
    ]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/floatingips", json={"floatingips": [
        {"id": "fip1", "floating_ip_address": "10.0.0.5", "port_id": "vp1"}
    ]})
    items = client.list_loadbalancers()
    assert len(items) == 1
    assert items[0]["id"] == "lb1"
    assert items[0]["name"] == "nhom17_lb"
    assert items[0]["floating_ip"] == "10.0.0.5"
    assert items[0]["provisioning_status"] == "ACTIVE"


def test_delete_load_balancer_deletes_fip_and_lb(mocked, client):
    mocked.add(responses.GET, f"{LB_URL}/lb1", json={"loadbalancer": {
        "id": "lb1", "provisioning_status": "ACTIVE", "vip_port_id": "vp1"
    }})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/floatingips", json={"floatingips": [
        {"id": "fip1", "floating_ip_address": "10.0.0.5", "port_id": "vp1"}
    ]})
    mocked.add(responses.DELETE, f"{NETWORK}/v2.0/floatingips/fip1", status=204)
    mocked.add(responses.DELETE, f"{LB_URL}/lb1", match=[matchers.query_param_matcher({"cascade": "true"})], status=204)

    logs = []
    client.delete_load_balancer("lb1", cascade=True, logs=logs)
    calls = calls_summary(mocked)
    assert ("DELETE", f"{NETWORK}/v2.0/floatingips/fip1") in calls
    assert ("DELETE", f"{LB_URL}/lb1") in calls
    assert any("Đã gửi yêu cầu xóa Load Balancer" in l for l in logs)


def test_create_load_balancer_custom_name(mocked, client):
    custom_name = "test_custom_lb"
    mocked.add(responses.GET, LB_URL, match=[matchers.query_param_matcher({"name": custom_name})],
               json={"loadbalancers": []})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks",
               match=[matchers.query_param_matcher({"name": config.NET_NAME})],
               json={"networks": [{"id": "n1", "name": config.NET_NAME}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks",
               match=[matchers.query_param_matcher({"router:external": "true"})],
               json={"networks": [{"id": "ext1"}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/subnets",
               json={"subnets": [{"id": "s1", "name": config.SUBNET_NAME}]})
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/servers/detail", json={"servers": []})
    mocked.add(responses.POST, LB_URL, json=lb_json())
    mocked.add(responses.GET, f"{LB_URL}/lb1", json=lb_json())
    mocked.add(responses.POST, f"{LB}/v2.0/lbaas/listeners", json={"listener": {"id": "l_custom"}})
    mocked.add(responses.POST, f"{LB}/v2.0/lbaas/pools", json={"pool": {"id": "p_custom"}})
    mocked.add(responses.POST, f"{NETWORK}/v2.0/floatingips",
               json={"floatingip": {"floating_ip_address": "10.0.0.9"}})

    logs = []
    res = client.create_load_balancer(logs, name=custom_name)
    assert res["lb_id"] == "lb1"
    create_lb_body = bodies_for(mocked, "/loadbalancers")[0]["loadbalancer"]
    assert create_lb_body["name"] == custom_name



