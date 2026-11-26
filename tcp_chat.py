"""
TCP Peer-to-Peer Chat & Connection Manager Module.

Handles all reliable TCP networking:
- tcp_acceptor(): Listens as a TCP server for incoming chat and file connections.
- peer_listener(): Runs one thread per connected socket, parsing framed commands
  (IDENT, FILE_IDENT, MSG, FILE, BYE) from the TCP byte stream.
- connect_to_peer(): Opens an outbound persistent TCP chat socket to a discovered peer.
- send_message(): Sends a length-prefixed UTF-8 chat message over the peer's TCP socket.
- close_all_connections(): Gracefully sends BYE and closes all active chat sockets on exit.
"""

import socket
import shared_state
import threading
import protocol
import tcp_file_transfer


def bytes_extract(sock: socket.socket, number_of_bytes: int) -> bytes:
    """
    Read an exact number of bytes from a TCP socket.

    Why this wrapper exists:
    TCP is a stream protocol, so a single sock.recv(N) may return fewer than N bytes
    if packets are fragmented across the network. recv_exact() loops until all N bytes arrive.

    Args:
        sock (socket.socket): Connected TCP socket to read from.
        number_of_bytes (int): Exact byte count to extract.

    Returns:
        bytes: Byte sequence of length `number_of_bytes`.
    """
    return tcp_file_transfer.recv_exact(sock, number_of_bytes)


def header_reader(sock: socket.socket) -> str:
    """
    Read one `|`-delimited protocol header field from a TCP byte stream.

    Reads 1 byte at a time so we NEVER accidentally consume bytes belonging to the
    next field or binary payload (such as raw message text or file data).

    Args:
        sock (socket.socket): Connected TCP socket to read from.

    Returns:
        str: Decoded UTF-8 field text (without the trailing '|').

    Raises:
        ConnectionError: If sock.recv(1) returns b"" (remote peer closed the TCP connection).
    """
    buffer = bytearray()

    # Keep pulling 1 byte at a time from the TCP receive buffer until the '|' delimiter is found.
    while True:
        byte = sock.recv(1)
        if not byte:
            # In TCP, recv() returning an empty bytes object (b"") means EOF (peer closed connection).
            raise ConnectionError("Lost connection!")
        if byte == b'|':
            return buffer.decode("utf-8")
        buffer.extend(byte)


def clear_connection(peer: str, sock: socket.socket):
    """
    Remove a closed/broken socket from shared_state.active_connections (if it is still the
    registered chat socket for `peer`) and close the underlying OS socket descriptor.

    Args:
        peer (str): Peer username associated with this socket (may be None if disconnected before IDENT).
        sock (socket.socket): The TCP socket being cleaned up.
    """
    if peer:
        with shared_state.state_lock:
            # Identity check (`is sock`) is crucial:
            # 1) Temporary file-transfer sockets are NOT in active_connections, so closing them won't drop chat.
            # 2) If a peer reconnected with a newer socket, closing the old socket won't delete the new one.
            if shared_state.active_connections.get(peer) is sock:
                del shared_state.active_connections[peer]
    try:
        sock.close()
    except OSError:
        pass


