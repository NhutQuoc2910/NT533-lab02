"""Client REST thuần cho OpenStack (Keystone, Nova, Neutron, Glance, Octavia).

Không dùng bất kỳ SDK OpenStack nào: mọi lời gọi là HTTP request với X-Auth-Token.
"""
import base64
import re
import time

import requests

import config

SERVICE_TYPES = ("compute", "network", "image", "load-balancer")


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
