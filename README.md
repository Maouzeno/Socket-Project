# Socket-Project

Du an LAN chat va file sharing su dung UDP de tim peer va TCP de truyen du lieu.

## TCP file sharing

File transfer module:

- `protocol.py`: dong goi va giai ma cac goi tin
- `shared_state.py`: trang thai duoc chia se giua cac module
- `tcp_file_transfer.py`: gui va nhan file bang chunk 64 KB
- `demo_file_sharing.py`: vi du gui/nhan file don gian qua terminal

Chay nguoi nhan:

```powershell
python demo_file_sharing.py receive --host 0.0.0.0 --port 5001
```

Chay nguoi gui:

```powershell
python demo_file_sharing.py send <receiver-ip> <file-path> --port 5001
```

Cac tai lieu task va kien truc duoc luu trong thu muc `References/`.