def peer_listener(sock: socket.socket, known_peer_name: str, print_callback=None):
    """
    Per-connection worker thread that continuously reads and dispatches framed TCP commands
    from a single remote peer until BYE is received or the connection drops.

    Args:
        sock (socket.socket): Connected TCP socket (either accepted by tcp_acceptor or opened by connect_to_peer).
        known_peer_name (str | None): Peer's username if we initiated the connection, or None for incoming sockets
            (will be populated once IDENT or FILE_IDENT arrives).
        print_callback (Callable[[str], None] | None): Optional thread-safe logging callback for GUI/console.
    """
    log = print_callback or print
    peer_name = known_peer_name
    try:
        while True:
            # Read the command token up to the first '|' (e.g., "IDENT", "MSG", "FILE", "FILE_IDENT", "BYE").
            cmd = header_reader(sock)

            # 1. Persistent chat handshake: "IDENT|<name>|<tcp_port>|"
            if cmd == "IDENT":
                sender_name = header_reader(sock)
                sender_port = header_reader(sock)
                peer_name = sender_name
                # Register this socket so we can reuse it to send chat replies back to sender_name.
                with shared_state.state_lock:
                    shared_state.active_connections[sender_name] = sock

            # 2. Chat message frame: "MSG|<byte_len>|<utf8_bytes>"
            elif cmd == "MSG":
                byte_len = int(header_reader(sock))
                # Read exact payload bytes so framing stays synchronized for the next command.
                raw_msg = bytes_extract(sock, byte_len)
                text = raw_msg.decode("utf-8")
                sender = peer_name or "Unknown"
                formatted_message = f"[{sender}]: {text}"

                # Store message in shared chat history and increment unread badge if viewing another chat.
                with shared_state.state_lock:
                    if sender not in shared_state.chat_histories:
                        shared_state.chat_histories[sender] = []
                    shared_state.chat_histories[sender].append(formatted_message)

                    is_active_chat = (shared_state.active_chat_user == sender)
                    if not is_active_chat:
                        shared_state.unread_counts[sender] = (shared_state.unread_counts.get(sender, 0) + 1)

            # 3. Binary file transfer frame: "FILE|<filename>|<size_in_bytes>|<raw_file_bytes>"
            elif cmd == "FILE":
                filename = header_reader(sock)
                size_in_bytes = int(header_reader(sock))
                sender = peer_name or "Unknown"
                # Hand the TCP stream over to tcp_file_transfer to read exact `size_in_bytes` bytes to disk.
                saved_path = tcp_file_transfer.receive_file(sock, filename, size_in_bytes, sender, print_callback=log)
                formatted_file_msg = f"[{sender}]: [FILE] Received '{filename}' -> {saved_path}"
                with shared_state.state_lock:
                    shared_state.chat_histories.setdefault(sender, []).append(formatted_file_msg)
                    if shared_state.active_chat_user != sender:
                        shared_state.unread_counts[sender] = shared_state.unread_counts.get(sender, 0) + 1

            # 4. Dedicated file-connection handshake: "FILE_IDENT|<name>|"
            # Sets peer_name for the upcoming FILE command WITHOUT touching shared_state.active_connections,
            # so ongoing chat stays uninterrupted on its own socket.
            elif cmd == "FILE_IDENT":
                sender_name = header_reader(sock)
                peer_name = sender_name

            # 5. Graceful disconnect: "BYE|"
            elif cmd == "BYE":
                if peer_name:
                    log(f"[INFO]: {peer_name} has disconnected.")
                break
    except (ConnectionError, OSError, ValueError) as e:
        log(f"[ERROR]: Connection with {peer_name} lost: {e}")
    finally:
        # Always close the socket and clean up active_connections on thread exit.
        clear_connection(peer_name, sock)


def tcp_acceptor(print_callback=None):
    """
    TCP server loop that binds to this device's TCP port and accepts incoming peer connections.
    Spawns a new daemon `peer_listener` thread for every accepted connection (chat or file transfer).

    Args:
        print_callback (Callable[[str], None] | None): Optional thread-safe logging callback for GUI/console.
    """
    log = print_callback or print
    # Read our listening port under state_lock, then RELEASE the lock before blocking in accept().
    with shared_state.state_lock:
        my_port = shared_state.my_info["tcp_port"]

    try:
        # AF_INET = IPv4; SOCK_STREAM = TCP (reliable, ordered, connection-oriented byte stream).
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # SO_REUSEADDR lets us rebind immediately to the same port without waiting for OS TIME_WAIT.
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Bind to '' (0.0.0.0) so peers on any local network interface (Wi-Fi/Ethernet/loopback) can connect.
        server_socket.bind(('', my_port))

        # Put socket in passive listening mode; OS queues up to 5 pending TCP 3-way handshakes.
        server_socket.listen(5)
    except OSError as e:
        log(f"[ERROR]: Failed to start TCP server on port {my_port}: {e}")
        return

    log(f"[INFO]: Listening for TCP connections on port {my_port}")

    try:
        while True:
            try:
                # Block until a remote peer completes a TCP handshake.
                # Returns a brand-new dedicated `client_socket` for that peer and their `(ip, ephemeral_port)`.
                client_socket, addr = server_socket.accept()
                log(f"[INFO]: Connection established with {addr}")

                # Spawn a dedicated listener thread so accepting new peers is never blocked by existing ones.
                # known_peer_name=None because we don't know their username until they send IDENT / FILE_IDENT.
                listener_thread = threading.Thread(target=peer_listener, args=(client_socket, None, log), daemon=True)
                listener_thread.start()
            except OSError:
                break
    finally:
        try:
            server_socket.close()
        except OSError:
            pass


