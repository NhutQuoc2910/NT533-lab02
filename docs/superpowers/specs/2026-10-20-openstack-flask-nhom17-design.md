# Thiết kế: OpenStack REST API Management Web App (Flask) - Nhóm 17

Ngày: 2026-10-20
Nguồn: `context.md` + 6 quyết định chốt của người dùng (Keypair, Image/Flavor, `.env`, Scale Out, tác vụ dài, marker trích dẫn).

## 1. Mục tiêu và phạm vi

Web app Python Flask quản lý OpenStack trên NC-Cloud (`https://cloud.uitiot.vn`) hoàn toàn qua REST API thuần (`requests`), phục vụ 8 yêu cầu của Lab 2 môn NT533.

Trong phạm vi: Flavor/Image, Network/Subnet, Router, Instance, thiết lập mạng 1-Click, VM kèm Cloud-Init Nginx và Floating IP, Octavia Load Balancer, Scale Out/Scale In.

Ngoài phạm vi (YAGNI):
- Quản lý Keypair. Tạo server không truyền `key_name` (tham số tùy chọn, mặc định `None`).
- WebSocket, Celery, hàng đợi nền, đăng nhập người dùng cho web app.
- Mọi SDK OpenStack (`openstacksdk`, `python-*client`) bị cấm.

## 2. Kiến trúc

Ba lớp, mỗi lớp có một trách nhiệm:

| Lớp | File | Trách nhiệm | Phụ thuộc |
|-----|------|-------------|-----------|
| Cấu hình | `config.py` | Đọc `.env`, khai báo URL, tham số mạng nhom17, giá trị mặc định | `python-dotenv`, `os` |
| Client | `openstack_api.py` | Xác thực Keystone, gọi REST từng dịch vụ, polling. Không biết gì về Flask | `requests`, `config` |
| Web | `app.py` | Route giao diện và `/api/...`, bọc kết quả thành envelope JSON | `flask`, `openstack_api` |
| Giao diện | `templates/index.html` | Dashboard Bootstrap 5, `fetch` AJAX | Bootstrap CDN |

Luồng orchestration (1-Click, tạo VM + Floating IP, LB, Scale) nằm trong `OpenStackClient` dưới dạng các hàm cấp cao nhận một danh sách `logs` để ghi lại từng bước. `app.py` chỉ gọi hàm và đóng gói kết quả.

### Cấu trúc thư mục

```text
openstack_flask_nhom17/
├── config.py
├── openstack_api.py
├── app.py
├── requirements.txt         # flask, requests, urllib3, python-dotenv
├── requirements-dev.txt     # pytest, responses
├── .env.example             # mẫu, được commit
├── .env                     # thật, KHÔNG commit
├── .gitignore               # .env, __pycache__/, .venv/
├── templates/
│   └── index.html
└── tests/
    ├── test_auth.py
    ├── test_network.py
    ├── test_compute.py
    └── test_loadbalancer.py
```

## 3. Cấu hình (`config.py`, `.env`)

`.env.example`:

```text
OS_APP_CRED_ID=
OS_APP_CRED_SECRET=
DEFAULT_IMAGE_ID=
DEFAULT_FLAVOR_ID=
```

`config.py` đọc bằng `os.getenv()` sau `load_dotenv()`:
- `OS_AUTH_URL = https://cloud-identity.uitiot.vn/v3/auth/tokens`
- `OS_APP_CRED_ID`, `OS_APP_CRED_SECRET`: bắt buộc, thiếu thì báo lỗi rõ khi khởi động.
- `DEFAULT_IMAGE_ID`, `DEFAULT_FLAVOR_ID`: dùng cho Scale Out. Người dùng điền sau khi xem danh sách ở Yêu cầu 1.
- Fallback endpoint: compute `https://cloud-compute.uitiot.vn/v2.1`, network `https://cloud-network.uitiot.vn/v2.0`, image `https://cloud-image.uitiot.vn/v2/images`. Octavia không có fallback, bắt buộc có trong catalog.
- Tham số nhom17: `NET_NAME=nhom17_net`, `SUBNET_NAME=nhom17_subnet`, `ROUTER_NAME=nhom17_router`, `CIDR=192.168.17.0/24`, `GATEWAY_IP=192.168.17.1`, pool `192.168.17.101` đến `192.168.17.200`, DNS `8.8.8.8`, `1.1.1.1`, `VM_PREFIX=nhom17_vm_`.
- `POLL_INTERVAL=3`, `POLL_TIMEOUT=180` (giây).
- `VERIFY_SSL` mặc định `True`. Tắt được bằng env nếu chứng chỉ trường có vấn đề (cho nên có `urllib3`).

