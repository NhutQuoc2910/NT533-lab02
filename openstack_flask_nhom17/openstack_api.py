"""Client REST thuần cho OpenStack (Keystone, Nova, Neutron, Glance, Octavia).

Không dùng bất kỳ SDK OpenStack nào: mọi lời gọi là HTTP request với X-Auth-Token.
"""
import base64
import re
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

import config

SERVICE_TYPES = ("compute", "network", "image", "load-balancer")

WEB_USER_DATA_SCRIPT = """#!/bin/bash
IP=$(hostname -I | xargs)
mkdir -p /var/www/html
cat > /var/www/html/index.html <<EOF
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Nhóm 17 - NT533.R11</title></head>
<body><h1>Nhóm 17 - NT533</h1><p>Địa chỉ IP nội bộ: ${IP}</p></body></html>
EOF

# Chạy web server ngay lập tức bằng python3 có sẵn trong Ubuntu Cloud Image
nohup python3 -m http.server 80 --directory /var/www/html > /var/log/web_server.log 2>&1 &

# Cài đặt Nginx nếu có kết nối Internet và nâng cấp dịch vụ
apt-get update -y
apt-get install -y nginx
if command -v nginx > /dev/null 2>&1; then
    pkill -f "python3 -m http.server 80" || true
    systemctl enable nginx
    systemctl restart nginx
fi
"""

DEFAULT_SG_RULES = (("tcp", 80), ("tcp", 22), ("icmp", None))