def connect_to_peer(peer_name: str, print_callback=None) -> bool:
    """
    Ensure a persistent TCP chat connection exists with `peer_name`.
    If not already connected, looks up the peer's IP/TCP port from UDP discovery, opens a TCP
    connection, sends our IDENT handshake, registers the socket, and starts a peer_listener thread.

    Args:
        peer_name (str): Target peer's username.
        print_callback (Callable[[str], None] | None): Optional thread-safe logging callback.

    Returns:
        bool: True if a usable TCP connection is ready in active_connections, False on failure.
    """
    log = print_callback or print
    with shared_state.state_lock:
        # Fast path: reuse existing active chat socket if already connected.
        if peer_name in shared_state.active_connections:
            return True

        # Look up the peer's latest IP and listening TCP port discovered via UDP broadcasts.
        peer_info = shared_state.online_users.get(peer_name)
        my_name = shared_state.my_info["name"]
        my_port = shared_state.my_info["tcp_port"]

    if not peer_info:
        log(f"[ERROR]: {peer_name} is not online")
        return False

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Cap the TCP 3-way handshake + IDENT send to 5 seconds so offline/unreachable peers fail fast.
        sock.settimeout(5.0)
        sock.connect((peer_info["ip"], peer_info["port"]))

        # Immediately identify ourselves so the remote peer_listener registers this socket under my_name.
        sock.sendall(protocol.encode_tcp_ident(my_name, my_port))

        # Switch back to blocking mode (no timeout) so peer_listener can wait indefinitely for chat messages.
        sock.settimeout(None)

        # Double-check under state_lock in case the remote peer connected to us simultaneously.
        already_connected = False
        with shared_state.state_lock:
            if peer_name in shared_state.active_connections:
                already_connected = True
            else:
                shared_state.active_connections[peer_name] = sock

        if already_connected:
            # Close the redundant socket to avoid leaking file descriptors.
            sock.close()
            return True

        # Start a background listener thread to receive replies arriving on this outbound socket.
        listener_thread = threading.Thread(target=peer_listener, args=(sock, peer_name, log), daemon=True)
        listener_thread.start()
        return True
    except OSError:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        log(f"[ERROR]: Could not connect to {peer_name}.")
        return False


def send_message(peer_name: str, message: str, print_callback=None) -> bool:
    """
    Send a UTF-8 chat message to `peer_name` over TCP, establishing a connection first if needed.

    Args:
        peer_name (str): Recipient's username.
        message (str): Plaintext message to send.
        print_callback (Callable[[str], None] | None): Optional thread-safe logging callback.

    Returns:
        bool: True if transmitted and recorded in chat_histories, False on error.
    """
    log = print_callback or print

    # Ensure TCP chat connection is open before sending.
    if not connect_to_peer(peer_name, print_callback=log):
        return False

    with shared_state.state_lock:
        sock = shared_state.active_connections.get(peer_name)
    if not sock:
        return False

    try:
        # Frame as "MSG|<byte_len>|<utf8_bytes>" and use sendall() to guarantee the entire packet is sent.
        packet = protocol.encode_tcp_msg(message)
        sock.sendall(packet)

        # Record our outgoing message in shared chat history so the UI displays it.
        with shared_state.state_lock:
            shared_state.chat_histories.setdefault(peer_name, []).append(f"[Me]: {message}")
        return True
    except OSError:
        # Remove broken socket so the next message attempt establishes a fresh connection.
        clear_connection(peer_name, sock)
        log(f"[ERROR]: Failed to send message to {peer_name}.")
        return False


def close_all_connections():
    """
    Gracefully terminate all active TCP chat connections during application shutdown.
    Sends a "BYE|" frame to each connected peer before closing their socket.
    """
    # Snapshot and clear the dictionary under state_lock so no other thread uses closing sockets.
    with shared_state.state_lock:
        conns = list(shared_state.active_connections.items())
        shared_state.active_connections.clear()

    bye_packet = protocol.encode_tcp_bye()
    for _name, sock in conns:
        try:
            sock.sendall(bye_packet)
            sock.close()
        except OSError:
            pass