## 4. `OpenStackClient`

### Xác thực
- `POST` Keystone với `methods: ["application_credential"]`, `id`, `secret`. Không có khối `scope`.
- Token lấy từ header `X-Subject-Token`. Endpoint `public` của `compute`, `network`, `image`, `load-balancer` lấy từ `token.catalog`, thiếu thì dùng fallback.
- Hàm `_request(method, service, path, **kw)` tự gắn `X-Auth-Token`. Gặp `401` thì lấy token mới và thử lại đúng một lần.
- Lỗi HTTP khác ném `OpenStackError(status, message)`, message lấy từ body lỗi của OpenStack.

### Nhóm hàm theo yêu cầu

| Yêu cầu | Hàm chính |
|---------|-----------|
| 1 | `list_flavors()` (`/flavors/detail`), `list_images()` |
| 2 | `list/create/delete_network`, `list/create/delete_subnet` |
| 3 | `list/create_router` (có `external_gateway_info`), `add/remove_router_interface`, `delete_router` |
| 4 | `list_servers()` kèm IP nội bộ và Floating IP, `create_server(name, image_id, flavor_id, network_id, user_data=None, key_name=None)`, `delete_server` |
| 5 | `setup_network_1click(logs)` |
| 6 | `create_web_vm(name, image_id, flavor_id, logs)`, `assign_floating_ip(server_id, logs)` |
| 7 | `create_load_balancer(logs)`, `wait_for_lb_active(lb_id)` |
| 8 | `scale_out(logs)`, `scale_in(logs, server_id=None)` |

### Quy tắc thực thi
- `External Network` tìm động qua `GET /networks?router:external=true`, lấy cái đầu tiên.
- `setup_network_1click` idempotent: mỗi tài nguyên (net, subnet, router, interface) được kiểm tra theo tên trước, đã có thì ghi log "đã tồn tại" và bỏ qua. Rule Security Group `default` (TCP 80, TCP 22, ICMP, ingress) chỉ thêm khi chưa có, trùng rule (`409`) được coi là đã có.
- Xóa Router: gỡ hết interface trước. Xóa Network: xóa Subnet con trước.
- `user_data`: bash script cài `nginx`, ghi `/var/www/html/index.html` chứa "Nhóm 17 - NT533" và kết quả `hostname -I`, mã hóa Base64.
- `assign_floating_ip`: cấp Floating IP từ External Network, tìm `port_id` của VM qua `GET /ports?device_id=<server_id>`, gán vào.
- IP của VM không được ghim, lấy từ DHCP pool bắt đầu `.101`. Sau nhiều lần xóa/tạo, số thứ tự trong tên VM có thể không khớp đuôi IP. Chấp nhận được.

### Polling
- `wait_for_lb_active(lb_id)`: gọi `GET /lbaas/loadbalancers/{id}` mỗi `POLL_INTERVAL`=3s, tối đa `POLL_TIMEOUT`=180s. Thoát khi `provisioning_status == "ACTIVE"`. Gặp `ERROR` hoặc quá timeout thì ném `OpenStackError` với thông báo nêu rõ trạng thái và `lb_id`.
- `wait_for_server_active(server_id)`: cùng chu kỳ và timeout, gặp `ERROR` thì ném lỗi, trả về IP nội bộ.
- Gọi `wait_for_lb_active` trước và sau mỗi bước tạo/xóa LB, Listener, Pool, Member.

### Load Balancer (Yêu cầu 7)
Tạo LB trên `nhom17_subnet`, chờ ACTIVE. Tạo Listener (`HTTP`, 80), chờ. Tạo Pool (`ROUND_ROBIN`, `HTTP`), chờ. Thêm từng VM hiện có vào Pool (port 80), chờ sau mỗi member. Cấp Floating IP và gán vào `vip_port_id` của LB.

### Scale Out (+1 VM)
1. Tìm VM `nhom17_vm_N` có `N` lớn nhất, số kế tiếp là `N+1`.
2. Tạo server bằng `DEFAULT_IMAGE_ID`, `DEFAULT_FLAVOR_ID`, `nhom17_net`, kèm `user_data` Nginx. Không gán Floating IP.
3. `wait_for_server_active`, lấy IP nội bộ.
4. `wait_for_lb_active`, tạo Pool Member (IP, port 80), `wait_for_lb_active`.
5. Nếu bước 4 lỗi thì giữ VM đã tạo và ghi rõ trong logs để người dùng xử lý (không tự rollback).

