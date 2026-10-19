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

# Rule ingress cần mở trên Security Group "default": (protocol, port).
DEFAULT_SG_RULES = (("tcp", 80), ("tcp", 22), ("icmp", None))


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

    # ----- Yêu cầu 5: Thiết lập mạng 1-Click -----

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

    # ----- Yêu cầu 7: Octavia Load Balancer -----

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

    def find_lb(self):
        lbs = self._request(
            "GET", "load-balancer", "/v2.0/lbaas/loadbalancers", params={"name": config.LB_NAME}
        )["loadbalancers"]
        return self._find_by_name(lbs, config.LB_NAME)

    def _require_lb(self):
        lb = self.find_lb()
        if not lb:
            raise OpenStackError(
                f"Chưa có Load Balancer '{config.LB_NAME}', hãy tạo Load Balancer trước"
            )
        if not lb.get("pools"):
            raise OpenStackError(f"Load Balancer '{config.LB_NAME}' chưa có Pool")
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

    def create_load_balancer(self, logs=None):
        if self.find_lb():
            raise OpenStackError(
                f"Load Balancer '{config.LB_NAME}' đã tồn tại, hãy dùng Scale Out/Scale In"
            )
        network = self._require_network()
        subnet = self._find_by_name(
            self.list_subnets(network_id=network["id"], name=config.SUBNET_NAME), config.SUBNET_NAME
        )
        if not subnet:
            raise OpenStackError(
                f"Chưa có subnet '{config.SUBNET_NAME}', hãy chạy Thiết lập mạng 1-Click trước"
            )

        lb = self._request(
            "POST", "load-balancer", "/v2.0/lbaas/loadbalancers",
            json={"loadbalancer": {"name": config.LB_NAME, "vip_subnet_id": subnet["id"]}},
        )["loadbalancer"]
        self._log(logs, f"Đã gửi yêu cầu tạo Load Balancer {config.LB_NAME} ({lb['id']})")
        lb = self.wait_for_lb_active(lb["id"])
        self._log(logs, "Load Balancer ACTIVE")

        listener = self._request(
            "POST", "load-balancer", "/v2.0/lbaas/listeners",
            json={"listener": {
                "name": f"{config.LB_NAME}_listener",
                "protocol": "HTTP",
                "protocol_port": 80,
                "loadbalancer_id": lb["id"],
            }},
        )["listener"]
        self.wait_for_lb_active(lb["id"])
        self._log(logs, f"Đã tạo Listener HTTP:80 ({listener['id']})")

        pool = self._request(
            "POST", "load-balancer", "/v2.0/lbaas/pools",
            json={"pool": {
                "name": f"{config.LB_NAME}_pool",
                "protocol": "HTTP",
                "lb_algorithm": "ROUND_ROBIN",
                "listener_id": listener["id"],
            }},
        )["pool"]
        self.wait_for_lb_active(lb["id"])
        self._log(logs, f"Đã tạo Pool ROUND_ROBIN ({pool['id']})")

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

    # ----- Yêu cầu 8: Scale Out / Scale In -----

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
