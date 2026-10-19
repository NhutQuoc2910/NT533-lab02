# NT533-lab02

## Chạy ứng dụng (Nhóm 17 - NT533 Lab 2)

```powershell
cd openstack_flask_nhom17
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
# Điền OS_APP_CRED_ID và OS_APP_CRED_SECRET vào .env
.venv\Scripts\python app.py
```

Mở http://127.0.0.1:5000. Chạy unit test (không gọi cloud thật): `.venv\Scripts\python -m pytest` (hoặc `.venv\bin\python.exe -m pytest` tùy phiên bản Python trên Windows).

Sau khi giao diện tải được danh sách Flavor/Image, điền `DEFAULT_IMAGE_ID` và `DEFAULT_FLAVOR_ID` vào `.env` rồi khởi động lại để dùng Scale Out.