from unittest.mock import MagicMock

import pytest

import app as app_module
from openstack_api import OpenStackError


@pytest.fixture
def fake(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "client", fake_client)
    return fake_client


@pytest.fixture
def http():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_envelope_on_success(fake, http):
    fake.list_flavors.return_value = [{"id": "f1", "name": "m1"}]
    res = http.get("/api/flavors")
    body = res.get_json()
    assert res.status_code == 200
    assert body["success"] is True
    assert body["data"] == {"items": [{"id": "f1", "name": "m1"}]}
    assert set(body) == {"success", "message", "logs", "data"}


def test_openstack_error_keeps_logs(fake, http):
    def fail(logs):
        logs.append("bước 1 xong")
        raise OpenStackError("LB ERROR", 500)

    fake.scale_out.side_effect = fail
    res = http.post("/api/scale-out")
    body = res.get_json()
    assert res.status_code == 200
    assert body["success"] is False
    assert body["message"] == "LB ERROR"
    assert body["logs"] == ["bước 1 xong"]


def test_unexpected_error_returns_500(fake, http):
    fake.list_images.side_effect = ValueError("boom")
    res = http.get("/api/images")
    assert res.status_code == 500
    assert res.get_json()["success"] is False


def test_missing_field_returns_400(fake, http):
    res = http.post("/api/networks", json={})
    assert res.status_code == 400
    assert res.get_json()["success"] is False
    fake.create_network.assert_not_called()


def test_create_network(fake, http):
    fake.create_network.return_value = {"id": "n1"}
    res = http.post("/api/networks", json={"name": "x"})
    assert res.get_json()["data"] == {"item": {"id": "n1"}}
    fake.create_network.assert_called_once_with("x")


def test_create_subnet(fake, http):
    fake.create_subnet.return_value = {"id": "s1"}
    res = http.post("/api/subnets", json={"network_id": "n1", "name": "s", "cidr": "10.0.0.0/24", "gateway_ip": "10.0.0.1"})
    assert res.get_json()["success"] is True
    fake.create_subnet.assert_called_once_with("n1", "s", "10.0.0.0/24", gateway_ip="10.0.0.1")


def test_create_router_with_external_gateway_by_default(fake, http):
    fake.get_external_network_id.return_value = "ext1"
    fake.create_router.return_value = {"id": "r1"}
    http.post("/api/routers", json={"name": "r"})
    fake.create_router.assert_called_once_with("r", external_network_id="ext1")


def test_create_router_without_gateway(fake, http):
    fake.create_router.return_value = {"id": "r1"}
    http.post("/api/routers", json={"name": "r", "external": False})
    fake.create_router.assert_called_once_with("r", external_network_id=None)


def test_router_interface_attach_and_detach(fake, http):
    assert http.post("/api/routers/r1/interfaces", json={"subnet_id": "s1"}).get_json()["success"]
    fake.add_router_interface.assert_called_once_with("r1", "s1")
    assert http.delete("/api/routers/r1/interfaces", json={"subnet_id": "s1"}).get_json()["success"]
    fake.remove_router_interface.assert_called_once_with("r1", subnet_id="s1")


def test_create_plain_vm_uses_next_name(fake, http):
    fake.next_vm_name.return_value = "nhom17_vm_2"
    fake.create_vm.return_value = {"server_id": "x", "name": "nhom17_vm_2", "ip": "1.1.1.1"}
    res = http.post("/api/servers", json={"image_id": "i", "flavor_id": "f"})
    assert res.get_json()["success"] is True
    args, kwargs = fake.create_vm.call_args
    assert args[:3] == ("nhom17_vm_2", "i", "f")
    assert kwargs["web"] is False


def test_create_web_vm(fake, http):
    fake.create_vm.return_value = {"server_id": "x", "name": "v", "ip": "1.1.1.1"}
    http.post("/api/servers/web", json={"name": "v", "image_id": "i", "flavor_id": "f"})
    args, kwargs = fake.create_vm.call_args
    assert args[:3] == ("v", "i", "f")
    assert kwargs["web"] is True


def test_create_vm_requires_image_and_flavor(fake, http):
    assert http.post("/api/servers", json={"name": "v"}).status_code == 400


