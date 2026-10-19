# PROJECT SPECIFICATION: OPENSTACK REST API MANAGEMENT WEB APP (PYTHON FLASK)

## 1. Tổng quan Dự án

- **Môn học:** NT533 - Hệ tính toán phân bố (Lab 2: OpenStack API)[cite: 1].
- **Hạ tầng:** NC-Cloud (`https://cloud.uitiot.vn`)[cite: 1].
- **Nhóm thực hiện:** Nhóm 17 (`nhom17`)[cite: 8, 9].
- **Mục tiêu:** Xây dựng Web Application bằng **Python Flask** để quản lý các thành phần trên OpenStack thông qua REST API bao gồm: Network, Subnet, Router, Security Group, Compute (Instance), Keypair và Load Balancer[cite: 1, 2, 3].

## 2. Kiến trúc Hệ thống & Ràng buộc Kỹ thuật (CRITICAL RULES)

1. **Tech Stack:**
   - **Backend:** Python 3 + **Flask** (cung cấp RESTful endpoints nội bộ cho Frontend và gọi trực tiếp OpenStack REST API)[cite: 2].
   - **HTTP Client:** Thư viện `requests` thuần của Python.
   - **Frontend:** HTML5 + **Bootstrap 5** + Vanilla JavaScript (`fetch` API) nằm trong `templates/index.html` để giao tiếp bất đồng bộ (AJAX) với Flask Backend mà không cần reload trang.
2. **CẤM DÙNG SDK OPENSTACK:** Tuyệt đối KHÔNG sử dụng `openstacksdk`, `python-novaclient`, `python-neutronclient`, `python-octaviaclient` hay bất kỳ thư viện bọc sẵn nào của OpenStack. Mọi tương tác với OpenStack phải thực hiện qua HTTP Request thuần (`GET`, `POST`, `PUT`, `DELETE`) với Header `X-Auth-Token` và JSON payload[cite: 2, 6].
3. **Cơ chế Xác thực (Keystone API v3):**
   - Sử dụng **Application Credentials** (`methods: ["application_credential"]`) với `id` và `secret`.
   - **KHÔNG** truyền khối `scope` vào JSON body khi dùng Application Credentials.
   - Trích xuất Token từ HTTP Response Header `X-Subject-Token`.
   - Tự động trích xuất các URL `public` endpoint của từng dịch vụ (`compute`, `network`, `image`, `load-balancer`) từ mảng `token.catalog` trong Response Body của Keystone (hoặc dùng fallback URL mặc định của trường).
   - Tự động kiểm tra và làm mới (refresh) token nếu gặp lỗi HTTP `401 Unauthorized`.

## 3. Endpoints Thực tế trên Hạ tầng NC-Cloud UIT

- **Identity (Keystone):** `https://cloud-identity.uitiot.vn/v3/auth/tokens` (hoặc URL cấu hình trong `config.py`)[cite: 1, 2].
- **Image (Glance):** `https://cloud-image.uitiot.vn/v2/images`[cite: 2, 7].
- **Network (Neutron):** `https://cloud-network.uitiot.vn/v2.0` (`/networks`, `/subnets`, `/routers`, `/ports`, `/floatingips`, `/security-groups`, `/security-group-rules`)[cite: 2, 3, 8, 9].
- **Compute (Nova):** `https://cloud-compute.uitiot.vn/v2.1` (`/flavors/detail`, `/servers`)[cite: 2].
- **Load Balancer (Octavia):** Trích xuất từ Service Catalog (`type: "load-balancer"`, các đường dẫn `/v2.0/lbaas/loadbalancers`, `/v2.0/lbaas/listeners`, `/v2.0/lbaas/pools`)[cite: 3].

## 4. Quy ước Thông số Mô hình mạng (Nhóm 17)

- **External Network:** Tìm động qua `GET /v2.0/networks?router:external=true`[cite: 2, 3].
- **Internal Network:** `"name": "nhom17_net"`, `"admin_state_up": true`[cite: 2, 8].
- **Subnet (`nhom17_subnet`):**
  - `"ip_version": 4`, `"cidr": "192.168.17.0/24"`, `"gateway_ip": "192.168.17.1"`[cite: 9].
  - `"allocation_pools": [{"start": "192.168.17.101", "end": "192.168.17.200"}]` (Bắt buộc cấp phát từ `.101` theo Hình 1 đề bài)[cite: 2, 9].
  - `"dns_nameservers": ["8.8.8.8", "1.1.1.1"]`, `"enable_dhcp": true`[cite: 9].
