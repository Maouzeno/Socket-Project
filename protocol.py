def encode_udp_hello(name: str, tcp_port: int, status: str = "online") -> bytes:
    payload = f"HELLO|{name.replace("|", "_")}|{tcp_port}|{status}"
    return payload.encode("utf-8")

def encode_tcp_ident(name: str, tcp_port: int) -> bytes:
    payload = f"IDENT|{name.replace("|", "_")}|{tcp_port}|"
    return payload.encode("utf-8")

def encode_tcp_file_ident(name: str) -> bytes:
    payload = f"FILE_IDENT|{name.replace("|", "_")}|"
    return payload.encode("utf-8")

def encode_tcp_msg(text: str) -> bytes:
    text_bytes = text.encode("utf-8")
    header = f"MSG|{len(text_bytes)}|".encode("utf-8")
    return header + text_bytes

def encode_tcp_file_header(filename: str, size_in_bytes: int) -> bytes:
    payload = f"FILE|{filename.replace("|", "_")}|{size_in_bytes}|"
    return payload.encode("utf-8")

def encode_tcp_bye() -> bytes:
    return "BYE|".encode("utf-8")

def parse_udp_hello(datagram_bytes: bytes):
    try:
        text = datagram_bytes.decode("utf-8").strip()
        parts = text.split("|")
        
        if len(parts) != 4 or parts[0] != "HELLO":
            return None
            
        name = parts[1]
        port = int(parts[2])
        status = parts[3]
        
        if not name or port <= 0 or port > 65535:
            return None
            
        return (name, port, status)
    except (UnicodeDecodeError, ValueError):
        return None