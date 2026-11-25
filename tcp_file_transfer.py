import os
import threading
import protocol


CHUNK_SIZE = 64 * 1024
DOWNLOAD_DIR = "downloads"


class TCPFileTransfer:

    # 1. READ EXACT BYTES

    @staticmethod
    def recv_exact(sock, num_bytes):
        if num_bytes < 0:
            raise ValueError("num_bytes must not be negative")

        received = bytearray()
        while len(received) < num_bytes:
            chunk = sock.recv(num_bytes - len(received))
            if not chunk:
                raise ConnectionError(
                    "Connection closed before all requested bytes arrived"
                )
            received.extend(chunk)
        return bytes(received)

    # 2. SEND FILE IN BACKGROUND

    def send_file_async(self, sock, filepath, print_callback):
        def send_file():
            try:
                filename = os.path.basename(os.fsdecode(os.fspath(filepath)))
                if not filename or "|" in filename:
                    raise ValueError(
                        "The filename is invalid for the transfer protocol"
                    )

                with open(filepath, "rb") as source_file:
                    file_size = os.fstat(source_file.fileno()).st_size
                    sock.sendall(
                        protocol.encode_tcp_file_header(filename, file_size)
                    )

                    bytes_sent = 0
                    while bytes_sent < file_size:
                        chunk = source_file.read(
                            min(CHUNK_SIZE, file_size - bytes_sent)
                        )
                        if not chunk:
                            raise OSError(
                                "File ended before its reported size was sent"
                            )
                        sock.sendall(chunk)
                        bytes_sent += len(chunk)
                        progress = int(bytes_sent * 100 / file_size)
                        print_callback(f"\rSending {filename}: {progress}%")

                    if file_size == 0:
                        print_callback(f"\rSending {filename}: 100%")
            except (OSError, ValueError) as error:
                print_callback(f"\033[91mFile transfer failed: {error}\033[0m")

        transfer_thread = threading.Thread(target=send_file, name="file-transfer")
        transfer_thread.start()
        return transfer_thread

    # 3. RECEIVE FILE

    def receive_file(self, sock, filename, file_size, sender_name):
        if not isinstance(file_size, int) or file_size < 0:
            raise ValueError("file_size must be a non-negative integer")

        safe_filename = os.path.basename(filename.replace("\\", "/"))
        if safe_filename in ("", ".", "..") or "|" in safe_filename:
            raise ValueError("The received filename is invalid")

        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        name, extension = os.path.splitext(safe_filename)
        index = 0

        while True:
            candidate_name = (
                safe_filename if index == 0 else f"{name} ({index}){extension}"
            )
            destination = os.path.join(DOWNLOAD_DIR, candidate_name)
            try:
                output_file = open(destination, "xb")
                break
            except FileExistsError:
                index += 1

        try:
            with output_file:
                remaining = file_size
                while remaining:
                    chunk = self.recv_exact(
                        sock, min(CHUNK_SIZE, remaining)
                    )
                    output_file.write(chunk)
                    remaining -= len(chunk)
        except Exception as error:
            try:
                os.remove(destination)
            except FileNotFoundError:
                pass
            print(
                f"\033[91mFailed to receive file from {sender_name}: "
                f"{error}\033[0m"
            )
            raise

        print(f"Received {safe_filename} from {sender_name}: {destination}")
        return destination
