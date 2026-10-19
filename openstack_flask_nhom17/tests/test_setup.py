import json

import responses
from responses import matchers

import config
from helpers import NETWORK, mutating_calls

EXISTING_RULES = [
    {"direction": "ingress", "ethertype": "IPv4", "protocol": "tcp",
     "port_range_min": 80, "port_range_max": 80, "remote_ip_prefix": "0.0.0.0/0", "remote_group_id": None},
    {"direction": "ingress", "ethertype": "IPv4", "protocol": "tcp",
     "port_range_min": 22, "port_range_max": 22, "remote_ip_prefix": "0.0.0.0/0", "remote_group_id": None},
    {"direction": "ingress", "ethertype": "IPv4", "protocol": "icmp",
     "port_range_min": None, "port_range_max": None, "remote_ip_prefix": "0.0.0.0/0", "remote_group_id": None},
]


def mock_common(mocked, net=None, subnet=None, router=None, router_ports=None, rules=None):
    net_list = [net] if net else []
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/networks",
        match=[matchers.query_param_matcher({"name": config.NET_NAME})],
        json={"networks": net_list},
    )
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/networks",
        match=[matchers.query_param_matcher({"router:external": "true"})],
        json={"networks": [{"id": "ext1"}]},
    )
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/subnets",
        json={"subnets": [subnet] if subnet else []},
    )
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/routers",
        json={"routers": [router] if router else []},
    )
    mocked.add(responses.GET, f"{NETWORK}/v2.0/ports", json={"ports": router_ports or []})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/security-groups", json={"security_groups": [{"id": "sg1"}]})
    mocked.add(
        responses.GET, f"{NETWORK}/v2.0/security-group-rules",
        json={"security_group_rules": rules or []},
    )


def test_setup_creates_everything_in_order(mocked, client):
    mock_common(mocked)
    mocked.add(responses.POST, f"{NETWORK}/v2.0/networks", json={"network": {"id": "n1", "name": config.NET_NAME}})
    mocked.add(responses.POST, f"{NETWORK}/v2.0/subnets", json={"subnet": {"id": "s1", "name": config.SUBNET_NAME}})
    mocked.add(responses.POST, f"{NETWORK}/v2.0/routers", json={"router": {"id": "r1", "name": config.ROUTER_NAME}})
    mocked.add(responses.PUT, f"{NETWORK}/v2.0/routers/r1/add_router_interface", json={})
    mocked.add(responses.POST, f"{NETWORK}/v2.0/security-group-rules", json={"security_group_rule": {}})

    logs = []
    result = client.setup_network_1click(logs)

    assert result == {"network_id": "n1", "subnet_id": "s1", "router_id": "r1"}
    assert logs
    posts = mutating_calls(mocked)
    assert [u.rsplit("/", 1)[-1] for m, u in posts[:3]] == ["networks", "subnets", "routers"]
    assert posts[3] == ("PUT", f"{NETWORK}/v2.0/routers/r1/add_router_interface")

    bodies = [json.loads(c.request.body) for c in mocked.calls if c.request.method == "POST"
              and c.request.url.endswith("/subnets")]
    subnet = bodies[0]["subnet"]
    assert subnet["cidr"] == "192.168.17.0/24"
    assert subnet["gateway_ip"] == "192.168.17.1"
    assert subnet["allocation_pools"] == [{"start": "192.168.17.101", "end": "192.168.17.200"}]
    assert subnet["dns_nameservers"] == ["8.8.8.8", "1.1.1.1"]
    assert subnet["enable_dhcp"] is True

    router_body = [json.loads(c.request.body) for c in mocked.calls if c.request.method == "POST"
                   and c.request.url.endswith("/routers")][0]
    assert router_body["router"]["external_gateway_info"] == {"network_id": "ext1"}

    rule_bodies = [json.loads(c.request.body)["security_group_rule"] for c in mocked.calls
                   if c.request.method == "POST" and c.request.url.endswith("/security-group-rules")]
    assert {(r["protocol"], r.get("port_range_min")) for r in rule_bodies} == {
        ("tcp", 80), ("tcp", 22), ("icmp", None)
    }
    assert all(r["direction"] == "ingress" and r["security_group_id"] == "sg1" for r in rule_bodies)


def test_setup_is_idempotent_when_everything_exists(mocked, client):
    mock_common(
        mocked,
        net={"id": "n1", "name": config.NET_NAME},
        subnet={"id": "s1", "name": config.SUBNET_NAME},
        router={"id": "r1", "name": config.ROUTER_NAME},
        router_ports=[{"id": "p1", "fixed_ips": [{"subnet_id": "s1"}]}],
        rules=EXISTING_RULES,
    )
    logs = []
    result = client.setup_network_1click(logs)
    assert result == {"network_id": "n1", "subnet_id": "s1", "router_id": "r1"}
    assert mutating_calls(mocked) == []
    assert any("đã tồn tại" in line for line in logs)


def test_security_group_409_is_treated_as_existing(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/security-groups", json={"security_groups": [{"id": "sg1"}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/security-group-rules", json={"security_group_rules": []})
    mocked.add(
        responses.POST, f"{NETWORK}/v2.0/security-group-rules", status=409,
        json={"NeutronError": {"message": "Security group rule already exists"}},
    )
    logs = []
    client.ensure_default_sg_rules(logs)
    assert len(logs) == 3


def test_rule_with_remote_group_is_not_counted_as_open_rule(mocked, client):
    rule = dict(EXISTING_RULES[0], remote_ip_prefix=None, remote_group_id="sgX")
    mocked.add(responses.GET, f"{NETWORK}/v2.0/security-groups", json={"security_groups": [{"id": "sg1"}]})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/security-group-rules", json={"security_group_rules": [rule]})
    mocked.add(responses.POST, f"{NETWORK}/v2.0/security-group-rules", json={"security_group_rule": {}})
    client.ensure_default_sg_rules([])
    created = [c for c in mocked.calls if c.request.method == "POST"
               and c.request.url.endswith("/security-group-rules")]
    assert len(created) == 3
