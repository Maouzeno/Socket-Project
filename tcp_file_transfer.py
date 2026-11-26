"""
TCP Binary File Transfer Module.

Architecture:
- File transfers stream binary data in 64 KB chunks to avoid loading huge files into RAM.
- Sending uses a DEDICATED temporary TCP connection per file (FILE_IDENT -> FILE -> bytes -> BYE -> close)
  so large file transfers never block, delay, or corrupt ongoing TCP chat messages.
"""

import os
import socket
import shared_state
import threading
from typing import Callable, Optional

import protocol


# Read/write buffer size per chunk (64 KB): balances low memory usage with high LAN throughput.
CHUNK_SIZE = 64 * 1024


def recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """
    Loop on sock.recv() until exactly `num_bytes` bytes have been read from the TCP stream.

    Why this is required in TCP networking:
    TCP delivers a stream of bytes, not fixed-size packets. Calling sock.recv(65536) might
    return only 1460 bytes (one Ethernet MTU segment). Looping until len(chunks) == num_bytes
    guarantees complete, uncorrupted framing.

    Args:
        sock (socket.socket): Connected TCP socket.
        num_bytes (int): Exact number of bytes to read.

    Returns:
        bytes: Byte object of exact length `num_bytes`.

    Raises:
        ConnectionError: If the remote peer closes the socket before all bytes arrive.
    """
    chunks = bytearray()
    while len(chunks) < num_bytes:
        # Only request the remaining byte count so we never over-read into the next TCP frame (e.g. BYE|).
        chunk = sock.recv(num_bytes - len(chunks))
        if not chunk:
            raise ConnectionError("Socket closed prematurely while reading file data.")
        chunks.extend(chunk)
    return bytes(chunks)