class OpenStackError(Exception):
    """Lỗi từ OpenStack hoặc từ logic orchestration, mang thông báo đọc được."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.status_code = status


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
        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            retry_strategy = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[500, 502, 503, 504],
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)
        self.token = None
        self.endpoints = {}


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
        resp = None
        for attempt in (1, 2, 3):
            try:
                resp = self.session.post(
                    config.OS_AUTH_URL,
                    json=body,
                    timeout=config.HTTP_TIMEOUT,
                    verify=config.VERIFY_SSL,
                )
                break
            except requests.exceptions.RequestException as exc:
                if attempt < 3:
                    time.sleep(1)
                    continue
                raise OpenStackError(
                    f"Lỗi kết nối tới Keystone ({config.OS_AUTH_URL}): {exc}. "
                    "Hạ tầng NC-Cloud UIT chập chờn, vui lòng thử lại sau vài giây."
                ) from exc

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
            try:
                resp = self.session.request(
                    method,
                    self._base(service) + path,
                    headers={"X-Auth-Token": self.token, "Content-Type": "application/json"},
                    params=params,
                    json=json,
                    timeout=config.HTTP_TIMEOUT,
                    verify=config.VERIFY_SSL,
                )
            except requests.exceptions.RequestException as exc:
                if attempt == 1:
                    time.sleep(1)
                    continue
                raise OpenStackError(
                    f"Lỗi kết nối tới dịch vụ '{service}' ({exc}). "
                    "Hạ tầng NC-Cloud UIT chập chờn hoặc đóng kết nối, vui lòng thử lại."
                ) from exc

            if resp.status_code == 401 and attempt == 1:
                self.authenticate()
                continue
            break
        if resp.status_code >= 400:
            raise OpenStackError(_error_text(resp), resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()


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

    def _cleanup_subnet_ports(self, subnet_id, logs=None):
        try:
            ports = self._request("GET", "network", "/v2.0/ports")["ports"]
        except Exception:
            return
        for p in ports:
            has_subnet = any(ip.get("subnet_id") == subnet_id for ip in p.get("fixed_ips", []))
            if not has_subnet:
                continue
            owner = p.get("device_owner", "")
            if owner.startswith("network:router_interface") or owner == "network:ha_router_replicated_interface":
                router_id = p.get("device_id")
                if router_id:
                    self._log(logs, f"Tự động gỡ Router Interface khỏi Subnet (Router: {router_id}, Port: {p['id']})")
                    try:
                        self.remove_router_interface(router_id, port_id=p["id"], logs=logs)
                    except Exception:
                        try:
                            self.remove_router_interface(router_id, subnet_id=subnet_id, logs=logs)
                        except Exception:
                            pass
            elif owner.startswith("compute:"):
                device_id = str(p.get("device_id") or "")
                port_name = str(p.get("name") or "")
                if device_id.startswith("lb-") or port_name.startswith("octavia-"):
                    try:
                        self._request("DELETE", "network", f"/v2.0/ports/{p['id']}")
                        self._log(logs, f"Đã tự động giải phóng cổng mồ côi của Load Balancer {p['id']}")
                        continue
                    except Exception:
                        raise OpenStackError(
                            f"Không thể xóa Subnet/Network vì Load Balancer (Device: {device_id}) đang gắn vào. "
                            "Vui lòng xóa Load Balancer trước."
                        )
                vm_name = device_id
                try:
                    srv = self.get_server(device_id)
                    vm_name = f"'{srv.get('name')}' (ID: {device_id})"
                except Exception:
                    pass
                raise OpenStackError(
                    f"Không thể xóa Subnet/Network vì máy ảo {vm_name} vẫn đang gắn vào mạng này. "
                    "Vui lòng xóa máy ảo trước."
                )
            elif owner in ("network:dhcp", "network:distributed"):
                continue
            else:
                try:
                    self._request("DELETE", "network", f"/v2.0/ports/{p['id']}")
                    self._log(logs, f"Đã tự động giải phóng cổng phụ thuộc {p['id']} ({owner})")
                except Exception:
                    device_id = str(p.get("device_id") or "")
                    if "lb" in owner.lower() or "octavia" in owner.lower() or device_id.startswith("lb-"):
                        raise OpenStackError(
                            f"Không thể xóa Subnet/Network vì Load Balancer (Device: {device_id}) đang gắn vào. "
                            "Vui lòng xóa Load Balancer trước."
                        )

    def _cleanup_network_ports(self, network_id, logs=None):
        try:
            ports = self._request("GET", "network", "/v2.0/ports", params={"network_id": network_id})["ports"]
        except Exception:
            return
        for p in ports:
            owner = p.get("device_owner", "")
            if owner.startswith("network:router_interface") or owner == "network:ha_router_replicated_interface":
                router_id = p.get("device_id")
                if router_id:
                    self._log(logs, f"Tự động gỡ Router Interface khỏi Network (Router: {router_id}, Port: {p['id']})")
                    try:
                        self.remove_router_interface(router_id, port_id=p["id"], logs=logs)
                    except Exception:
                        pass
            elif owner.startswith("compute:"):
                device_id = str(p.get("device_id") or "")
                port_name = str(p.get("name") or "")
                if device_id.startswith("lb-") or port_name.startswith("octavia-"):
                    try:
                        self._request("DELETE", "network", f"/v2.0/ports/{p['id']}")
                        self._log(logs, f"Đã tự động giải phóng cổng mồ côi của Load Balancer {p['id']}")
                        continue
                    except Exception:
                        raise OpenStackError(
                            f"Không thể xóa Network vì Load Balancer (Device: {device_id}) đang gắn vào. "
                            "Vui lòng xóa Load Balancer trước."
                        )
                vm_name = device_id
                try:
                    srv = self.get_server(device_id)
                    vm_name = f"'{srv.get('name')}' (ID: {device_id})"
                except Exception:
                    pass
                raise OpenStackError(
                    f"Không thể xóa Network vì máy ảo {vm_name} vẫn đang gắn vào mạng này. "
                    "Vui lòng xóa máy ảo trước."
                )
            elif owner in ("network:dhcp", "network:distributed"):
                continue
            else:
                try:
                    self._request("DELETE", "network", f"/v2.0/ports/{p['id']}")
                    self._log(logs, f"Đã tự động giải phóng cổng phụ thuộc {p['id']} ({owner})")
                except Exception:
                    device_id = str(p.get("device_id") or "")
                    if "lb" in owner.lower() or "octavia" in owner.lower() or device_id.startswith("lb-"):
                        raise OpenStackError(
                            f"Không thể xóa Network vì Load Balancer (Device: {device_id}) đang gắn vào. "
                            "Vui lòng xóa Load Balancer trước."
                        )

    def delete_subnet(self, subnet_id, logs=None):
        try:
            self._request("DELETE", "network", f"/v2.0/subnets/{subnet_id}")
            return
        except OpenStackError as exc:
            if exc.status_code != 409:
                raise
            self._cleanup_subnet_ports(subnet_id, logs=logs)
            self._request("DELETE", "network", f"/v2.0/subnets/{subnet_id}")

    def delete_network(self, network_id, logs=None):
        for subnet in self.list_subnets(network_id=network_id):
            self.delete_subnet(subnet["id"], logs=logs)
        try:
            self._request("DELETE", "network", f"/v2.0/networks/{network_id}")
        except OpenStackError as exc:
            if exc.status_code != 409:
                raise
            self._cleanup_network_ports(network_id, logs=logs)
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

    def _disassociate_router_floating_ips(self, router_id, subnet_id=None, port_id=None, logs=None):
        try:
            fips = self._request("GET", "network", "/v2.0/floatingips")["floatingips"]
        except Exception:
            return

        target_port_ids = set()
        if port_id:
            target_port_ids.add(port_id)
        elif subnet_id:
            try:
                ports = self._request("GET", "network", "/v2.0/ports")["ports"]
                for p in ports:
                    if any(ip.get("subnet_id") == subnet_id for ip in p.get("fixed_ips", [])):
                        target_port_ids.add(p["id"])
            except Exception:
                pass

        for f in fips:
            if not f.get("port_id"):
                continue
            is_match = False
            if target_port_ids and f.get("port_id") in target_port_ids:
                is_match = True
            elif not target_port_ids and f.get("router_id") == router_id:
                is_match = True

            if is_match:
                self._log(
                    logs,
                    f"Tự động gỡ liên kết Floating IP {f['floating_ip_address']} để giải phóng Router Interface",
                )
                try:
                    self._request(
                        "PUT",
                        "network",
                        f"/v2.0/floatingips/{f['id']}",
                        json={"floatingip": {"port_id": None}},
                    )
                except Exception:
                    pass

    def remove_router_interface(self, router_id, subnet_id=None, port_id=None, logs=None):
        body = {"subnet_id": subnet_id} if subnet_id else {"port_id": port_id}
        try:
            return self._request(
                "PUT", "network", f"/v2.0/routers/{router_id}/remove_router_interface", json=body
            )
        except OpenStackError as exc:
            if exc.status_code == 409 and "floating ip" in str(exc).lower():
                self._log(
                    logs,
                    f"Phát hiện Floating IP đang phụ thuộc vào Router {router_id}, tiến hành tự động gỡ Floating IP...",
                )
                self._disassociate_router_floating_ips(
                    router_id, subnet_id=subnet_id, port_id=port_id, logs=logs
                )
                return self._request(
                    "PUT", "network", f"/v2.0/routers/{router_id}/remove_router_interface", json=body
                )
            raise

    def _router_ports(self, router_id):
        return self._request(
            "GET", "network", "/v2.0/ports",
            params={"device_id": router_id, "device_owner": "network:router_interface"},
        )["ports"]

    def delete_router(self, router_id, logs=None):
        for port in self._router_ports(router_id):
            self.remove_router_interface(router_id, port_id=port["id"], logs=logs)
        self._request("DELETE", "network", f"/v2.0/routers/{router_id}")


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


    def list_keypairs(self):
        data = self._request("GET", "compute", "/v2.1/os-keypairs")
        return [kp["keypair"] for kp in data.get("keypairs", [])]

    def create_keypair(self, name):
        body = {"keypair": {"name": name}}
        return self._request("POST", "compute", "/v2.1/os-keypairs", json=body)["keypair"]

    def delete_keypair(self, name):
        self._request("DELETE", "compute", f"/v2.1/os-keypairs/{name}")

    def list_servers(self):
        data = self._request("GET", "compute", "/v2.1/servers/detail")
        return [self._simplify_server(s) for s in data["servers"]]

    def get_server(self, server_id):
        data = self._request("GET", "compute", f"/v2.1/servers/{server_id}")
        return self._simplify_server(data["server"])

    def create_server(self, name, image_id, flavor_id, network_id, user_data=None, key_name=None, volume_size=10):
        server = {
            "name": name,
            "flavorRef": flavor_id,
            "networks": [{"uuid": network_id}],
            "block_device_mapping_v2": [
                {
                    "source_type": "image",
                    "destination_type": "volume",
                    "uuid": image_id,
                    "boot_index": 0,
                    "volume_size": volume_size,
                    "delete_on_termination": True,
                }
            ],
        }
        if user_data:
            server["user_data"] = user_data
        if key_name:
            server["key_name"] = key_name
        return self._request("POST", "compute", "/v2.1/servers", json={"server": server})["server"]

    def delete_server(self, server_id):
        self._request("DELETE", "compute", f"/v2.1/servers/{server_id}")

    def start_server(self, server_id):
        self._request("POST", "compute", f"/v2.1/servers/{server_id}/action", json={"os-start": None})

    def stop_server(self, server_id):
        self._request("POST", "compute", f"/v2.1/servers/{server_id}/action", json={"os-stop": None})

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

    def create_vm(self, name, image_id, flavor_id, web=False, key_name=None, logs=None):
        network = self._require_network()
        user_data = encode_user_data(WEB_USER_DATA_SCRIPT) if web else None
        if key_name:
            self._log(logs, f"Gắn SSH Keypair '{key_name}' vào máy ảo")
        server = self.create_server(
            name, image_id, flavor_id, network["id"], user_data=user_data, key_name=key_name
        )
        self._log(logs, f"Đã gửi yêu cầu tạo máy ảo {name} (id {server['id']})")
        ip = self.wait_for_server_active(server["id"])
        res = {"server_id": server["id"], "name": name, "ip": ip}
        if key_name:
            res["key_name"] = key_name
        return res

    def create_web_vm(self, name, image_id, flavor_id, key_name=None, logs=None):
        return self.create_vm(name, image_id, flavor_id, web=True, key_name=key_name, logs=logs)

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

    def disassociate_floating_ip(self, server_id, logs=None):
        ports = self._request(
            "GET", "network", "/v2.0/ports", params={"device_id": server_id}
        )["ports"]
        if not ports:
            raise OpenStackError(f"Không tìm thấy port của máy ảo {server_id}")
        port_ids = {p["id"] for p in ports}
        fips = self._request("GET", "network", "/v2.0/floatingips")["floatingips"]
        detached = []
        for f in fips:
            if f.get("port_id") in port_ids:
                self._request(
                    "PUT",
                    "network",
                    f"/v2.0/floatingips/{f['id']}",
                    json={"floatingip": {"port_id": None}},
                )
                detached.append(f["floating_ip_address"])
                self._log(logs, f"Đã gỡ Floating IP {f['floating_ip_address']} khỏi máy ảo {server_id}")
        if not detached:
            self._log(logs, f"Máy ảo {server_id} không có Floating IP nào đang gắn")
        return {"server_id": server_id, "disassociated": detached}


    @staticmethod
    def _rule_matches(rule, protocol, port):
        return (
            rule.get("direction") == "ingress"
            and rule.get("ethertype") == "IPv4"
            and rule.get("protocol") == protocol
            and rule.get("port_range_min") == port
            and rule.get("port_range_max") == port
            and rule.get("remote_ip_prefix") in (None, "0.0.0.0/0")
            and not rule.get("remote_group_id")
        )

    def ensure_default_sg_rules(self, logs=None):
        groups = self._request(
            "GET", "network", "/v2.0/security-groups", params={"name": "default"}
        )["security_groups"]
        if not groups:
            raise OpenStackError("Không tìm thấy Security Group 'default'")
        sg_id = groups[0]["id"]
        existing = self._request(
            "GET", "network", "/v2.0/security-group-rules", params={"security_group_id": sg_id}
        )["security_group_rules"]
        for protocol, port in DEFAULT_SG_RULES:
            label = f"{protocol.upper()}/{port}" if port else protocol.upper()
            if any(self._rule_matches(r, protocol, port) for r in existing):
                self._log(logs, f"Rule {label} trên Security Group default đã tồn tại, bỏ qua")
                continue
            rule = {
                "security_group_id": sg_id,
                "direction": "ingress",
                "ethertype": "IPv4",
                "protocol": protocol,
                "remote_ip_prefix": "0.0.0.0/0",
            }
            if port:
                rule["port_range_min"] = port
                rule["port_range_max"] = port
            try:
                self._request(
                    "POST", "network", "/v2.0/security-group-rules",
                    json={"security_group_rule": rule},
                )
            except OpenStackError as err:
                if err.status == 409:
                    self._log(logs, f"Rule {label} đã tồn tại (409), bỏ qua")
                    continue
                raise
            self._log(logs, f"Đã thêm rule ingress {label} vào Security Group default")

    def setup_network_1click(self, logs=None):
        net = self._find_by_name(self.list_networks(name=config.NET_NAME), config.NET_NAME)
        if net:
            self._log(logs, f"Network {config.NET_NAME} đã tồn tại ({net['id']})")
        else:
            net = self.create_network(config.NET_NAME)
            self._log(logs, f"Đã tạo network {config.NET_NAME} ({net['id']})")

        subnet = self._find_by_name(
            self.list_subnets(network_id=net["id"], name=config.SUBNET_NAME), config.SUBNET_NAME
        )
        if subnet:
            self._log(logs, f"Subnet {config.SUBNET_NAME} đã tồn tại ({subnet['id']})")
        else:
            subnet = self.create_subnet(
                net["id"],
                config.SUBNET_NAME,
                config.CIDR,
                gateway_ip=config.GATEWAY_IP,
                allocation_pools=[config.ALLOCATION_POOL],
                dns_nameservers=config.DNS_NAMESERVERS,
            )
            self._log(
                logs,
                f"Đã tạo subnet {config.SUBNET_NAME} ({config.CIDR}, "
                f"pool từ {config.ALLOCATION_POOL['start']})",
            )

        router = self._find_by_name(self.list_routers(name=config.ROUTER_NAME), config.ROUTER_NAME)
        if router:
            self._log(logs, f"Router {config.ROUTER_NAME} đã tồn tại ({router['id']})")
        else:
            external_id = self.get_external_network_id()
            router = self.create_router(config.ROUTER_NAME, external_network_id=external_id)
            self._log(logs, f"Đã tạo router {config.ROUTER_NAME} nối External Network {external_id}")

        attached = any(
            ip.get("subnet_id") == subnet["id"]
            for port in self._router_ports(router["id"])
            for ip in port.get("fixed_ips", [])
        )
        if attached:
            self._log(logs, f"Router đã gắn interface {config.SUBNET_NAME}, bỏ qua")
        else:
            self.add_router_interface(router["id"], subnet["id"])
            self._log(logs, f"Đã gắn interface {config.SUBNET_NAME} vào router")

        self.ensure_default_sg_rules(logs)
        return {"network_id": net["id"], "subnet_id": subnet["id"], "router_id": router["id"]}


    def wait_for_lb_active(self, lb_id):
        """Polling provisioning_status == ACTIVE (mỗi POLL_INTERVAL giây, tối đa POLL_TIMEOUT giây)."""
        state = {"status": None}

        def check():
            lb = self._request(
                "GET", "load-balancer", f"/v2.0/lbaas/loadbalancers/{lb_id}"
            )["loadbalancer"]
            state["status"] = lb["provisioning_status"]
            if state["status"] == "ERROR":
                raise OpenStackError(f"Load Balancer {lb_id} ở trạng thái ERROR")
            return state["status"] == "ACTIVE", lb

        return self._poll(
            check,
            lambda: f"Load Balancer {lb_id} đạt ACTIVE (trạng thái hiện tại: {state['status']})",
        )

    def find_lb(self, name=None):
        target = name or config.LB_NAME
        lbs = self._request(
            "GET", "load-balancer", "/v2.0/lbaas/loadbalancers", params={"name": target}
        )["loadbalancers"]
        return self._find_by_name(lbs, target)

    def list_loadbalancers(self):
        lbs = self._request("GET", "load-balancer", "/v2.0/lbaas/loadbalancers")["loadbalancers"]
        fip_map = {}
        try:
            fips = self._request("GET", "network", "/v2.0/floatingips")["floatingips"]
            for f in fips:
                if f.get("port_id"):
                    fip_map[f["port_id"]] = f["floating_ip_address"]
        except Exception:
            pass

        result = []
        for lb in lbs:
            vip_port = lb.get("vip_port_id")
            result.append({
                "id": lb["id"],
                "name": lb.get("name") or "(không tên)",
                "provisioning_status": lb.get("provisioning_status", "UNKNOWN"),
                "operating_status": lb.get("operating_status", "UNKNOWN"),
                "vip_address": lb.get("vip_address"),
                "vip_port_id": vip_port,
                "floating_ip": fip_map.get(vip_port),
                "provider": lb.get("provider", "ovn"),
                "listeners": lb.get("listeners", []),
                "pools": lb.get("pools", []),
            })
        return result

    def _require_lb(self, name=None):
        target = name or config.LB_NAME
        lb = self.find_lb(target)
        if not lb:
            raise OpenStackError(
                f"Chưa có Load Balancer '{target}', hãy tạo Load Balancer trước"
            )
        if not lb.get("pools"):
            raise OpenStackError(f"Load Balancer '{target}' chưa có Pool")
        return lb, lb["pools"][0]["id"]

    def list_pool_members(self, pool_id):
        return self._request(
            "GET", "load-balancer", f"/v2.0/lbaas/pools/{pool_id}/members"
        )["members"]

    def add_pool_member(self, lb_id, pool_id, address, subnet_id, logs=None):
        self.wait_for_lb_active(lb_id)
        body = {"member": {"address": address, "protocol_port": 80, "subnet_id": subnet_id}}
        member = self._request(
            "POST", "load-balancer", f"/v2.0/lbaas/pools/{pool_id}/members", json=body
        )["member"]
        self.wait_for_lb_active(lb_id)
        self._log(logs, f"Đã thêm {address}:80 vào Pool của Load Balancer")
        return member

    def remove_pool_member(self, lb_id, pool_id, member_id, logs=None):
        self.wait_for_lb_active(lb_id)
        self._request(
            "DELETE", "load-balancer", f"/v2.0/lbaas/pools/{pool_id}/members/{member_id}"
        )
        self.wait_for_lb_active(lb_id)
        self._log(logs, f"Đã xóa member {member_id} khỏi Pool của Load Balancer")

    def delete_load_balancer(self, lb_id, cascade=True, logs=None):
        try:
            lb_data = self._request("GET", "load-balancer", f"/v2.0/lbaas/loadbalancers/{lb_id}")["loadbalancer"]
            if lb_data.get("provisioning_status") not in ("ACTIVE", "ERROR"):
                self.wait_for_lb_active(lb_id)
            vip_port_id = lb_data.get("vip_port_id")
            if vip_port_id:
                try:
                    fips = self._request("GET", "network", "/v2.0/floatingips")["floatingips"]
                    for f in fips:
                        if f.get("port_id") == vip_port_id:
                            self._request("DELETE", "network", f"/v2.0/floatingips/{f['id']}")
                            self._log(logs, f"Đã xóa Floating IP {f['floating_ip_address']} gắn trên VIP của Load Balancer")
                except Exception as e:
                    self._log(logs, f"Kiểm tra/xóa Floating IP VIP: {e}")
        except Exception:
            pass

        params = {"cascade": "true"} if cascade else None
        self._request("DELETE", "load-balancer", f"/v2.0/lbaas/loadbalancers/{lb_id}", params=params)
        self._log(logs, f"Đã gửi yêu cầu xóa Load Balancer {lb_id} (cascade={cascade})")

    def create_load_balancer(self, logs=None, name=None):
        lb_name = (name or "").strip() or config.LB_NAME
        if self.find_lb(lb_name):
            raise OpenStackError(
                f"Load Balancer '{lb_name}' đã tồn tại, hãy dùng Scale Out/Scale In"
            )

        network = self._require_network()
        subnet = self._find_by_name(
            self.list_subnets(network_id=network["id"], name=config.SUBNET_NAME), config.SUBNET_NAME
        )
        if not subnet:
            raise OpenStackError(
                f"Chưa có subnet '{config.SUBNET_NAME}', hãy chạy Thiết lập mạng 1-Click trước"
            )

        lb_body = {"name": lb_name, "vip_subnet_id": subnet["id"]}
        if getattr(config, "LB_PROVIDER", None):
            lb_body["provider"] = config.LB_PROVIDER

        lb = self._request(
            "POST", "load-balancer", "/v2.0/lbaas/loadbalancers",
            json={"loadbalancer": lb_body},
        )["loadbalancer"]
        self._log(logs, f"Đã gửi yêu cầu tạo Load Balancer {lb_name} ({lb['id']})")
        lb = self.wait_for_lb_active(lb["id"])
        self._log(logs, "Load Balancer ACTIVE")

        protocol = "HTTP"
        lb_algorithm = "ROUND_ROBIN"

        try:
            listener = self._request(
                "POST", "load-balancer", "/v2.0/lbaas/listeners",
                json={"listener": {
                    "name": f"{lb_name}_listener",
                    "protocol": "HTTP",
                    "protocol_port": 80,
                    "loadbalancer_id": lb["id"],
                }},
            )["listener"]
            self.wait_for_lb_active(lb["id"])
            self._log(logs, f"Đã tạo Listener HTTP:80 ({listener['id']})")
        except OpenStackError as exc:
            if exc.status_code == 501 and ("ovn" in str(exc).lower() or "protocol" in str(exc).lower()):
                self._log(logs, "Octavia OVN provider chỉ hỗ trợ giao thức L4 (TCP). Tự động chuyển sang TCP:80...")
                protocol = "TCP"
                lb_algorithm = "SOURCE_IP_PORT"
                listener = self._request(
                    "POST", "load-balancer", "/v2.0/lbaas/listeners",
                    json={"listener": {
                        "name": f"{lb_name}_listener",
                        "protocol": "TCP",
                        "protocol_port": 80,
                        "loadbalancer_id": lb["id"],
                    }},
                )["listener"]
                self.wait_for_lb_active(lb["id"])
                self._log(logs, f"Đã tạo Listener TCP:80 ({listener['id']})")
            else:
                raise

        try:
            pool = self._request(
                "POST", "load-balancer", "/v2.0/lbaas/pools",
                json={"pool": {
                    "name": f"{lb_name}_pool",
                    "protocol": protocol,
                    "lb_algorithm": lb_algorithm,
                    "listener_id": listener["id"],
                }},
            )["pool"]
            self.wait_for_lb_active(lb["id"])
            self._log(logs, f"Đã tạo Pool {lb_algorithm} ({pool['id']})")
        except OpenStackError as exc:
            if exc.status_code == 501 and ("ovn" in str(exc).lower() or "algorithm" in str(exc).lower()):
                self._log(logs, "Octavia OVN provider yêu cầu thuật toán SOURCE_IP_PORT. Tự động chuyển đổi...")
                lb_algorithm = "SOURCE_IP_PORT"
                pool = self._request(
                    "POST", "load-balancer", "/v2.0/lbaas/pools",
                    json={"pool": {
                        "name": f"{lb_name}_pool",
                        "protocol": "TCP" if protocol == "HTTP" else protocol,
                        "lb_algorithm": lb_algorithm,
                        "listener_id": listener["id"],
                    }},
                )["pool"]
                self.wait_for_lb_active(lb["id"])
                self._log(logs, f"Đã tạo Pool {lb_algorithm} ({pool['id']})")
            else:
                raise

        members = []
        for vm in self.list_nhom17_vms():
            if vm["fixed_ips"]:
                address = vm["fixed_ips"][0]
                self.add_pool_member(lb["id"], pool["id"], address, subnet["id"], logs)
                members.append(address)
        if not members:
            self._log(logs, "Chưa có máy ảo nhom17_vm_* nào để thêm vào Pool")

        floating_ip = self._create_floating_ip(lb["vip_port_id"])
        self._log(logs, f"Đã gán Floating IP {floating_ip} cho VIP của Load Balancer")
        return {
            "lb_id": lb["id"],
            "pool_id": pool["id"],
            "vip_address": lb["vip_address"],
            "floating_ip": floating_ip,
            "members": members,
        }


    def scale_out(self, logs=None):
        if not config.DEFAULT_IMAGE_ID or not config.DEFAULT_FLAVOR_ID:
            missing = [
                name for name, value in (
                    ("DEFAULT_IMAGE_ID", config.DEFAULT_IMAGE_ID),
                    ("DEFAULT_FLAVOR_ID", config.DEFAULT_FLAVOR_ID),
                ) if not value
            ]
            raise OpenStackError("Thiếu cấu hình trong .env: " + ", ".join(missing))
        lb, pool_id = self._require_lb()
        name = self.next_vm_name()
        self._log(logs, f"Scale Out: tạo máy ảo {name} (không gán Floating IP riêng)")
        created = self.create_vm(
            name, config.DEFAULT_IMAGE_ID, config.DEFAULT_FLAVOR_ID, web=True, logs=logs
        )
        try:
            self.add_pool_member(lb["id"], pool_id, created["ip"], lb["vip_subnet_id"], logs)
        except OpenStackError as err:
            self._log(logs, f"Thêm {name} vào Load Balancer thất bại, máy ảo được giữ lại để xử lý thủ công")
            raise OpenStackError(
                f"Máy ảo {name} đã tạo nhưng không thêm được vào Load Balancer: {err.message}",
                err.status,
            ) from err
        self._log(logs, f"Scale Out hoàn tất: {name} ({created['ip']}) đã nằm trong Load Balancer")
        return created

    def scale_in(self, logs=None, server_id=None):
        vms = self.list_nhom17_vms()
        if len(vms) <= 1:
            raise OpenStackError("Chỉ còn 1 VM, không thể Scale In")
        if server_id:
            target = next((v for v in vms if v["id"] == server_id), None)
            if not target:
                raise OpenStackError(f"Không tìm thấy máy ảo {server_id} trong nhóm {config.VM_PREFIX}*")
        else:
            target = vms[-1]
        lb, pool_id = self._require_lb()
        self._log(logs, f"Scale In: chọn máy ảo {target['name']}")

        member = next(
            (m for m in self.list_pool_members(pool_id) if m["address"] in target["fixed_ips"]),
            None,
        )
        if member:
            self.remove_pool_member(lb["id"], pool_id, member["id"], logs)
        else:
            self._log(logs, f"{target['name']} không nằm trong Pool, bỏ qua bước xóa member")

        self.delete_server(target["id"])
        self._log(logs, f"Đã xóa máy ảo {target['name']}")
        return {"deleted": target["name"], "member_removed": member is not None}
