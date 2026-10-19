# OpenStack Flask Nhóm 17 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây web app Flask quản lý Network, Subnet, Router, Instance, Load Balancer và Scale Out/In trên NC-Cloud qua REST API thuần.

**Architecture:** `config.py` đọc `.env`. `openstack_api.py` chứa `OpenStackClient` (Keystone, Nova, Neutron, Glance, Octavia qua `requests`, polling, orchestration cấp cao ghi `logs`). `app.py` bọc kết quả thành envelope `{success, message, logs, data}`. `templates/index.html` là dashboard Bootstrap 5 + Vanilla JS.

**Tech Stack:** Python 3, Flask, requests, urllib3, python-dotenv, pytest, responses, Bootstrap 5 (CDN).

**Spec:** `docs/superpowers/specs/2026-10-20-openstack-flask-nhom17-design.md`

**Quy ước chung:**
- Mọi lệnh chạy trong thư mục `e:\HK5\NT533\THUCHANH\Lab02\NT533-lab02\openstack_flask_nhom17` (PowerShell), trừ lệnh `git` có đường dẫn tương đối từ thư mục đó.
- Không dùng SDK OpenStack. Không có marker trích dẫn `[cite: ...]` trong code, comment hay tài liệu.
- Chuẩn hóa URL: `normalize_base_url` cắt phần phiên bản (`/v2`, `/v2.0`, `/v2.1`, kèm project id) khỏi URL catalog, các hàm client luôn tự thêm phiên bản vào đường dẫn (`/v2.1/...` cho compute, `/v2.0/...` cho network và load-balancer, `/v2/...` cho image). Vì vậy `FALLBACK_ENDPOINTS` trong `config.py` là URL gốc không có phiên bản (điều chỉnh nhỏ so với spec, hành vi không đổi).
- VM web dùng script `apt-get`, giả định image Ubuntu/Debian.

## File Structure

| File | Trách nhiệm |
|------|-------------|
| `config.py` | Đọc `.env`, hằng số nhom17, `validate()` |
| `openstack_api.py` | `OpenStackError`, `OpenStackClient`, tiện ích (`normalize_base_url`, `encode_user_data`, `vm_index`) |
| `app.py` | Flask routes, envelope JSON, xử lý lỗi |
| `templates/index.html` | Dashboard |
| `tests/helpers.py` | Hằng số URL, catalog giả, `calls_summary`, `mutating_calls` |
| `tests/conftest.py` | Fixture `mocked`, `client`, cấu hình test nhanh |
| `tests/test_*.py` | Test theo nhóm chức năng |
| `pytest.ini` | `pythonpath = .` |
| `requirements*.txt`, `.env.example`, `.gitignore` | Môi trường |

---

### Task 0: Khung dự án và cấu hình

**Files:**
- Create: `openstack_flask_nhom17/requirements.txt`, `requirements-dev.txt`, `.env.example`, `.gitignore`, `pytest.ini`, `config.py`
- Create: `openstack_flask_nhom17/tests/helpers.py`, `tests/conftest.py`, `tests/test_config.py`

- [ ] **Step 1: Tạo file môi trường**

`requirements.txt`:
```text
flask>=3.0
requests>=2.31
urllib3>=2.0
python-dotenv>=1.0
```

`requirements-dev.txt`:
```text
-r requirements.txt
pytest>=8.0
responses>=0.25
```

`.env.example`:
```text
OS_APP_CRED_ID=
OS_APP_CRED_SECRET=
DEFAULT_IMAGE_ID=
DEFAULT_FLAVOR_ID=
VERIFY_SSL=true
```

`.gitignore`:
```text
.env
.venv/
__pycache__/
.pytest_cache/
```

`pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 2: Tạo virtualenv và cài thư viện**

Run:
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```
Expected: cài thành công `Flask`, `requests`, `python-dotenv`, `pytest`, `responses`.

- [ ] **Step 3: Viết test helper và conftest**

`tests/helpers.py`:
```python
AUTH_URL = "https://cloud-identity.uitiot.vn/v3/auth/tokens"
COMPUTE = "https://cloud-compute.uitiot.vn"
NETWORK = "https://cloud-network.uitiot.vn"
IMAGE = "https://cloud-image.uitiot.vn"
LB = "https://cloud-lb.uitiot.vn"

CATALOG = [
    {"type": "compute", "endpoints": [
        {"interface": "public", "url": COMPUTE + "/v2.1"},
        {"interface": "internal", "url": "http://internal/v2.1"},
    ]},
    {"type": "network", "endpoints": [{"interface": "public", "url": NETWORK}]},
    {"type": "image", "endpoints": [{"interface": "public", "url": IMAGE}]},
    {"type": "load-balancer", "endpoints": [{"interface": "public", "url": LB}]},
]


def calls_summary(rsps):
    """Danh sách (method, url không query) của các request, bỏ qua lời gọi Keystone."""
    return [
        (c.request.method, c.request.url.split("?")[0])
        for c in rsps.calls
        if c.request.url != AUTH_URL
    ]


def mutating_calls(rsps):
    """Các request POST/PUT/DELETE tới OpenStack (không tính Keystone)."""
    return [(m, u) for m, u in calls_summary(rsps) if m in ("POST", "PUT", "DELETE")]
```

`tests/conftest.py`:
```python
import pytest
import responses

import config
from helpers import AUTH_URL, CATALOG
from openstack_api import OpenStackClient


@pytest.fixture(autouse=True)
def fast_config(monkeypatch):
    monkeypatch.setattr(config, "OS_APP_CRED_ID", "cred-id")
    monkeypatch.setattr(config, "OS_APP_CRED_SECRET", "cred-secret")
    monkeypatch.setattr(config, "POLL_INTERVAL", 0)
    monkeypatch.setattr(config, "POLL_TIMEOUT", 0.05)


@pytest.fixture
def mocked():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(
            responses.POST,
            AUTH_URL,
            json={"token": {"catalog": CATALOG}},
            headers={"X-Subject-Token": "tok-1"},
        )
        yield rsps


@pytest.fixture
def client(mocked):
    return OpenStackClient()
```

- [ ] **Step 4: Viết test cấu hình (failing)**

