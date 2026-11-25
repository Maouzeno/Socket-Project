import argparse
import socket

import tcp_file_transfer


DEFAULT_PORT = 5001
MAX_HEADER_SIZE = 4096


def _read_file_header(sock):
    header = bytearray()
    while header.count(b"|") < 3:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("Connection closed before the file header arrived")
        header.extend(byte)
        if len(header) > MAX_HEADER_SIZE:
            raise ValueError("File header is too large")

    try:
        message_type, filename, file_size_text, _ = header.decode("utf-8").split("|")
        file_size = int(file_size_text)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Invalid file-transfer header") from error

    if message_type != "FILE" or file_size < 0:
        raise ValueError("Invalid file-transfer header")
    return filename, file_size


def receive_demo(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        print(f"Waiting for a file on {host}:{port}...")
        connection, address = server.accept()

        with connection:
            print(f"Connected to {address[0]}:{address[1]}")
            filename, file_size = _read_file_header(connection)
            transfer = tcp_file_transfer.TCPFileTransfer()
            transfer.receive_file(
                connection, filename, file_size, address[0]
            )


def send_demo(host, port, filepath):
    with socket.create_connection((host, port)) as connection:
        transfer = tcp_file_transfer.TCPFileTransfer()
        transfer_thread = transfer.send_file_async(
            connection,
            filepath,
            lambda message: print(message, end="", flush=True),
        )
        transfer_thread.join()
        print()


def main():
    parser = argparse.ArgumentParser(description="Simple TCP file-sharing demo")
    subparsers = parser.add_subparsers(dest="command")

    receive_parser = subparsers.add_parser("receive", help="wait for one incoming file")
    receive_parser.add_argument("--host", default="0.0.0.0")
    receive_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    send_parser = subparsers.add_parser("send", help="send a file to a receiver")
    send_parser.add_argument("host", help="receiver IP address or hostname")
    send_parser.add_argument("filepath", help="path to the file to send")
    send_parser.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = parser.parse_args()
    if args.command == "receive":
        receive_demo(args.host, args.port)
    elif args.command == "send":
        send_demo(args.host, args.port, args.filepath)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
