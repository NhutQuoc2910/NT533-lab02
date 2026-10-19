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