### Scale In (-1 VM)
1. Chọn VM do người dùng chỉ định hoặc VM có `N` lớn nhất. Từ chối nếu chỉ còn 1 VM.
2. Tìm Member trong Pool theo địa chỉ IP nội bộ của VM.
3. `wait_for_lb_active`, xóa Member, `wait_for_lb_active`.
4. `DELETE /servers/{id}`.

## 5. Web layer

### Envelope phản hồi (mọi endpoint `/api/...`)

```json
{ "success": true, "message": "string", "logs": ["string"], "data": {} }
```

- Endpoint đọc đơn giản (danh sách) cũng dùng envelope, `logs` có thể rỗng.
- Lỗi `OpenStackError` hoặc lỗi bất ngờ: `success=false`, `message` mô tả rõ, `logs` giữ các bước đã chạy được tới thời điểm lỗi. Trả HTTP 200 cho lỗi nghiệp vụ OpenStack để frontend luôn đọc được envelope, HTTP 400 cho input sai, HTTP 500 cho lỗi không lường trước.

### Endpoints

| Method | Path | Mục đích |
|--------|------|----------|
| GET | `/` | Trang dashboard |
| GET | `/api/flavors`, `/api/images` | Yêu cầu 1, đổ vào dropdown |
| GET/POST | `/api/networks`, DELETE `/api/networks/<id>` | Yêu cầu 2 |
| GET/POST | `/api/subnets`, DELETE `/api/subnets/<id>` | Yêu cầu 2 |
| GET/POST | `/api/routers`, DELETE `/api/routers/<id>` | Yêu cầu 3 |
| POST/DELETE | `/api/routers/<id>/interfaces` | Yêu cầu 3 |
| GET/POST | `/api/servers`, DELETE `/api/servers/<id>` | Yêu cầu 4 |
| POST | `/api/setup` | Yêu cầu 5 |
| POST | `/api/servers/web` | Yêu cầu 6, tạo VM kèm user_data (body: `name`, `image_id`, `flavor_id`) |
| POST | `/api/servers/<id>/floating-ip` | Yêu cầu 6, gán Floating IP riêng |
| POST | `/api/loadbalancer` | Yêu cầu 7 |
| POST | `/api/scale-out`, `/api/scale-in` | Yêu cầu 8 |

### Giao diện
- Trang đơn `templates/index.html`: Bootstrap 5, các Tab theo nhóm chức năng, Vanilla JS `fetch`.
- Hai Select box Image và Flavor, nạp từ `/api/images` và `/api/flavors`, dùng cho tạo VM (Yêu cầu 4 và 6). Scale Out không dùng dropdown, dùng giá trị mặc định ở server.
- Tác vụ dài (setup, tạo VM web, LB, Scale Out, Scale In): khi bấm thì vô hiệu hóa nút, hiện Bootstrap Spinner "Đang xử lý, vui lòng đợi 1-2 phút...". Khi nhận envelope thì tắt spinner, hiện Toast/Alert theo `success` và `message`, in từng dòng `logs` vào khung "Nhật ký hệ thống (Activity Logs)".
- Backend xử lý tuần tự đồng bộ. Flask chạy với `threaded=True` để một request chậm không chặn các request tải danh sách.

## 6. Xử lý lỗi và bảo mật
- `.env` không bao giờ được commit. `.env.example` không chứa giá trị thật. Token và secret không được ghi vào `logs`.
- Mọi lỗi từ OpenStack được chuyển thành thông báo đọc được (status + message của OpenStack).
- Không có marker trích dẫn `[cite: ...]` trong code, comment hay tài liệu.

## 7. Kiểm thử
- Unit test bằng `pytest` + `responses` mock HTTP, không gọi cloud thật. Phủ: xác thực và refresh khi `401`, trích xuất endpoint từ catalog, `wait_for_lb_active` (ACTIVE, ERROR, timeout với thời gian giả), logic chọn số thứ tự Scale Out, chọn VM Scale In, tính idempotent của 1-Click, envelope của `app.py` qua Flask test client.
- Kiểm thử tích hợp thật trên NC-Cloud bằng credential nhom17, theo thứ tự yêu cầu 1 đến 8. Kết quả (ảnh chụp giao diện + Activity Logs) dùng cho báo cáo.

## 8. Việc người dùng cần cung cấp trước khi chạy thật
- Application Credential ID/Secret của nhom17 (vào `.env`).
- `DEFAULT_IMAGE_ID`, `DEFAULT_FLAVOR_ID` (lấy từ danh sách Yêu cầu 1 sau khi app chạy được).
