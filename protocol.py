"""
Application-layer wire protocol for LAN Chat & File Sharing.

Why we need a protocol:
- UDP sends discrete datagrams, so a simple delimiter-separated string ("HELLO|...") works cleanly.
- TCP is a raw, continuous byte stream (it does NOT preserve message boundaries).
  Therefore, every TCP frame begins with a `|`-delimited command header so the receiver
  knows what is coming next and how many exact bytes to read from the stream.

Note on delimiter safety:
- Any `|` inside a username or filename is replaced with `_` so it cannot split a header
  into extra fields and corrupt parsing on the receiver side.
"""


def encode_udp_hello(name: str, tcp_port: int, status: str = "online") -> bytes:
    """
    Encode a UDP discovery broadcast packet announcing this peer's presence.

    Wire format: "HELLO|<name>|<tcp_port>|<status>"

    Args:
        name (str): This device's display username.
        tcp_port (int): TCP port where this device accepts chat/file connections.
        status (str): Presence status ("online" or "away").

    Returns:
        bytes: UTF-8 encoded datagram ready for sock.sendto().
    """
    # Replace '|' with '_' so the receiver's split('|') always yields exactly 4 fields.
    payload = f"HELLO|{name.replace("|", "_")}|{tcp_port}|{status}"
    return payload.encode("utf-8")


def encode_tcp_ident(name: str, tcp_port: int) -> bytes:
    """
    Encode the handshake packet sent immediately after opening a persistent TCP chat socket.
    Tells the remote peer who connected so they can register this socket in active_connections.

    Wire format: "IDENT|<name>|<tcp_port>|"

    Args:
        name (str): Sender's username.
        tcp_port (int): Sender's listening TCP port.

    Returns:
        bytes: UTF-8 encoded identification header.
    """
    # Trailing '|' is required because tcp_chat.header_reader() reads until it encounters '|'.
    payload = f"IDENT|{name.replace("|", "_")}|{tcp_port}|"
    return payload.encode("utf-8")


def encode_tcp_file_ident(name: str) -> bytes:
    """
    Encode the identification packet for a dedicated temporary file-transfer TCP connection.
    Unlike IDENT, this tells the receiver who is sending the file WITHOUT overwriting
    their active persistent chat socket in shared_state.active_connections.

    Wire format: "FILE_IDENT|<name>|"

    Args:
        name (str): Sender's username.

    Returns:
        bytes: UTF-8 encoded file-connection identification header.
    """
    payload = f"FILE_IDENT|{name.replace("|", "_")}|"
    return payload.encode("utf-8")


def encode_tcp_msg(text: str) -> bytes:
    """
    Encode a length-prefixed TCP chat message packet.

    Wire format: "MSG|<byte_length>|<utf8_text_bytes>"

    Why length-prefixing matters:
    - A chat message may contain ANY character (including '|', newlines, or multi-byte UTF-8 emojis).
    - Sending len(text_bytes) (byte count, NOT Python character count len(text)) tells the
      receiver the exact number of bytes to read via recv_exact().

    Args:
        text (str): Plaintext chat message to send.

    Returns:
        bytes: Complete framed packet (header + UTF-8 message bytes).
    """
    # Convert str -> bytes FIRST because UTF-8 characters (e.g., Vietnamese/emojis) take 2-4 bytes each.
    text_bytes = text.encode("utf-8")
    header = f"MSG|{len(text_bytes)}|".encode("utf-8")
    return header + text_bytes


def encode_tcp_file_header(filename: str, size_in_bytes: int) -> bytes:
    """
    Encode the metadata header sent over TCP immediately before streaming raw binary file bytes.

    Wire format: "FILE|<filename>|<size_in_bytes>|"
    (Followed directly on the TCP stream by `size_in_bytes` raw bytes.)

    Args:
        filename (str): Base name of the file (without directory paths).
        size_in_bytes (int): Exact byte size of the file so the receiver knows when to stop reading.

    Returns:
        bytes: UTF-8 encoded file metadata header.
    """
    payload = f"FILE|{filename.replace("|", "_")}|{size_in_bytes}|"
    return payload.encode("utf-8")


def encode_tcp_bye() -> bytes:
    """
    Encode a graceful disconnect signal sent before closing a TCP connection.
    Allows the remote peer_listener() loop to exit cleanly instead of raising ConnectionError.

    Wire format: "BYE|"

    Returns:
        bytes: UTF-8 encoded disconnect command.
    """
    return "BYE|".encode("utf-8")


def parse_udp_hello(datagram_bytes: bytes):
    """
    Decode and validate an incoming UDP "HELLO" broadcast datagram.

    Args:
        datagram_bytes (bytes): Raw bytes received from sock.recvfrom().

    Returns:
        tuple[str, int, str] | None: (name, tcp_port, status) if the packet is valid,
        or None if the datagram is malformed, corrupted, or not a HELLO packet.
    """
    try:
        text = datagram_bytes.decode("utf-8").strip()
        parts = text.split("|")

        # Validate exact field count and command tag so random LAN UDP traffic is safely ignored.
        if len(parts) != 4 or parts[0] != "HELLO":
            return None

        name = parts[1]
        port = int(parts[2])
        status = parts[3]

        # Reject empty usernames or invalid TCP port numbers outside the 16-bit range (1..65535).
        if not name or port <= 0 or port > 65535:
            return None

        return (name, port, status)
    except (UnicodeDecodeError, ValueError):
        # Ignore non-UTF-8 bytes or non-numeric port fields from unrelated network traffic.
        return None
