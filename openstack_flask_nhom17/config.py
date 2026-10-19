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
