"""Cấu hình ứng dụng: đọc secret từ .env và khai báo thông số mạng của Nhóm 17."""
import os

from dotenv import load_dotenv

load_dotenv()

OS_AUTH_URL = os.getenv("OS_AUTH_URL", "https://cloud-identity.uitiot.vn/v3/auth/tokens")
OS_APP_CRED_ID = os.getenv("OS_APP_CRED_ID", "")
OS_APP_CRED_SECRET = os.getenv("OS_APP_CRED_SECRET", "")

DEFAULT_IMAGE_ID = os.getenv("DEFAULT_IMAGE_ID", "")
DEFAULT_FLAVOR_ID = os.getenv("DEFAULT_FLAVOR_ID", "")

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
LB_PROVIDER = os.getenv("LB_PROVIDER", "amphora")

POLL_INTERVAL = 3
POLL_TIMEOUT = 300
HTTP_TIMEOUT = 30
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
