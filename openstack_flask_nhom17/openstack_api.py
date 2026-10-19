"""Client REST thuần cho OpenStack (Keystone, Nova, Neutron, Glance, Octavia).

Không dùng bất kỳ SDK OpenStack nào: mọi lời gọi là HTTP request với X-Auth-Token.
"""
import base64
import re
import time

import requests

import config

SERVICE_TYPES = ("compute", "network", "image", "load-balancer")

WEB_USER_DATA_SCRIPT = """#!/bin/bash
apt-get update -y
apt-get install -y nginx
IP=$(hostname -I | xargs)
cat > /var/www/html/index.html <<EOF
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Nhóm 17 - NT533</title></head>
<body><h1>Nhóm 17 - NT533</h1><p>Địa chỉ IP nội bộ: ${IP}</p></body></html>
EOF
systemctl enable nginx
systemctl restart nginx
"""


class OpenStackError(Exception):
    """Lỗi từ OpenStack hoặc từ logic orchestration, mang thông báo đọc được."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status


def normalize_base_url(url):
    """Cắt phần phiên bản (/v2, /v2.0, /v2.1 kèm project id) khỏi URL trong catalog."""
    return re.sub(r"(?<!/)/v2(\.\d+)?(/.*)?$", "", url.rstrip("/"))


def encode_user_data(script):
    return base64.b64encode(script.encode("utf-8")).decode("ascii")


def vm_index(name):
    """Trả về N nếu name là nhom17_vm_N, ngược lại None."""
    match = re.fullmatch(re.escape(config.VM_PREFIX) + r"(\d+)", name or "")
    return int(match.group(1)) if match else None


def _find_message(data):
    if isinstance(data, dict):
        for key in ("message", "faultstring", "error_message"):
            if isinstance(data.get(key), str):
                return data[key]
        for value in data.values():
            found = _find_message(value)
            if found:
                return found
    return None


def _error_text(resp):
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    return f"HTTP {resp.status_code}: {_find_message(data) or resp.text[:200]}"


class OpenStackClient:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.token = None
        self.endpoints = {}

    # ----- Xác thực và gọi HTTP -----

    def authenticate(self):
        body = {
            "auth": {
                "identity": {
                    "methods": ["application_credential"],
                    "application_credential": {
                        "id": config.OS_APP_CRED_ID,
                        "secret": config.OS_APP_CRED_SECRET,
                    },
                }
            }
        }
        resp = self.session.post(
            config.OS_AUTH_URL,
            json=body,
            timeout=config.HTTP_TIMEOUT,
            verify=config.VERIFY_SSL,
        )
        if resp.status_code not in (200, 201):
            raise OpenStackError(_error_text(resp), resp.status_code)
        self.token = resp.headers["X-Subject-Token"]
        self.endpoints = self._parse_catalog(resp.json().get("token", {}).get("catalog", []))

    @staticmethod
    def _parse_catalog(catalog):
        endpoints = {}
        for entry in catalog:
            if entry.get("type") not in SERVICE_TYPES:
                continue
            for ep in entry.get("endpoints", []):
                if ep.get("interface") == "public":
                    endpoints[entry["type"]] = normalize_base_url(ep["url"])
                    break
        return endpoints

    def _base(self, service):
        base = self.endpoints.get(service) or config.FALLBACK_ENDPOINTS.get(service)
        if not base:
            raise OpenStackError(f"Không tìm thấy endpoint '{service}' trong Service Catalog")
        return base

    def _request(self, method, service, path, params=None, json=None):
        if self.token is None:
            self.authenticate()
        resp = None
        for attempt in (1, 2):
            resp = self.session.request(
                method,
                self._base(service) + path,
                headers={"X-Auth-Token": self.token, "Content-Type": "application/json"},
                params=params,
                json=json,
                timeout=config.HTTP_TIMEOUT,
                verify=config.VERIFY_SSL,
            )
            if resp.status_code == 401 and attempt == 1:
                self.authenticate()
                continue
            break
        if resp.status_code >= 400:
            raise OpenStackError(_error_text(resp), resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ----- Tiện ích dùng chung -----

    @staticmethod
    def _log(logs, message):
        if logs is not None:
            logs.append(message)

    @staticmethod
    def _find_by_name(items, name):
        return next((i for i in items if i.get("name") == name), None)

    def _poll(self, check, what):
        """Gọi check() mỗi POLL_INTERVAL giây đến khi (done, value) có done=True hoặc hết POLL_TIMEOUT."""
        deadline = time.monotonic() + config.POLL_TIMEOUT
        while True:
            done, value = check()
            if done:
                return value
            if time.monotonic() >= deadline:
                raise OpenStackError(f"Quá thời gian chờ {what()} (>{config.POLL_TIMEOUT}s)")
            time.sleep(config.POLL_INTERVAL)

    # ----- Yêu cầu 1, 2: Flavor, Image, Network -----

    def list_flavors(self):
        return self._request("GET", "compute", "/v2.1/flavors/detail")["flavors"]

    def list_images(self):
        return self._request("GET", "image", "/v2/images")["images"]

    def list_networks(self, name=None):
        params = {"name": name} if name else None
        return self._request("GET", "network", "/v2.0/networks", params=params)["networks"]

    def create_network(self, name):
        body = {"network": {"name": name, "admin_state_up": True}}
        return self._request("POST", "network", "/v2.0/networks", json=body)["network"]

    def delete_network(self, network_id):
        for subnet in self.list_subnets(network_id=network_id):
            self.delete_subnet(subnet["id"])
        self._request("DELETE", "network", f"/v2.0/networks/{network_id}")

    def get_external_network_id(self):
        nets = self._request(
            "GET", "network", "/v2.0/networks", params={"router:external": "true"}
        )["networks"]
        if not nets:
            raise OpenStackError("Không tìm thấy External Network (router:external=true)")
        return nets[0]["id"]

    def list_subnets(self, network_id=None, name=None):
        params = {}
        if network_id:
            params["network_id"] = network_id
        if name:
            params["name"] = name
        return self._request("GET", "network", "/v2.0/subnets", params=params or None)["subnets"]

    def create_subnet(self, network_id, name, cidr, gateway_ip=None,
                      allocation_pools=None, dns_nameservers=None, enable_dhcp=True):
        subnet = {
            "network_id": network_id,
            "name": name,
            "ip_version": 4,
            "cidr": cidr,
            "enable_dhcp": enable_dhcp,
        }
        if gateway_ip:
            subnet["gateway_ip"] = gateway_ip
        if allocation_pools:
            subnet["allocation_pools"] = allocation_pools
        if dns_nameservers:
            subnet["dns_nameservers"] = dns_nameservers
        return self._request("POST", "network", "/v2.0/subnets", json={"subnet": subnet})["subnet"]

    def delete_subnet(self, subnet_id):
        self._request("DELETE", "network", f"/v2.0/subnets/{subnet_id}")

    def list_routers(self, name=None):
        params = {"name": name} if name else None
        return self._request("GET", "network", "/v2.0/routers", params=params)["routers"]

    def create_router(self, name, external_network_id=None):
        router = {"name": name, "admin_state_up": True}
        if external_network_id:
            router["external_gateway_info"] = {"network_id": external_network_id}
        return self._request("POST", "network", "/v2.0/routers", json={"router": router})["router"]

    def add_router_interface(self, router_id, subnet_id):
        return self._request(
            "PUT", "network", f"/v2.0/routers/{router_id}/add_router_interface",
            json={"subnet_id": subnet_id},
        )

    def remove_router_interface(self, router_id, subnet_id=None, port_id=None):
        body = {"subnet_id": subnet_id} if subnet_id else {"port_id": port_id}
        return self._request(
            "PUT", "network", f"/v2.0/routers/{router_id}/remove_router_interface", json=body
        )

    def _router_ports(self, router_id):
        return self._request(
            "GET", "network", "/v2.0/ports",
            params={"device_id": router_id, "device_owner": "network:router_interface"},
        )["ports"]

    def delete_router(self, router_id):
        for port in self._router_ports(router_id):
            self.remove_router_interface(router_id, port_id=port["id"])
        self._request("DELETE", "network", f"/v2.0/routers/{router_id}")

    # ----- Yêu cầu 4, 6: Instance, Floating IP -----

    @staticmethod
    def _simplify_server(server):
        fixed, floating = [], []
        for addrs in server.get("addresses", {}).values():
            for addr in addrs:
                target = floating if addr.get("OS-EXT-IPS:type") == "floating" else fixed
                target.append(addr["addr"])
        return {
            "id": server["id"],
            "name": server["name"],
            "status": server["status"],
            "fixed_ips": fixed,
            "floating_ips": floating,
        }

    def list_servers(self):
        data = self._request("GET", "compute", "/v2.1/servers/detail")
        return [self._simplify_server(s) for s in data["servers"]]

    def get_server(self, server_id):
        data = self._request("GET", "compute", f"/v2.1/servers/{server_id}")
        return self._simplify_server(data["server"])

    def create_server(self, name, image_id, flavor_id, network_id, user_data=None, key_name=None):
        server = {
            "name": name,
            "imageRef": image_id,
            "flavorRef": flavor_id,
            "networks": [{"uuid": network_id}],
        }
        if user_data:
            server["user_data"] = user_data
        if key_name:
            server["key_name"] = key_name
        return self._request("POST", "compute", "/v2.1/servers", json={"server": server})["server"]

    def delete_server(self, server_id):
        self._request("DELETE", "compute", f"/v2.1/servers/{server_id}")

    def wait_for_server_active(self, server_id):
        """Chờ server ACTIVE và có IP nội bộ, trả về IP đó."""
        state = {"status": None}

        def check():
            server = self.get_server(server_id)
            state["status"] = server["status"]
            if server["status"] == "ERROR":
                raise OpenStackError(f"Máy ảo {server_id} ở trạng thái ERROR")
            if server["status"] == "ACTIVE" and server["fixed_ips"]:
                return True, server["fixed_ips"][0]
            return False, None

        return self._poll(
            check, lambda: f"máy ảo {server_id} ACTIVE (trạng thái hiện tại: {state['status']})"
        )

    def _require_network(self):
        net = self._find_by_name(self.list_networks(name=config.NET_NAME), config.NET_NAME)
        if not net:
            raise OpenStackError(
                f"Chưa có network '{config.NET_NAME}', hãy chạy Thiết lập mạng 1-Click trước"
            )
        return net

    def list_nhom17_vms(self):
        vms = []
        for server in self.list_servers():
            index = vm_index(server["name"])
            if index is not None:
                vms.append(dict(server, index=index))
        return sorted(vms, key=lambda v: v["index"])

    def next_vm_name(self):
        vms = self.list_nhom17_vms()
        number = vms[-1]["index"] + 1 if vms else 1
        return f"{config.VM_PREFIX}{number}"

    def create_vm(self, name, image_id, flavor_id, web=False, logs=None):
        network = self._require_network()
        user_data = encode_user_data(WEB_USER_DATA_SCRIPT) if web else None
        server = self.create_server(name, image_id, flavor_id, network["id"], user_data=user_data)
        self._log(logs, f"Đã gửi yêu cầu tạo máy ảo {name} (id {server['id']})")
        ip = self.wait_for_server_active(server["id"])
        self._log(logs, f"Máy ảo {name} ACTIVE, IP nội bộ {ip}")
        return {"server_id": server["id"], "name": name, "ip": ip}

    def create_web_vm(self, name, image_id, flavor_id, logs=None):
        return self.create_vm(name, image_id, flavor_id, web=True, logs=logs)

    def _create_floating_ip(self, port_id):
        external_id = self.get_external_network_id()
        body = {"floatingip": {"floating_network_id": external_id, "port_id": port_id}}
        fip = self._request("POST", "network", "/v2.0/floatingips", json=body)["floatingip"]
        return fip["floating_ip_address"]

    def assign_floating_ip(self, server_id, logs=None):
        ports = self._request(
            "GET", "network", "/v2.0/ports", params={"device_id": server_id}
        )["ports"]
        if not ports:
            raise OpenStackError(f"Không tìm thấy port của máy ảo {server_id}")
        address = self._create_floating_ip(ports[0]["id"])
        self._log(logs, f"Đã gán Floating IP {address} cho máy ảo {server_id}")
        return {"server_id": server_id, "floating_ip": address}