- **Router (`nhom17_router`):**
  - Gateway ngoài: nối với `External Network` (`external_gateway_info`)[cite: 3].
  - Interface trong: nối với `nhom17_subnet` qua `PUT /v2.0/routers/{router_id}/add_router_interface`[cite: 3].
- **Instances (Máy ảo):** Đặt tên theo thứ tự `nhom17_vm_1`, `nhom17_vm_2`, `nhom17_vm_3`... nhận IP tương ứng `.101`, `.102`, `.103`[cite: 2].

## 5. Đặc tả 8 Yêu cầu Chức năng (Functional Requirements)

1. **Yêu cầu 1 (Flavor & Image):** Liệt kê danh sách Flavors (tên, vCPUs, RAM, Disk) và Images (tên, trạng thái, dung lượng) đang hỗ trợ[cite: 2].
2. **Yêu cầu 2 (Network & Subnet):** Liệt kê, tạo mới và xóa Network cùng Subnet[cite: 2].
3. **Yêu cầu 3 (Router):** Liệt kê, tạo mới (kèm External Gateway), gắn/gỡ interface subnet và xóa Router[cite: 2, 3].
4. **Yêu cầu 4 (Instance):** Liệt kê danh sách máy ảo (kèm trạng thái, IP nội bộ, Floating IP), tạo mới và xóa máy ảo[cite: 2, 3].
5. **Yêu cầu 5 (Thiết lập mạng tự động 1-Click):**
   - Tự động kiểm tra và khởi tạo tuần tự: `nhom17_net` -> `nhom17_subnet` (pool từ `.101`) -> `nhom17_router` (nối External Network + gắn interface `nhom17_subnet`)[cite: 2, 3, 8, 9].
   - Tự động thêm rule vào Security Group `default` cho phép Ingress: TCP port `80` (HTTP), TCP port `22` (SSH), và `ICMP`[cite: 3].
6. **Yêu cầu 6 (Tạo VM kèm Cloud-Init Web Server & Floating IP):**
   - Tạo chuỗi Base64 cho trường `user_data` từ bash script tự động cài đặt `nginx` và ghi file `/var/www/html/index.html`[cite: 3].
   - Nội dung trang web hiển thị rõ: **"Nhóm 17 - NT533"** và **Địa chỉ IP nội bộ của máy ảo** (`hostname -I`)[cite: 1, 3, 8].
   - Cấp phát Floating IP từ External Network và gán vào `port_id` của máy ảo[cite: 3].
7. **Yêu cầu 7 (Octavia Load Balancer):**
   - Khởi tạo Load Balancer trên `nhom17_subnet` -> Tạo Listener (`HTTP`, port `80`) -> Tạo Pool (`ROUND_ROBIN`, `HTTP`) -> Thêm các VM hiện có vào Pool Members (port `80`) -> Gán Floating IP cho `vip_port_id` của Load Balancer[cite: 3].
   - **Bắt buộc:** Có hàm polling `wait_for_lb_active(lb_id)` đợi `provisioning_status == "ACTIVE"` giữa mỗi bước tạo LB, Listener, Pool và Member.
8. **Yêu cầu 8 (Scale Out / Scale In tự động):**
   - **Scale Out (+1 VM):** Tự động xác định số thứ tự tiếp theo (`nhom17_vm_N`), gọi API tạo máy ảo với `user_data` cài sẵn Nginx, polling chờ máy ảo đạt `status == "ACTIVE"` và lấy được IP nội bộ (`192.168.17.10x`), sau đó tự động gọi API đưa IP máy ảo mới vào Load Balancer Pool[cite: 2, 3].
   - **Scale In (-1 VM):** Chọn máy ảo (hoặc lấy máy ảo cuối cùng), gọi API xóa Member tương ứng khỏi Load Balancer Pool (đợi LB `ACTIVE`), sau đó gọi API xóa Instance (`DELETE /v2.1/servers/{server_id}`)[cite: 3].

## 6. Cấu trúc Thư mục Dự án

```text
openstack_flask_nhom17/
├── config.py               # Lưu cấu hình URL, Application Credential ID/Secret, thông số mạng nhom17
├── openstack_api.py        # Class OpenStackClient chứa toàn bộ các hàm gọi REST API thuần bằng requests
├── app.py                  # Flask Server định nghĩa các route giao diện (/) và REST endpoints (/api/...)
├── requirements.txt        # flask, requests, urllib3
└── templates/
    └── index.html          # Giao diện Dashboard Bootstrap 5 chia theo các Tab chức năng + JS Fetch
```
