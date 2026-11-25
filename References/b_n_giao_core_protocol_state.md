# TASK CORE - SHARED STATE & PROTOCOL (NỀN TẢNG DỰ ÁN)
**Người phụ trách:** Huy (hoặc cả nhóm cùng thống nhất tạo ra trước tiên)
**Module cần tạo:** `shared_state.py` và `protocol.py`

> **PROMPT CHO AI HỖ TRỢ CODE:**
> "Tôi đang làm đồ án LAN Chat. Hãy giúp tôi tạo 2 file nền tảng: `shared_state.py` (chứa các cấu trúc dữ liệu dùng chung) và `protocol.py` (chuyên đóng gói/giải mã các gói tin mạng). Code phải tuân thủ chuẩn `snake_case`."

## 1. Chi tiết file `shared_state.py`
File này dùng để tránh việc import vòng tròn (circular import). Chỉ chứa khai báo biến.
* Khai báo: `import threading`
* Khai báo `state_lock = threading.Lock()`
* Dữ liệu cá nhân: `my_info = {"name": "", "tcp_port": 0, "status": "online"}`
* Dữ liệu mạng lưới (Dictionaries):
  * `online_users = {}` (Lưu danh sách peer từ UDP)
  * `active_connections = {}` (Mapping tên peer sang TCP Socket)
  * `unread_counts = {}` (Đếm tin nhắn chưa đọc)
  * `chat_histories = {}` (Lưu lịch sử tin nhắn)
* Trạng thái UI: `active_chat_user = None` (Tên người đang được chọn để chat)

## 2. Chi tiết file `protocol.py`
Tất cả các file khác sẽ phải gọi các hàm ở đây để chuyển String/Data thành chuỗi Bytes chuẩn bị gửi đi. Cần các hàm sau:

### Các hàm Encode (Đóng gói thành bytes)
* `encode_udp_hello(name, port, status)`: Trả về `f"HELLO|{name}|{port}|{status}".encode('utf-8')`.
* `encode_tcp_ident(name, port)`: Trả về `f"IDENT|{name}|{port}|".encode('utf-8')`.
* `encode_tcp_msg(text)`: Encode chuỗi text ra utf-8 để lấy kích thước chính xác theo byte. Trả về `f"MSG|{len(text_bytes)}|".encode('utf-8') + text_bytes`.
* `encode_tcp_file_header(filename, file_size)`: Trả về `f"FILE|{filename}|{file_size}|".encode('utf-8')`.
* `encode_tcp_bye()`: Trả về `b"BYE|"`.

### Các hàm Decode/Parse (Giải mã cơ bản)
* `parse_udp_hello(datagram_bytes)`: Decode utf-8, tách bằng `split('|')`. Nếu đúng format trả về `(name, port, status)`. Nếu sai trả về `None`.
* Hàm đọc header TCP sẽ do phần `tcp_chat.py` tự xử lý bằng cách đọc từng byte cho tới dấu `|` (vì TCP là stream, không thể split toàn bộ ngay lập tức).