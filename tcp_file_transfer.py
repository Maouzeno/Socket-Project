import os
import socket
import shared_state
import threading
from typing import Callable, Optional

import protocol


CHUNK_SIZE = 64 * 1024


def recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < num_bytes:
        chunk = sock.recv(num_bytes - len(chunks))
        if not chunk:
            raise ConnectionError("Socket closed prematurely while reading file data.")
        chunks.extend(chunk)
    return bytes(chunks)


def _unique_download_path(filename: str) -> str:
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
    if file_size < 0:
        raise ValueError("File size cannot be negative.")

    print_callback = print_callback or print
    output_path = _unique_download_path(filename)
    output_file = None

    try:
        output_file = open(output_path, "wb")
        received = 0

        while received < file_size:
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


# def _send_file(sock: socket.socket, filepath: str, print_callback: Callable[[str], None]) -> None:
#     try:
#         file_size = os.path.getsize(filepath)
#         filename = os.path.basename(filepath)
#         sock.sendall(protocol.encode_tcp_file_header(filename, file_size))

#         sent = 0
#         with open(filepath, "rb") as file:
#             while True:
#                 chunk = file.read(CHUNK_SIZE)
#                 if not chunk:
#                     break
#                 sock.sendall(chunk)
#                 sent += len(chunk)
#                 percent = int((sent * 100) / file_size) if file_size else 100
#                 print_callback(
#                     f"\r[FILE] Sending {filename}: "
#                     f"{percent}% ({sent}/{file_size} bytes)"
#                 )

#         print_callback(
#             f"\033[32m[OK]\033[0m Finished sending {filename}"
#         )
#     except (ConnectionError, OSError, socket.error) as error:
#         print_callback(
#             f"\033[31m[ERROR]\033[0m Failed to send {filepath}: {error}"
#         )


# def send_file_async(
#     sock: socket.socket,
#     filepath: str,
#     print_callback: Callable[[str], None],
# ) -> threading.Thread:
#     callback = print_callback or print
#     worker = threading.Thread(
#         target=_send_file,
#         args=(sock, filepath, callback),
#         name=f"send-file-{os.path.basename(filepath)}",
#         daemon=True,
#     )
#     worker.start()
#     return worker


def _send_file(peer_name: str, filepath: str, print_callback: Callable[[str], None]) -> None:
    sock = None
    try:
        # 1. Look up peer IP/port and our own name safely
        with shared_state.state_lock:
            peer_info = shared_state.online_users.get(peer_name)
            my_name = shared_state.my_info["name"]

        if not peer_info:
            print_callback(f"[ERROR] User '{peer_name}' is not online.")
            return

        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath).replace("|", "_")

        # 2. Open a dedicated TCP socket for this file transfer
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)  # Avoid hanging forever if peer just went offline
        sock.connect((peer_info["ip"], peer_info["port"]))
        sock.settimeout(None)  # Remove timeout for large file transfer

        # 3. Identify sender (without overwriting chat socket), then send header
        sock.sendall(protocol.encode_tcp_file_ident(my_name))
        sock.sendall(protocol.encode_tcp_file_header(filename, file_size))

        # 4. Stream file chunks
        sent = 0
        with open(filepath, "rb") as file:
            while True:
                chunk = file.read(CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendall(chunk)
                sent += len(chunk)
                percent = int((sent * 100) / file_size) if file_size else 100
                print_callback(
                    f"\r[FILE] Sending {filename}: "
                    f"{percent}% ({sent}/{file_size} bytes)"
                )

        # 5. Tell receiver's peer_listener to exit cleanly
        sock.sendall(protocol.encode_tcp_bye())
        print_callback(f"[OK] Finished sending {filename}")

    except (ConnectionError, OSError, socket.error) as error:
        print_callback(f"[ERROR] Failed to send {filepath}: {error}")
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def send_file_async(peer_name: str, filepath: str, print_callback: Optional[Callable[[str], None]] = None) -> threading.Thread:
    callback = print_callback or print
    worker = threading.Thread(
        target=_send_file,
        args=(peer_name, filepath, callback),
        name=f"send-file-{os.path.basename(filepath)}",
        daemon=True,
    )
    worker.start()
    return worker