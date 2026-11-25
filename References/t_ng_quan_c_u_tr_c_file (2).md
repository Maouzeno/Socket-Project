# ĐỒ ÁN MẠNG MÁY TÍNH: LAN CHAT & FILE SHARING (P2P)
**TÀI LIỆU TỔNG QUAN VÀ KIẾN TRÚC MÔ-ĐUN (CẬP NHẬT KỸ LƯỠNG)**

## 1. Cấu trúc thư mục (File Hierarchy)
Để dự án chuyên nghiệp và dễ quản lý, mã nguồn được chia thành **7 file Python**:

```text
lan_chat_project/
│
├── shared_state.py        <-- (Core) Chứa dữ liệu dùng chung (biến toàn cục, khóa Lock).
├── protocol.py            <-- (Core) Chứa các hàm encode/decode gói tin chuẩn format.
│
├── udp_discovery.py       <-- (Phúc - Người A): Module phát hiện peer và trạng thái.
├── tcp_file_transfer.py   <-- (Thành - Người B): Module gửi/nhận file nhị phân.
│
├── tcp_chat.py            <-- (Huy - Người C): Module xử lý Socket TCP Chat.
├── console_ui.py          <-- (Huy - Người C): Module chống đè chữ trên Terminal.
├── lanchat.py             <-- (Huy - Người C): Main app, ghép nối các module.
│
├── downloads/             <-- Thư mục lưu file nhận.
└── README.md
```

## 2. Đặc tả Giao thức (Protocol Framing)
* **Gói UDP Hello:** `HELLO|<name>|<tcp_port>|<status>`
* **Gói TCP Ident (Chào hỏi ngay khi kết nối):** `IDENT|<name>|<tcp_port>|`
* **Gói TCP Tin nhắn:** `MSG|<length_in_bytes>|<utf-8_text>`
* **Gói TCP Gửi File:** `FILE|<filename>|<size_in_bytes>|` (Sau chuỗi này là luồng byte nhị phân của file).
* **Gói TCP Đóng kết nối:** `BYE|`