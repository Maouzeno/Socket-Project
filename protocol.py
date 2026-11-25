def encode_udp_hello(name, port, status):
    return f"HELLO|{name}|{port}|{status}".encode("utf-8")


def encode_tcp_ident(name, port):
    return f"IDENT|{name}|{port}|".encode("utf-8")


def encode_tcp_msg(text):
    text_bytes = text.encode("utf-8")
    return f"MSG|{len(text_bytes)}|".encode("utf-8") + text_bytes


def encode_tcp_file_header(filename, file_size):
    return f"FILE|{filename}|{file_size}|".encode("utf-8")


def encode_tcp_bye():
    return b"BYE|"


def parse_udp_hello(datagram_bytes):
    try:
        parts = datagram_bytes.decode("utf-8").split("|")
        if len(parts) != 4 or parts[0] != "HELLO":
            return None

        name, port_text, status = parts[1:]
        port = int(port_text)
    except (UnicodeDecodeError, ValueError):
        return None

    return name, port, status