`tests/test_config.py`:
```python
import pytest

import config


def test_validate_raises_when_credentials_missing(monkeypatch):
    monkeypatch.setattr(config, "OS_APP_CRED_ID", "")
    monkeypatch.setattr(config, "OS_APP_CRED_SECRET", "")
    with pytest.raises(RuntimeError) as exc:
        config.validate()
    assert "OS_APP_CRED_ID" in str(exc.value)
    assert "OS_APP_CRED_SECRET" in str(exc.value)


def test_validate_passes_when_credentials_present():
    config.validate()


def test_group_constants():
    assert config.NET_NAME == "nhom17_net"
    assert config.SUBNET_NAME == "nhom17_subnet"
    assert config.ROUTER_NAME == "nhom17_router"
    assert config.CIDR == "192.168.17.0/24"
    assert config.ALLOCATION_POOL == {"start": "192.168.17.101", "end": "192.168.17.200"}
    assert config.HTTP_TIMEOUT > 0
```

- [ ] **Step 5: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'config'`).

- [ ] **Step 6: Viết `config.py`**

```python
"""Cấu hình ứng dụng: đọc secret từ .env và khai báo thông số mạng của Nhóm 17."""
import os

from dotenv import load_dotenv

load_dotenv()

OS_AUTH_URL = os.getenv("OS_AUTH_URL", "https://cloud-identity.uitiot.vn/v3/auth/tokens")
OS_APP_CRED_ID = os.getenv("OS_APP_CRED_ID", "")
OS_APP_CRED_SECRET = os.getenv("OS_APP_CRED_SECRET", "")

# Dùng cho Scale Out tự động (lấy ID từ danh sách Flavor/Image của ứng dụng).
DEFAULT_IMAGE_ID = os.getenv("DEFAULT_IMAGE_ID", "")
DEFAULT_FLAVOR_ID = os.getenv("DEFAULT_FLAVOR_ID", "")

# URL gốc (không kèm phiên bản) dùng khi catalog không có endpoint của dịch vụ.
# Octavia không có fallback: bắt buộc phải có trong Service Catalog.
FALLBACK_ENDPOINTS = {
    "compute": "https://cloud-compute.uitiot.vn",
    "network": "https://cloud-network.uitiot.vn",
    "image": "https://cloud-image.uitiot.vn",
    "load-balancer": None,
}

NET_NAME = "nhom17_net"
SUBNET_NAME = "nhom17_subnet"
ROUTER_NAME = "nhom17_router"
LB_NAME = "nhom17_lb"
CIDR = "192.168.17.0/24"
GATEWAY_IP = "192.168.17.1"
ALLOCATION_POOL = {"start": "192.168.17.101", "end": "192.168.17.200"}
DNS_NAMESERVERS = ["8.8.8.8", "1.1.1.1"]
VM_PREFIX = "nhom17_vm_"

POLL_INTERVAL = 3  # giây
POLL_TIMEOUT = 180  # giây
HTTP_TIMEOUT = 30  # giây
VERIFY_SSL = os.getenv("VERIFY_SSL", "true").strip().lower() != "false"