def _unique_download_path(filename: str) -> str:
    """
    Compute a safe, collision-free file path inside the local `downloads/` directory.

    Args:
        filename (str): Original filename sent in the FILE header.

    Returns:
        str: Path under `downloads/` that does not overwrite any existing file
             (appends _1, _2, ... if a file with the same name already exists).
    """
    # os.path.basename() strips directory components (e.g., "../../evil.exe" -> "evil.exe")
    # to prevent path-traversal attacks from remote peers.
    safe_name = os.path.basename(filename)
    if not safe_name:
        safe_name = "received_file"

    os.makedirs("downloads", exist_ok=True)
    path = os.path.join("downloads", safe_name)
    if not os.path.exists(path):
        return path

    base, extension = os.path.splitext(safe_name)
    counter = 1
    while True:
        candidate = os.path.join("downloads", f"{base}_{counter}{extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def receive_file(sock: socket.socket, filename: str, file_size: int, sender_name: str, print_callback: Optional[Callable[[str], None]] = None) -> str:
    """
    Read exactly `file_size` raw binary bytes from `sock` in chunks and save them to `downloads/`.
    Called by tcp_chat.peer_listener() immediately after parsing a "FILE|<filename>|<size>|" header.

    Args:
        sock (socket.socket): Connected TCP socket positioned at byte 0 of the file payload.
        filename (str): Name of the incoming file.
        file_size (int): Expected byte length of the file from the FILE header.
        sender_name (str): Username of the peer sending the file (for progress/log display).
        print_callback (Callable[[str], None] | None): Optional thread-safe callback for progress/status updates.

    Returns:
        str: Final local path where the received file was saved.

    Raises:
        ValueError: If file_size is negative.
        ConnectionError / OSError: Re-raised after cleaning up any partial file on disk.
    """
    if file_size < 0:
        raise ValueError("File size cannot be negative.")

    print_callback = print_callback or print
    output_path = _unique_download_path(filename)
    output_file = None

    try:
        # Open in binary write mode ("wb") so bytes are written verbatim without newline conversion.
        output_file = open(output_path, "wb")
        received = 0

        # Stream chunks directly to disk until the exact declared file_size has been received.
        while received < file_size:
            # Clamp the final read so we stop at the exact end of the file and don't consume trailing "BYE|".
            chunk_size = min(CHUNK_SIZE, file_size - received)
            chunk = recv_exact(sock, chunk_size)
            output_file.write(chunk)
            received += len(chunk)

            percent = int((received * 100) / file_size) if file_size else 100
            print_callback(
                f"\r[FILE] Receiving {filename} from {sender_name}: "
                f"{percent}% ({received}/{file_size} bytes)"
            )

        output_file.close()
        output_file = None
        print_callback(
            f"\033[32m[OK]\033[0m Received {filename} from {sender_name} "
            f"and saved to {output_path}"
        )
        return output_path
    except Exception as error:
        # Clean up half-downloaded corrupt files if the TCP connection drops mid-transfer.
        if output_file is not None:
            output_file.close()
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            pass
        print_callback(
            f"\033[31m[ERROR]\033[0m Failed to receive {filename} "
            f"from {sender_name}: {error}"
        )
        raise


def _send_file(peer_name: str, filepath: str, print_callback: Callable[[str], None]) -> None:
    """
    Worker function (run inside a background thread by send_file_async) that opens a dedicated
    TCP connection to `peer_name`, streams the file in 64 KB chunks, and closes the socket.

    TCP sequence sent on this temporary connection:
        1. FILE_IDENT|<my_name>|
        2. FILE|<filename>|<file_size>|
        3. <raw binary bytes in 64 KB chunks>
        4. BYE|

    Args:
        peer_name (str): Target peer's username (looked up in shared_state.online_users).
        filepath (str): Local path of the file to send.
        print_callback (Callable[[str], None]): Thread-safe callback for progress and completion logs.
    """
    sock = None
    try:
        # 1. Look up the peer's IP/TCP port and our own username safely under state_lock.
        with shared_state.state_lock:
            peer_info = shared_state.online_users.get(peer_name)
            my_name = shared_state.my_info["name"]

        if not peer_info:
            print_callback(f"[ERROR] User '{peer_name}' is not online.")
            return

        file_size = os.path.getsize(filepath)
        # Replace '|' in filename so the receiver's header_reader() never splits the filename into two fields.
        filename = os.path.basename(filepath).replace("|", "_")

        # 2. Open a dedicated TCP socket for this file transfer so chat messages on active_connections
        #    cannot interleave with or be blocked by file bytes.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)  # Prevent hanging forever if the peer just crashed or is unreachable.
        sock.connect((peer_info["ip"], peer_info["port"]))
        sock.settimeout(None)  # Disable timeout once connected so large/slow transfers don't abort.

        # 3. Identify sender via FILE_IDENT (keeps receiver's chat socket intact), then send FILE header.
        sock.sendall(protocol.encode_tcp_file_ident(my_name))
        sock.sendall(protocol.encode_tcp_file_header(filename, file_size))

        # 4. Read file from disk in binary mode ("rb") and push chunks over TCP via sendall().
        sent = 0
        with open(filepath, "rb") as file:
            while True:
                chunk = file.read(CHUNK_SIZE)
                if not chunk:
                    break
                # sendall() loops internally until the OS accepts every byte of `chunk` into the TCP buffer.
                sock.sendall(chunk)
                sent += len(chunk)
                percent = int((sent * 100) / file_size) if file_size else 100
                print_callback(
                    f"\r[FILE] Sending {filename}: "
                    f"{percent}% ({sent}/{file_size} bytes)"
                )

        # 5. Send BYE| so the remote peer_listener exits cleanly without logging a connection-lost error.
        sock.sendall(protocol.encode_tcp_bye())
        with shared_state.state_lock:
            shared_state.chat_histories.setdefault(peer_name, []).append(
                f"[Me]: [FILE] Sent '{filename}' ({file_size} bytes)"
            )
        print_callback(f"[OK] Finished sending {filename}")

    except (ConnectionError, OSError, socket.error) as error:
        print_callback(f"[ERROR] Failed to send {filepath}: {error}")
    finally:
        # Always close the dedicated temporary file socket (never touches active chat sockets).
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def send_file_async(peer_name: str, filepath: str, print_callback: Optional[Callable[[str], None]] = None) -> threading.Thread:
    """
    Launch a non-blocking background daemon thread to transfer `filepath` to `peer_name`.
    Running file I/O and network sends off the main thread keeps the Tkinter UI responsive.

    Args:
        peer_name (str): Recipient's username.
        filepath (str): Local path to the file to send.
        print_callback (Callable[[str], None] | None): Optional thread-safe progress/status callback.

    Returns:
        threading.Thread: The started background worker thread.
    """
    callback = print_callback or print
    worker = threading.Thread(
        target=_send_file,
        args=(peer_name, filepath, callback),
        name=f"send-file-{os.path.basename(filepath)}",
        daemon=True,
    )
    worker.start()
    return worker