def test_floating_ip_route(fake, http):
    fake.assign_floating_ip.return_value = {"server_id": "s", "floating_ip": "10.0.0.5"}
    res = http.post("/api/servers/s/floating-ip")
    assert res.get_json()["data"]["floating_ip"] == "10.0.0.5"


def test_delete_routes(fake, http):
    assert http.delete("/api/networks/n1").get_json()["success"]
    fake.delete_network.assert_called_once_with("n1")
    assert http.delete("/api/subnets/s1").get_json()["success"]
    fake.delete_subnet.assert_called_once_with("s1")
    assert http.delete("/api/routers/r1").get_json()["success"]
    fake.delete_router.assert_called_once_with("r1")
    assert http.delete("/api/servers/v1").get_json()["success"]
    fake.delete_server.assert_called_once_with("v1")
    assert http.delete("/api/loadbalancers/lb1").get_json()["success"]
    assert fake.delete_load_balancer.call_count == 1
    assert fake.delete_load_balancer.call_args[0][0] == "lb1"
    assert fake.delete_load_balancer.call_args[1]["cascade"] is True


def test_setup_loadbalancer_and_scale_routes(fake, http):
    fake.setup_network_1click.return_value = {"network_id": "n1"}
    fake.create_load_balancer.return_value = {"lb_id": "lb1"}
    fake.list_loadbalancers.return_value = [{"id": "lb1", "name": "nhom17_lb"}]
    fake.scale_out.return_value = {"name": "nhom17_vm_2"}
    fake.scale_in.return_value = {"deleted": "nhom17_vm_2"}
    assert http.get("/api/loadbalancers").get_json()["data"] == {"items": [{"id": "lb1", "name": "nhom17_lb"}]}
    assert http.post("/api/setup").get_json()["data"] == {"network_id": "n1"}
    assert http.post("/api/loadbalancer").get_json()["data"] == {"lb_id": "lb1"}
    assert fake.create_load_balancer.call_args.kwargs == {"name": None}
    assert http.post("/api/loadbalancer", json={"name": "custom_lb"}).get_json()["data"] == {"lb_id": "lb1"}
    assert fake.create_load_balancer.call_args.kwargs == {"name": "custom_lb"}
    assert http.post("/api/scale-out").get_json()["data"] == {"name": "nhom17_vm_2"}
    assert http.post("/api/scale-in", json={"server_id": "srv2"}).get_json()["data"] == {"deleted": "nhom17_vm_2"}
    assert fake.scale_in.call_args.kwargs == {"server_id": "srv2"}


def test_keypair_routes(fake, http):
    fake.list_keypairs.return_value = [{"name": "kp1"}]
    fake.create_keypair.return_value = {"name": "kp2", "private_key": "MIIE..."}
    assert http.get("/api/keypairs").get_json()["data"] == {"items": [{"name": "kp1"}]}
    res = http.post("/api/keypairs", json={"name": "kp2"}).get_json()
    assert res["success"] is True
    assert res["data"]["item"]["name"] == "kp2"


def test_create_vm_with_auto_keypair(fake, http):
    fake.create_keypair.return_value = {"name": "myvm_key", "private_key": "-----BEGIN RSA PRIVATE KEY-----"}
    fake.create_vm.return_value = {"server_id": "s1", "name": "myvm", "ip": "192.168.17.10"}
    res = http.post(
        "/api/servers",
        json={"name": "myvm", "image_id": "img1", "flavor_id": "flv1", "key_mode": "auto"},
    ).get_json()
    assert res["success"] is True
    assert res["data"]["keypair"]["name"] == "myvm_key"
    assert res["data"]["keypair"]["private_key"] == "-----BEGIN RSA PRIVATE KEY-----"
    fake.create_keypair.assert_called_once_with("myvm_key")
    _, kwargs = fake.create_vm.call_args
    assert kwargs["key_name"] == "myvm_key"


def test_disassociate_floating_ip_route(fake, http):
    fake.disassociate_floating_ip.return_value = {"server_id": "s1", "disassociated": ["10.0.0.5"]}
    res = http.delete("/api/servers/s1/floating-ip").get_json()
    assert res["success"] is True
    assert res["data"]["disassociated"] == ["10.0.0.5"]