def validate():
    """Báo lỗi rõ ràng nếu thiếu Application Credential."""
    missing = [
        name
        for name, value in (
            ("OS_APP_CRED_ID", OS_APP_CRED_ID),
            ("OS_APP_CRED_SECRET", OS_APP_CRED_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Thiếu biến môi trường trong file .env: " + ", ".join(missing))
```

- [ ] **Step 7: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 8: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "chore: scaffold project, config and test fixtures"
```

---

### Task 1: Lõi client (xác thực, catalog, `_request`, lỗi)

**Files:**
- Create: `openstack_flask_nhom17/openstack_api.py`
- Test: `openstack_flask_nhom17/tests/test_auth.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_auth.py`:
```python
import json

import pytest
import responses

from helpers import AUTH_URL, COMPUTE, LB, NETWORK
from openstack_api import OpenStackClient, OpenStackError, normalize_base_url


def test_authenticate_uses_application_credential_without_scope(client, mocked):
    client.authenticate()
    body = json.loads(mocked.calls[0].request.body)
    identity = body["auth"]["identity"]
    assert identity["methods"] == ["application_credential"]
    assert identity["application_credential"] == {"id": "cred-id", "secret": "cred-secret"}
    assert "scope" not in body["auth"]
    assert client.token == "tok-1"


def test_catalog_extracts_public_endpoints(client):
    client.authenticate()
    assert client.endpoints["compute"] == COMPUTE
    assert client.endpoints["network"] == NETWORK
    assert client.endpoints["load-balancer"] == LB


def test_authenticate_failure_raises(mocked):
    mocked.replace(responses.POST, AUTH_URL, status=401, json={"error": {"message": "bad credential"}})
    with pytest.raises(OpenStackError) as exc:
        OpenStackClient().authenticate()
    assert exc.value.status == 401
    assert "bad credential" in exc.value.message


def test_normalize_base_url():
    assert normalize_base_url("https://h/v2.1") == "https://h"
    assert normalize_base_url("https://h:9696/") == "https://h:9696"
    assert normalize_base_url("https://h/v2.1/abc123") == "https://h"
    assert normalize_base_url("https://h/load-balancer") == "https://h/load-balancer"
    assert normalize_base_url("https://v2.example.com") == "https://v2.example.com"


def test_request_refreshes_token_on_401(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", status=401, json={})
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": []})
    assert client.list_networks() == []
    auth_calls = [c for c in mocked.calls if c.request.url == AUTH_URL]
    assert len(auth_calls) == 2


def test_request_sends_token_header(mocked, client):
    mocked.add(responses.GET, f"{NETWORK}/v2.0/networks", json={"networks": []})
    client.list_networks()
    assert mocked.calls[-1].request.headers["X-Auth-Token"] == "tok-1"


def test_error_message_extracted(mocked, client):
    mocked.add(
        responses.GET,
        f"{NETWORK}/v2.0/networks",
        status=409,
        json={"NeutronError": {"message": "quota exceeded"}},
    )
    with pytest.raises(OpenStackError) as exc:
        client.list_networks()
    assert exc.value.status == 409
    assert "quota exceeded" in exc.value.message


def test_missing_octavia_endpoint_raises(client):
    client.token = "t"
    client.endpoints = {}
    with pytest.raises(OpenStackError) as exc:
        client._request("GET", "load-balancer", "/v2.0/lbaas/loadbalancers")
    assert "load-balancer" in exc.value.message


def test_fallback_endpoint_used_when_catalog_lacks_service(mocked, client):
    client.token = "t"
    client.endpoints = {}
    mocked.add(responses.GET, f"{COMPUTE}/v2.1/flavors/detail", json={"flavors": []})
    assert client.list_flavors() == []
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_auth.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'openstack_api'`).

- [ ] **Step 3: Viết lõi `openstack_api.py`**

```python
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

    # ----- Yêu cầu 1, 2: Flavor, Image, Network (bổ sung ở Task 2) -----

    def list_flavors(self):
        return self._request("GET", "compute", "/v2.1/flavors/detail")["flavors"]

    def list_images(self):
        return self._request("GET", "image", "/v2/images")["images"]

    def list_networks(self, name=None):
        params = {"name": name} if name else None
        return self._request("GET", "network", "/v2.0/networks", params=params)["networks"]
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest tests/test_auth.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add OpenStackClient core (auth, catalog, request, errors)"
```

---

### Task 2: Yêu cầu 1, 2, 3 - Flavor/Image, Network/Subnet, Router

**Files:**
- Modify: `openstack_flask_nhom17/openstack_api.py` (thêm vào cuối class `OpenStackClient`)
- Test: `openstack_flask_nhom17/tests/test_network.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_network.py`:
```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_network.py -v`
Expected: các test mới FAIL với `AttributeError: 'OpenStackClient' object has no attribute 'create_network'` (hai test `list_flavors`, `list_images` đã pass).

- [ ] **Step 3: Thêm vào cuối class `OpenStackClient` trong `openstack_api.py`**

```python
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
```

Lưu ý: `list_networks` đã có từ Task 1, không thêm lại.

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest tests/test_network.py tests/test_auth.py -v`
Expected: tất cả passed.

- [ ] **Step 5: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add network, subnet and router operations"
```

---

### Task 3: Yêu cầu 4 - Instance, polling server, tạo VM

**Files:**
- Modify: `openstack_flask_nhom17/openstack_api.py`
- Test: `openstack_flask_nhom17/tests/test_compute.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_compute.py`:
```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_compute.py -v`
Expected: các test FAIL với `AttributeError: 'OpenStackClient' object has no attribute 'list_servers'` (hoặc `create_vm`, `next_vm_name`, `assign_floating_ip`); `test_vm_index` và `test_encode_user_data_roundtrip` đã pass vì các hàm đó có từ Task 1.

- [ ] **Step 3: Thêm hằng số script ở đầu `openstack_api.py` (ngay sau `SERVICE_TYPES`)**

```python
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
```

- [ ] **Step 4: Thêm vào cuối class `OpenStackClient`**

```python
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
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest tests/test_compute.py -v`
Expected: tất cả passed.

- [ ] **Step 6: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add instance operations, server polling and floating IP"
```

---

### Task 4: Yêu cầu 5 - Thiết lập mạng 1-Click và Security Group

**Files:**
- Modify: `openstack_flask_nhom17/openstack_api.py`
- Test: `openstack_flask_nhom17/tests/test_setup.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_setup.py`:
```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_setup.py -v`
Expected: FAIL (`AttributeError: ... 'setup_network_1click'`).

- [ ] **Step 3: Thêm hằng số ở đầu `openstack_api.py` (sau `WEB_USER_DATA_SCRIPT`)**

```python
# Rule ingress cần mở trên Security Group "default": (protocol, port).
DEFAULT_SG_RULES = (("tcp", 80), ("tcp", 22), ("icmp", None))
```

- [ ] **Step 4: Thêm vào cuối class `OpenStackClient`**

```python
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
```

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest tests/test_setup.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add idempotent 1-click network setup and security group rules"
```

---

### Task 5: Yêu cầu 7 - Octavia Load Balancer

**Files:**
- Modify: `openstack_flask_nhom17/openstack_api.py`
- Test: `openstack_flask_nhom17/tests/test_loadbalancer.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_loadbalancer.py`:
```python
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


def mock_create_flow(mocked):
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_loadbalancer.py -v`
Expected: FAIL (`AttributeError: ... 'wait_for_lb_active'`).

- [ ] **Step 3: Thêm vào cuối class `OpenStackClient`**

```python
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
```

- [ ] **Step 4: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest tests/test_loadbalancer.py -v`
Expected: tất cả passed.

- [ ] **Step 5: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add Octavia load balancer creation with ACTIVE polling"
```

---

### Task 6: Yêu cầu 8 - Scale Out / Scale In

**Files:**
- Modify: `openstack_flask_nhom17/openstack_api.py`
- Test: `openstack_flask_nhom17/tests/test_scale.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_scale.py`:
```python
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
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_scale.py -v`
Expected: FAIL (`AttributeError: ... 'scale_out'`).

- [ ] **Step 3: Thêm vào cuối class `OpenStackClient`**

```python
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
```

- [ ] **Step 4: Chạy toàn bộ test client, xác nhận pass**

Run: `.venv\Scripts\python -m pytest -v`
Expected: tất cả passed.

- [ ] **Step 5: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add scale out and scale in with load balancer membership"
```

---

### Task 7: Flask server (`app.py`) và envelope JSON

**Files:**
- Create: `openstack_flask_nhom17/app.py`
- Create (placeholder tối thiểu để route `/` chạy được, thay ở Task 8): `openstack_flask_nhom17/templates/index.html`
- Test: `openstack_flask_nhom17/tests/test_app.py`

- [ ] **Step 1: Viết test (failing)**

`tests/test_app.py`:
```python
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


def test_setup_loadbalancer_and_scale_routes(fake, http):
    fake.setup_network_1click.return_value = {"network_id": "n1"}
    fake.create_load_balancer.return_value = {"lb_id": "lb1"}
    fake.scale_out.return_value = {"name": "nhom17_vm_2"}
    fake.scale_in.return_value = {"deleted": "nhom17_vm_2"}
    assert http.post("/api/setup").get_json()["data"] == {"network_id": "n1"}
    assert http.post("/api/loadbalancer").get_json()["data"] == {"lb_id": "lb1"}
    assert http.post("/api/scale-out").get_json()["data"] == {"name": "nhom17_vm_2"}
    assert http.post("/api/scale-in", json={"server_id": "srv2"}).get_json()["data"] == {"deleted": "nhom17_vm_2"}
    assert fake.scale_in.call_args.kwargs == {"server_id": "srv2"}
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_app.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'app'`).

- [ ] **Step 3: Viết `templates/index.html` tạm thời**

```html
<!DOCTYPE html>
<html lang="vi"><head><meta charset="utf-8"><title>Nhóm 17 - NT533</title></head>
<body><h1>Nhóm 17 - NT533</h1></body></html>
```

- [ ] **Step 4: Viết `app.py`**

```python
"""Flask server: giao diện (/) và REST endpoints nội bộ (/api/...) bọc OpenStackClient."""
from flask import Flask, jsonify, render_template, request

import config
from openstack_api import OpenStackClient, OpenStackError

app = Flask(__name__)
client = OpenStackClient()


class BadRequest(Exception):
    pass


def _body():
    return request.get_json(silent=True) or {}


def _require(body, *fields):
    missing = [f for f in fields if not body.get(f)]
    if missing:
        raise BadRequest("Thiếu trường bắt buộc: " + ", ".join(missing))


def _respond(success, message, logs=None, data=None, status=200):
    payload = {
        "success": success,
        "message": message,
        "logs": logs or [],
        "data": data if data is not None else {},
    }
    return jsonify(payload), status


def _handle(action, ok_message):
    """Chạy action(logs) và đóng gói kết quả thành envelope; logs luôn được giữ lại khi lỗi."""
    logs = []
    try:
        data = action(logs)
    except BadRequest as err:
        return _respond(False, str(err), logs, status=400)
    except OpenStackError as err:
        return _respond(False, err.message, logs)
    except Exception as err:  # noqa: BLE001 - trả lỗi bất ngờ về cho giao diện
        return _respond(False, f"Lỗi không lường trước: {err}", logs, status=500)
    return _respond(True, ok_message, logs, data)


@app.get("/")
def index():
    return render_template("index.html")


# ----- Yêu cầu 1 -----

@app.get("/api/flavors")
def api_flavors():
    return _handle(lambda logs: {"items": client.list_flavors()}, "Đã tải danh sách Flavor")


@app.get("/api/images")
def api_images():
    return _handle(lambda logs: {"items": client.list_images()}, "Đã tải danh sách Image")


# ----- Yêu cầu 2 -----

@app.get("/api/networks")
def api_networks():
    return _handle(lambda logs: {"items": client.list_networks()}, "Đã tải danh sách Network")


@app.post("/api/networks")
def api_create_network():
    def action(logs):
        body = _body()
        _require(body, "name")
        net = client.create_network(body["name"])
        logs.append(f"Đã tạo network {net['name']} ({net['id']})")
        return {"item": net}

    return _handle(action, "Đã tạo network")


@app.delete("/api/networks/<network_id>")
def api_delete_network(network_id):
    def action(logs):
        client.delete_network(network_id)
        logs.append(f"Đã xóa network {network_id} cùng các subnet con")
        return {}

    return _handle(action, "Đã xóa network")


@app.get("/api/subnets")
def api_subnets():
    return _handle(lambda logs: {"items": client.list_subnets()}, "Đã tải danh sách Subnet")


@app.post("/api/subnets")
def api_create_subnet():
    def action(logs):
        body = _body()
        _require(body, "network_id", "name", "cidr")
        subnet = client.create_subnet(
            body["network_id"], body["name"], body["cidr"],
            gateway_ip=body.get("gateway_ip") or None,
        )
        logs.append(f"Đã tạo subnet {subnet['name']} ({subnet['id']})")
        return {"item": subnet}

    return _handle(action, "Đã tạo subnet")


@app.delete("/api/subnets/<subnet_id>")
def api_delete_subnet(subnet_id):
    def action(logs):
        client.delete_subnet(subnet_id)
        logs.append(f"Đã xóa subnet {subnet_id}")
        return {}

    return _handle(action, "Đã xóa subnet")


# ----- Yêu cầu 3 -----

@app.get("/api/routers")
def api_routers():
    return _handle(lambda logs: {"items": client.list_routers()}, "Đã tải danh sách Router")


@app.post("/api/routers")
def api_create_router():
    def action(logs):
        body = _body()
        _require(body, "name")
        external_id = client.get_external_network_id() if body.get("external", True) else None
        router = client.create_router(body["name"], external_network_id=external_id)
        logs.append(f"Đã tạo router {router['name']} ({router['id']})")
        return {"item": router}

    return _handle(action, "Đã tạo router")


@app.delete("/api/routers/<router_id>")
def api_delete_router(router_id):
    def action(logs):
        client.delete_router(router_id)
        logs.append(f"Đã gỡ interface và xóa router {router_id}")
        return {}

    return _handle(action, "Đã xóa router")


@app.post("/api/routers/<router_id>/interfaces")
def api_attach_interface(router_id):
    def action(logs):
        body = _body()
        _require(body, "subnet_id")
        client.add_router_interface(router_id, body["subnet_id"])
        logs.append(f"Đã gắn subnet {body['subnet_id']} vào router {router_id}")
        return {}

    return _handle(action, "Đã gắn interface")


@app.delete("/api/routers/<router_id>/interfaces")
def api_detach_interface(router_id):
    def action(logs):
        body = _body()
        _require(body, "subnet_id")
        client.remove_router_interface(router_id, subnet_id=body["subnet_id"])
        logs.append(f"Đã gỡ subnet {body['subnet_id']} khỏi router {router_id}")
        return {}

    return _handle(action, "Đã gỡ interface")


# ----- Yêu cầu 4, 6 -----

@app.get("/api/servers")
def api_servers():
    return _handle(lambda logs: {"items": client.list_servers()}, "Đã tải danh sách máy ảo")


def _create_vm_action(web):
    def action(logs):
        body = _body()
        _require(body, "image_id", "flavor_id")
        name = body.get("name") or client.next_vm_name()
        vm = client.create_vm(name, body["image_id"], body["flavor_id"], web=web, logs=logs)
        return {"item": vm}

    return action


@app.post("/api/servers")
def api_create_server():
    return _handle(_create_vm_action(web=False), "Đã tạo máy ảo")


@app.post("/api/servers/web")
def api_create_web_server():
    return _handle(_create_vm_action(web=True), "Đã tạo máy ảo Web Server (Nginx)")


@app.delete("/api/servers/<server_id>")
def api_delete_server(server_id):
    def action(logs):
        client.delete_server(server_id)
        logs.append(f"Đã gửi yêu cầu xóa máy ảo {server_id}")
        return {}

    return _handle(action, "Đã xóa máy ảo")


@app.post("/api/servers/<server_id>/floating-ip")
def api_floating_ip(server_id):
    return _handle(lambda logs: client.assign_floating_ip(server_id, logs), "Đã gán Floating IP")


# ----- Yêu cầu 5, 7, 8 -----

@app.post("/api/setup")
def api_setup():
    return _handle(client.setup_network_1click, "Thiết lập mạng hoàn tất")


@app.post("/api/loadbalancer")
def api_loadbalancer():
    return _handle(client.create_load_balancer, "Đã tạo Load Balancer")


@app.post("/api/scale-out")
def api_scale_out():
    return _handle(client.scale_out, "Scale Out hoàn tất")


@app.post("/api/scale-in")
def api_scale_in():
    def action(logs):
        return client.scale_in(logs, server_id=_body().get("server_id") or None)

    return _handle(action, "Scale In hoàn tất")


if __name__ == "__main__":
    config.validate()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
```

Lưu ý: `_handle(client.setup_network_1click, ...)` gọi `client.setup_network_1click(logs)`. Test thay `app_module.client` bằng mock trước khi route chạy, nên tham chiếu `client.xxx` được đánh giá lúc request, đúng như test mong đợi.

- [ ] **Step 5: Chạy test, xác nhận pass**

Run: `.venv\Scripts\python -m pytest -v`
Expected: tất cả passed.

- [ ] **Step 6: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add Flask routes with JSON envelope"
```

---

### Task 8: Giao diện dashboard (`templates/index.html`)

**Files:**
- Modify (ghi đè bản tạm): `openstack_flask_nhom17/templates/index.html`
- Test: `openstack_flask_nhom17/tests/test_frontend.py`

- [ ] **Step 1: Viết test smoke (failing)**

`tests/test_frontend.py`:
```python
import pytest

import app as app_module

REQUIRED_IDS = [
    "logBox", "spinnerBox", "toastBox", "selImage", "selFlavor",
    "btnSetup", "btnCreateLb", "btnScaleOut", "btnScaleIn", "selScaleIn",
    "tblFlavors", "tblImages", "tblNetworks", "tblSubnets", "tblRouters", "tblServers",
    "btnCreateVm", "btnCreateWebVm", "btnCreateNet", "btnCreateSubnet", "btnCreateRouter",
]


@pytest.fixture
def page():
    app_module.app.config["TESTING"] = True
    res = app_module.app.test_client().get("/")
    assert res.status_code == 200
    return res.get_data(as_text=True)


@pytest.mark.parametrize("element_id", REQUIRED_IDS)
def test_page_has_element(page, element_id):
    assert f'id="{element_id}"' in page


def test_page_has_title_and_single_h1(page):
    assert "<title>Nhóm 17 - NT533" in page
    assert page.count("<h1") == 1
    assert "Nhật ký hệ thống (Activity Logs)" in page
    assert "Đang xử lý, vui lòng đợi 1-2 phút..." in page
```

- [ ] **Step 2: Chạy test, xác nhận fail**

Run: `.venv\Scripts\python -m pytest tests/test_frontend.py -v`
Expected: FAIL (thiếu các id).

- [ ] **Step 3: Ghi đè `templates/index.html`**

```html
<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nhóm 17 - NT533 | OpenStack Dashboard</title>
  <meta name="description" content="Dashboard quản lý OpenStack NC-Cloud qua REST API - Nhóm 17, môn NT533.">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body { background: #f1f4f9; }
    .topbar { background: linear-gradient(120deg, #0f2027, #203a43 55%, #2c5364); color: #fff; }
    .card { border: 0; border-radius: .9rem; box-shadow: 0 6px 20px rgba(15, 32, 39, .08); }
    .nav-tabs .nav-link { border: 0; color: #4a5a68; font-weight: 500; }
    .nav-tabs .nav-link.active { color: #0f2027; border-bottom: 3px solid #2c5364; background: transparent; }
    .log-box { background: #0b1a22; color: #b6f2c9; font-family: Consolas, monospace; font-size: .82rem;
               border-radius: .6rem; padding: .8rem; height: 60vh; overflow-y: auto; white-space: pre-wrap; }
    .sticky-panel { position: sticky; top: 1rem; }
    .btn { transition: transform .12s ease, box-shadow .12s ease; }
    .btn:not(:disabled):hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(0,0,0,.15); }
  </style>
</head>
<body>
  <header class="topbar py-3 mb-4">
    <div class="container-fluid px-4">
      <h1 class="h4 mb-0">Nhóm 17 - NT533: OpenStack Management Dashboard</h1>
      <small class="opacity-75">NC-Cloud (cloud.uitiot.vn) - REST API thuần bằng Python Flask</small>
    </div>
  </header>

  <div id="toastBox" class="toast-container position-fixed top-0 end-0 p-3" style="z-index: 1100"></div>

  <main class="container-fluid px-4 pb-5">
    <div id="spinnerBox" class="alert alert-info d-none d-flex align-items-center gap-3" role="status">
      <div class="spinner-border spinner-border-sm" aria-hidden="true"></div>
      <span>Đang xử lý, vui lòng đợi 1-2 phút...</span>
    </div>

    <div class="row g-4">
      <section class="col-lg-8">
        <div class="card p-3">
          <ul class="nav nav-tabs mb-3" role="tablist">
            <li class="nav-item"><button class="nav-link active" data-bs-toggle="tab" data-bs-target="#tabCatalog" type="button">Flavor &amp; Image</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tabNetwork" type="button">Network &amp; Subnet</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tabRouter" type="button">Router</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tabInstance" type="button">Instance</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tabSetup" type="button">1-Click &amp; Load Balancer</button></li>
            <li class="nav-item"><button class="nav-link" data-bs-toggle="tab" data-bs-target="#tabScale" type="button">Scale</button></li>
          </ul>

          <div class="tab-content">
            <div class="tab-pane fade show active" id="tabCatalog">
              <div class="d-flex justify-content-between mb-2">
                <h2 class="h6">Flavors</h2>
                <button class="btn btn-sm btn-outline-secondary" id="btnReloadCatalog">Tải lại</button>
              </div>
              <div id="tblFlavors" class="table-responsive"></div>
              <h2 class="h6 mt-3">Images</h2>
              <div id="tblImages" class="table-responsive"></div>
            </div>

            <div class="tab-pane fade" id="tabNetwork">
              <h2 class="h6">Network</h2>
              <div class="input-group mb-2">
                <input id="inNetName" class="form-control" placeholder="Tên network (vd: nhom17_net)">
                <button class="btn btn-primary" id="btnCreateNet">Tạo network</button>
              </div>
              <div id="tblNetworks" class="table-responsive"></div>
              <h2 class="h6 mt-3">Subnet</h2>
              <div class="row g-2 mb-2">
                <div class="col-md-3"><input id="inSubName" class="form-control" placeholder="Tên subnet"></div>
                <div class="col-md-3"><select id="selSubNet" class="form-select"></select></div>
                <div class="col-md-3"><input id="inSubCidr" class="form-control" placeholder="CIDR 192.168.17.0/24"></div>
                <div class="col-md-2"><input id="inSubGw" class="form-control" placeholder="Gateway (tùy chọn)"></div>
                <div class="col-md-1 d-grid"><button class="btn btn-primary" id="btnCreateSubnet">Tạo</button></div>
              </div>
              <div id="tblSubnets" class="table-responsive"></div>
            </div>

            <div class="tab-pane fade" id="tabRouter">
              <div class="input-group mb-2">
                <input id="inRouterName" class="form-control" placeholder="Tên router (vd: nhom17_router)">
                <div class="input-group-text"><input class="form-check-input mt-0 me-2" type="checkbox" id="chkExternal" checked> Nối External Network</div>
                <button class="btn btn-primary" id="btnCreateRouter">Tạo router</button>
              </div>
              <div id="tblRouters" class="table-responsive"></div>
              <h2 class="h6 mt-3">Gắn / gỡ interface subnet</h2>
              <div class="row g-2">
                <div class="col-md-4"><select id="selRouter" class="form-select"></select></div>
                <div class="col-md-4"><select id="selIfaceSubnet" class="form-select"></select></div>
                <div class="col-md-2 d-grid"><button class="btn btn-success" id="btnAttach">Gắn</button></div>
                <div class="col-md-2 d-grid"><button class="btn btn-outline-danger" id="btnDetach">Gỡ</button></div>
              </div>
            </div>

            <div class="tab-pane fade" id="tabInstance">
              <div class="row g-2 mb-2">
                <div class="col-md-3"><input id="inVmName" class="form-control" placeholder="Tên VM (để trống = tự đặt)"></div>
                <div class="col-md-4"><select id="selImage" class="form-select"></select></div>
                <div class="col-md-5"><select id="selFlavor" class="form-select"></select></div>
              </div>
              <div class="d-flex gap-2 mb-3">
                <button class="btn btn-primary" id="btnCreateVm">Tạo VM thường</button>
                <button class="btn btn-success" id="btnCreateWebVm">Tạo VM Web Server (Cloud-Init Nginx)</button>
                <button class="btn btn-outline-secondary ms-auto" id="btnReloadServers">Tải lại</button>
              </div>
              <div id="tblServers" class="table-responsive"></div>
            </div>

            <div class="tab-pane fade" id="tabSetup">
              <p class="text-muted">Tự động tạo <code>nhom17_net</code>, <code>nhom17_subnet</code>, <code>nhom17_router</code> và mở các port 80, 22, ICMP trên Security Group default. Chạy lại nhiều lần vẫn an toàn.</p>
              <button class="btn btn-primary mb-4" id="btnSetup">Thiết lập mạng 1-Click</button>
              <p class="text-muted">Tạo Load Balancer, Listener HTTP:80, Pool ROUND_ROBIN, thêm các VM nhom17_vm_* và gán Floating IP cho VIP.</p>
              <button class="btn btn-success" id="btnCreateLb">Tạo Load Balancer</button>
              <pre id="setupResult" class="mt-3 mb-0 small"></pre>
            </div>

            <div class="tab-pane fade" id="tabScale">
              <p class="text-muted">Scale Out dùng DEFAULT_IMAGE_ID và DEFAULT_FLAVOR_ID trong <code>.env</code>, VM mới không có Floating IP riêng.</p>
              <button class="btn btn-success mb-4" id="btnScaleOut">Scale Out (+1 VM)</button>
              <div class="row g-2">
                <div class="col-md-6"><select id="selScaleIn" class="form-select"></select></div>
                <div class="col-md-3 d-grid"><button class="btn btn-danger" id="btnScaleIn">Scale In (-1 VM)</button></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside class="col-lg-4">
        <div class="card p-3 sticky-panel">
          <div class="d-flex justify-content-between align-items-center mb-2">
            <h2 class="h6 mb-0">Nhật ký hệ thống (Activity Logs)</h2>
            <button class="btn btn-sm btn-outline-secondary" id="btnClearLogs">Xóa</button>
          </div>
          <div id="logBox" class="log-box"></div>
        </div>
      </aside>
    </div>
  </main>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    const $ = (selector) => document.querySelector(selector);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

    async function api(method, url, body) {
      try {
        const res = await fetch(url, {
          method,
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        return await res.json();
      } catch (err) {
        return { success: false, message: "Không kết nối được server: " + err.message, logs: [], data: {} };
      }
    }

    function toast(ok, message) {
      const el = document.createElement("div");
      el.className = `toast align-items-center text-bg-${ok ? "success" : "danger"} border-0`;
      el.innerHTML = `<div class="d-flex"><div class="toast-body">${esc(message)}</div>` +
        `<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
      $("#toastBox").appendChild(el);
      el.addEventListener("hidden.bs.toast", () => el.remove());
      new bootstrap.Toast(el, { delay: 6000 }).show();
    }

    function writeLogs(title, res) {
      const box = $("#logBox");
      const stamp = new Date().toLocaleTimeString("vi-VN");
      const lines = [`[${stamp}] ${res.success ? "OK" : "LỖI"} - ${title}: ${res.message}`]
        .concat((res.logs || []).map((line) => "    " + line));
      box.textContent += lines.join("\n") + "\n\n";
      box.scrollTop = box.scrollHeight;
    }

    // Khóa nút, hiện spinner với tác vụ dài, hiển thị Toast và ghi Activity Logs khi có kết quả.
    async function run(btn, title, method, url, body, long = false) {
      btn.disabled = true;
      if (long) $("#spinnerBox").classList.remove("d-none");
      const res = await api(method, url, body);
      if (long) $("#spinnerBox").classList.add("d-none");
      btn.disabled = false;
      toast(res.success, res.message);
      writeLogs(title, res);
      return res;
    }

    function table(selector, headers, rows) {
      const head = headers.map((h) => `<th>${h}</th>`).join("");
      const body = rows.length
        ? rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")
        : `<tr><td colspan="${headers.length}" class="text-muted">Không có dữ liệu</td></tr>`;
      $(selector).innerHTML = `<table class="table table-sm table-hover align-middle"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
    }

    function fillSelect(selector, items, label, placeholder) {
      const options = (placeholder ? [`<option value="">${esc(placeholder)}</option>`] : [])
        .concat(items.map((i) => `<option value="${esc(i.id)}">${esc(label(i))}</option>`));
      $(selector).innerHTML = options.join("");
    }

    async function loadList(path) {
      const res = await api("GET", path);
      if (!res.success) { toast(false, res.message); return []; }
      return res.data.items;
    }

    const delBtn = (act, id) => `<button class="btn btn-sm btn-outline-danger" data-act="${act}" data-id="${esc(id)}">Xóa</button>`;

    async function loadFlavors() {
      const items = await loadList("/api/flavors");
      table("#tblFlavors", ["Tên", "vCPUs", "RAM (MB)", "Disk (GB)"],
        items.map((f) => [esc(f.name), esc(f.vcpus), esc(f.ram), esc(f.disk)]));
      fillSelect("#selFlavor", items, (f) => `${f.name} (${f.vcpus} vCPU, ${f.ram} MB, ${f.disk} GB)`, "Chọn Flavor");
    }

    async function loadImages() {
      const items = await loadList("/api/images");
      table("#tblImages", ["Tên", "Trạng thái", "Dung lượng (MB)"],
        items.map((i) => [esc(i.name), esc(i.status), i.size ? (i.size / 1048576).toFixed(1) : "-"]));
      fillSelect("#selImage", items, (i) => i.name, "Chọn Image");
    }

    async function loadNetworks() {
      const items = await loadList("/api/networks");
      table("#tblNetworks", ["Tên", "ID", "Trạng thái", ""],
        items.map((n) => [esc(n.name), esc(n.id), esc(n.status), delBtn("del-net", n.id)]));
      fillSelect("#selSubNet", items, (n) => n.name, "Chọn network");
    }

    async function loadSubnets() {
      const items = await loadList("/api/subnets");
      table("#tblSubnets", ["Tên", "CIDR", "Gateway", ""],
        items.map((s) => [esc(s.name), esc(s.cidr), esc(s.gateway_ip), delBtn("del-subnet", s.id)]));
      fillSelect("#selIfaceSubnet", items, (s) => `${s.name} (${s.cidr})`, "Chọn subnet");
    }

    async function loadRouters() {
      const items = await loadList("/api/routers");
      table("#tblRouters", ["Tên", "ID", "Trạng thái", ""],
        items.map((r) => [esc(r.name), esc(r.id), esc(r.status), delBtn("del-router", r.id)]));
      fillSelect("#selRouter", items, (r) => r.name, "Chọn router");
    }

    async function loadServers() {
      const items = await loadList("/api/servers");
      table("#tblServers", ["Tên", "Trạng thái", "IP nội bộ", "Floating IP", ""],
        items.map((s) => [
          esc(s.name), esc(s.status), esc(s.fixed_ips.join(", ")), esc(s.floating_ips.join(", ") || "-"),
          `<button class="btn btn-sm btn-outline-primary me-1" data-act="fip" data-id="${esc(s.id)}">Gán Floating IP</button>` +
          delBtn("del-server", s.id),
        ]));
      const vms = items.filter((s) => /^nhom17_vm_\d+$/.test(s.name));
      fillSelect("#selScaleIn", vms, (s) => s.name, "Tự chọn VM cuối (nhom17_vm_N lớn nhất)");
    }

    const refreshNetworkTabs = () => Promise.all([loadNetworks(), loadSubnets()]);

    document.addEventListener("click", async (event) => {
      const btn = event.target.closest("[data-act]");
      if (!btn) return;
      const id = btn.dataset.id;
      if (btn.dataset.act === "del-net" && confirm("Xóa network này cùng các subnet con?")) {
        await run(btn, "Xóa network", "DELETE", `/api/networks/${id}`); await refreshNetworkTabs();
      } else if (btn.dataset.act === "del-subnet" && confirm("Xóa subnet này?")) {
        await run(btn, "Xóa subnet", "DELETE", `/api/subnets/${id}`); await loadSubnets();
      } else if (btn.dataset.act === "del-router" && confirm("Gỡ interface và xóa router này?")) {
        await run(btn, "Xóa router", "DELETE", `/api/routers/${id}`); await loadRouters();
      } else if (btn.dataset.act === "del-server" && confirm("Xóa máy ảo này?")) {
        await run(btn, "Xóa máy ảo", "DELETE", `/api/servers/${id}`); await loadServers();
      } else if (btn.dataset.act === "fip") {
        await run(btn, "Gán Floating IP", "POST", `/api/servers/${id}/floating-ip`); await loadServers();
      }
    });

    $("#btnReloadCatalog").onclick = () => Promise.all([loadFlavors(), loadImages()]);
    $("#btnReloadServers").onclick = loadServers;
    $("#btnClearLogs").onclick = () => { $("#logBox").textContent = ""; };

    $("#btnCreateNet").onclick = async (e) => {
      await run(e.target, "Tạo network", "POST", "/api/networks", { name: $("#inNetName").value.trim() });
      await refreshNetworkTabs();
    };
    $("#btnCreateSubnet").onclick = async (e) => {
      await run(e.target, "Tạo subnet", "POST", "/api/subnets", {
        name: $("#inSubName").value.trim(), network_id: $("#selSubNet").value,
        cidr: $("#inSubCidr").value.trim(), gateway_ip: $("#inSubGw").value.trim(),
      });
      await loadSubnets();
    };
    $("#btnCreateRouter").onclick = async (e) => {
      await run(e.target, "Tạo router", "POST", "/api/routers",
        { name: $("#inRouterName").value.trim(), external: $("#chkExternal").checked });
      await loadRouters();
    };
    $("#btnAttach").onclick = (e) => run(e.target, "Gắn interface", "POST",
      `/api/routers/${$("#selRouter").value}/interfaces`, { subnet_id: $("#selIfaceSubnet").value });
    $("#btnDetach").onclick = (e) => run(e.target, "Gỡ interface", "DELETE",
      `/api/routers/${$("#selRouter").value}/interfaces`, { subnet_id: $("#selIfaceSubnet").value });

    const vmPayload = () => ({
      name: $("#inVmName").value.trim(), image_id: $("#selImage").value, flavor_id: $("#selFlavor").value,
    });
    $("#btnCreateVm").onclick = async (e) => {
      await run(e.target, "Tạo VM thường", "POST", "/api/servers", vmPayload(), true); await loadServers();
    };
    $("#btnCreateWebVm").onclick = async (e) => {
      await run(e.target, "Tạo VM Web Server", "POST", "/api/servers/web", vmPayload(), true); await loadServers();
    };

    $("#btnSetup").onclick = async (e) => {
      const res = await run(e.target, "Thiết lập mạng 1-Click", "POST", "/api/setup", null, true);
      $("#setupResult").textContent = JSON.stringify(res.data, null, 2);
      await Promise.all([refreshNetworkTabs(), loadRouters()]);
    };
    $("#btnCreateLb").onclick = async (e) => {
      const res = await run(e.target, "Tạo Load Balancer", "POST", "/api/loadbalancer", null, true);
      $("#setupResult").textContent = JSON.stringify(res.data, null, 2);
    };
    $("#btnScaleOut").onclick = async (e) => {
      await run(e.target, "Scale Out", "POST", "/api/scale-out", null, true); await loadServers();
    };
    $("#btnScaleIn").onclick = async (e) => {
      await run(e.target, "Scale In", "POST", "/api/scale-in", { server_id: $("#selScaleIn").value }, true);
      await loadServers();
    };

    Promise.all([loadFlavors(), loadImages(), refreshNetworkTabs(), loadRouters(), loadServers()]);
  </script>
</body>
</html>
```

- [ ] **Step 4: Chạy toàn bộ test**

Run: `.venv\Scripts\python -m pytest -v`
Expected: tất cả passed.

- [ ] **Step 5: Commit**

```powershell
git add openstack_flask_nhom17
git commit -m "feat: add Bootstrap dashboard with activity logs and spinner"
```

---

### Task 9: Tài liệu chạy và kiểm thử tích hợp thật

**Files:**
- Modify: `README.md` (thêm mục hướng dẫn ở cuối file, giữ nội dung hiện có)

- [ ] **Step 1: Thêm vào cuối `README.md`**

````markdown

## Chạy ứng dụng (Nhóm 17 - NT533 Lab 2)

```powershell
cd openstack_flask_nhom17
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
# Điền OS_APP_CRED_ID và OS_APP_CRED_SECRET vào .env
.venv\Scripts\python app.py
```

Mở http://127.0.0.1:5000. Chạy unit test (không gọi cloud thật): `.venv\Scripts\python -m pytest`.

Sau khi giao diện tải được danh sách Flavor/Image, điền `DEFAULT_IMAGE_ID` và `DEFAULT_FLAVOR_ID` vào `.env` rồi khởi động lại để dùng Scale Out.
````

- [ ] **Step 2: Điền `.env` (do người dùng làm, không commit)**

Người dùng tự copy `.env.example` thành `.env` và điền Application Credential của nhom17. Xác nhận `git status` không liệt kê `.env`.

Run: `git status --short`
Expected: không có dòng nào chứa `.env` (chỉ `.env.example`).

- [ ] **Step 3: Chạy server và kiểm thử tích hợp theo thứ tự**

Run: `.venv\Scripts\python app.py`
Mở http://127.0.0.1:5000 và đi qua từng mục, chụp ảnh giao diện cùng Activity Logs cho báo cáo:

| # | Hành động | Kết quả mong đợi |
|---|-----------|------------------|
| 1 | Tab Flavor & Image | Hiện bảng Flavor, Image; hai dropdown ở tab Instance có dữ liệu |
| 2 | Tab Network: tạo, xóa network và subnet thử | Danh sách cập nhật, Toast xanh |
| 3 | Tab Router: tạo, gắn/gỡ interface, xóa thử | Thành công, log ghi từng bước |
| 5 | Tab 1-Click: bấm "Thiết lập mạng 1-Click", bấm lần hai | Lần 1 tạo net/subnet/router + SG; lần 2 log "đã tồn tại", không tạo trùng |
| 4, 6 | Tab Instance: tạo VM Web Server, gán Floating IP | VM ACTIVE, IP `192.168.17.101`; mở `http://<Floating IP>` thấy "Nhóm 17 - NT533" và IP nội bộ |
| 7 | Tab 1-Click: bấm "Tạo Load Balancer" | LB, Listener, Pool, Member, Floating IP VIP; mở `http://<Floating IP VIP>` thấy trang web |
| 8 | Điền DEFAULT ids vào `.env`, khởi động lại; Tab Scale: Scale Out, refresh trang LB vài lần; Scale In | VM mới `nhom17_vm_2` vào Pool, trang LB luân phiên IP; Scale In xóa member rồi xóa VM |

Nếu một bước lỗi, đọc `message` và `logs` trong Activity Logs; sửa code và thêm test hồi quy trước khi sửa tiếp.

- [ ] **Step 4: Commit**

```powershell
git add README.md
git commit -m "docs: add run instructions and integration checklist"
```

---

## Self-Review

**Spec coverage:**
- Mục 1 (phạm vi, bỏ Keypair, `key_name=None`): Task 3 `create_server` (kiểm bằng `test_create_server_payload_without_key_name`).
- Mục 2-3 (kiến trúc, `.env`, `config.py`, `DEFAULT_*`, `VERIFY_SSL`): Task 0.
- Mục 4 auth, refresh 401, catalog, fallback, polling: Task 1, Task 3 (`wait_for_server_active`), Task 5 (`wait_for_lb_active`).
- Yêu cầu 1: Task 2 + endpoint `/api/flavors`, `/api/images` Task 7 + dropdown Task 8.
- Yêu cầu 2, 3: Task 2, Task 7, Task 8.
- Yêu cầu 4: Task 3, Task 7, Task 8.
- Yêu cầu 5 (idempotent, SG): Task 4.
- Yêu cầu 6 (user_data, Floating IP): Task 3 (`create_vm`, `assign_floating_ip`), Task 7 (`/api/servers/web`, `/floating-ip`).
- Yêu cầu 7: Task 5.
- Yêu cầu 8 (không Floating IP cho VM scale out, giữ VM khi lỗi, từ chối còn 1 VM): Task 6.
- Mục 5 (envelope, mã HTTP 200/400/500, spinner, Toast, Activity Logs, `threaded=True`): Task 7, Task 8.
- Mục 6 (không lộ secret, không marker trích dẫn): `.gitignore`, `.env.example`, Task 0; `logs` không chứa token/secret (không có lệnh log nào ghi chúng).
- Mục 7 (unit test mock + test thật): Task 0-8 và Task 9.
- Mục 8 (credential, `DEFAULT_*`): Task 9.

**Placeholder scan:** không có "TBD/TODO". `index.html` tạm ở Task 7 là bản tối thiểu có chủ đích và được thay hoàn toàn ở Task 8.

**Type consistency:** `create_vm(name, image_id, flavor_id, web=False, logs=None)` dùng nhất quán ở Task 3, 6, 7. `add_pool_member(lb_id, pool_id, address, subnet_id, logs=None)` và `remove_pool_member(lb_id, pool_id, member_id, logs=None)` nhất quán Task 5, 6. `wait_for_lb_active` trả dict LB, dùng ở Task 5. `list_nhom17_vms()` (Task 3) được dùng ở Task 5, 6 và các phần tử có `index`, `fixed_ips`, `id`, `name`. Kết quả `scale_in` là `{"deleted", "member_removed"}` khớp test. `setup_network_1click` trả `{network_id, subnet_id, router_id}` khớp test.
