# TASK B - TCP FILE SHARING
**Người phụ trách:** Thành
**Module cần tạo:** `tcp_file_transfer.py`

> **PROMPT CHO AI HỖ TRỢ CODE:**
> "Tôi cần viết file `tcp_file_transfer.py` chuyên gửi/nhận file nhị phân qua TCP (Chunking để tránh tràn RAM). Dùng module `protocol.py` để lấy header gửi file."

## Task B0: Hàm `recv_exact(sock, num_bytes)`
* Đọc đủ số byte yêu cầu trên một TCP stream, dùng `while`. `raise ConnectionError` nếu socket đóng sớm.

## Task B1: Luồng Gửi file (`send_file_async`)
* **Yêu cầu:** Thread riêng không làm block màn hình chat.
* **Tham số:** `sock`, `filepath`, `print_callback`.
* **Chi tiết:**
  * Lấy file size, filename. 
  * Gọi `protocol.encode_tcp_file_header(filename, file_size)` và gửi qua socket.
  * Mở file chế độ `"rb"`. Lặp đọc từng khối tối đa 64KB gửi bằng `sendall`.
  * Tính phần trăm tiến độ % và in đè dòng báo cáo qua `print_callback`. Bắt chặt lỗi đứt cáp (Connection Error).

## Task B2: Hàm Nhận file (`receive_file`)
* **Yêu cầu:** TCP Receiver sẽ chuyển giao cho hàm này khi thấy prefix `FILE|`.
* **Tham số:** `sock`, `filename`, `file_size`, `sender_name`.
* **Chi tiết:**
  * Gọi `os.makedirs("downloads")`. Mở file chế độ `"wb"` tại đường dẫn `downloads/filename` (tự xử lý trùng tên).
  * Vòng lặp lấy bằng được lượng bytes đúng bằng `file_size` (gọi `recv_exact` hoặc vòng lặp tương tự) với chunk 64KB để ghi trực tiếp ra đĩa.

## Task B3: Dọn dẹp lỗi
* Nếu đang tải dở mà quăng lỗi `Exception`, lập tức đóng `file.close()` và `os.remove()` file đang tải dở. In báo cáo lỗi mầu đỏ.