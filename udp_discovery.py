
import socket
import threading
import time

import shared_state
import protocol


# CONFIGURATION
from config import (
    UDP_PORT,
    TCP_PORT,
    BROADCAST_INTERVAL,
    BROADCAST_ADDRESS,
    PEER_TIMEOUT,
    BUFFER_SIZE
)


# A0. CREATE UDP SOCKET

def create_udp_socket(port=UDP_PORT):
    """
    Create and configure UDP socket.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1.0)
    sock.bind(("", port))
    return sock


# A1. UDP BROADCASTER


def udp_broadcaster_loop(sock, stop_event=None):
    """
    Broadcast device presence every 3 seconds.
    """

    while stop_event is None or not stop_event.is_set():

        try:
            # Read current device information
            with shared_state.state_lock:
                name = shared_state.my_info["name"]
                tcp_port = shared_state.my_info["tcp_port"]
                status = shared_state.my_info["status"]

            # Encode UDP packet
            data = protocol.encode_udp_hello(
                name,
                tcp_port,
                status
            )

            # Send broadcast
            sock.sendto(
                data,
                (BROADCAST_ADDRESS, UDP_PORT)
            )

        except OSError as error:
            print(f"[Broadcast Error] {error}")

        # Wait before next broadcast
        if stop_event is not None:
            if stop_event.wait(BROADCAST_INTERVAL):
                break
        else:
            time.sleep(BROADCAST_INTERVAL)


# A2 + A3. UDP LISTENER


def udp_listener_loop(sock, print_callback=print, stop_event=None):
    """
    Listen for UDP hello packets.
    Update online users and remove inactive peers.
    """

    while stop_event is None or not stop_event.is_set():

        try:
            # Receive UDP packet
            data, address = sock.recvfrom(BUFFER_SIZE)

            # Decode packet
            result = protocol.parse_udp_hello(data)

            if result is not None:

                name, tcp_port, status = result

                # Validate status
                if status not in ("ONLINE", "AWAY"):
                    result = None

                else:
                    # Ignore own broadcast
                    with shared_state.state_lock:
                        my_name = shared_state.my_info["name"]
                        my_port = shared_state.my_info["tcp_port"]

                    if name == my_name and tcp_port == my_port:
                        result = None

                if result is not None:

                    current_time = time.time()

                    with shared_state.state_lock:

                        is_new = name not in shared_state.online_users

                        old_status = None

                        if not is_new:
                            old_status = (
                                shared_state.online_users[name]["status"]
                            )

                        # Update peer information
                        shared_state.online_users[name] = {
                            "ip": address[0],
                            "port": tcp_port,
                            "status": status,
                            "last_seen": current_time
                        }

                    # Notify UI outside the lock
                    if is_new:
                        print_callback(
                            f"[+] {name} ({address[0]}) is now online"
                        )

                    elif old_status != status:
                        print_callback(
                            f"[*] {name} changed status to {status}"
                        )

        except socket.timeout:
            # No packet received during timeout
            pass

        except OSError as error:
            if stop_event is not None and stop_event.is_set():
                break

            print_callback(f"[Listener Error] {error}")

            if sock.fileno() == -1:
                break

        # A3. REMOVE INACTIVE PEERS

        current_time = time.time()
        offline_users = []

        with shared_state.state_lock:

            # Iterate over a copy to avoid dictionary modification errors
            for name, peer in list(
                shared_state.online_users.items()
            ):

                elapsed = current_time - peer["last_seen"]

                if elapsed > PEER_TIMEOUT:

                    del shared_state.online_users[name]

                    offline_users.append(name)

        # Notify UI outside the lock
        for name in offline_users:
            print_callback(
                f"[-] {name} is now offline"
            )


# A4. GET FORMATTED ONLINE LIST


def get_formatted_online_list():
    """
    Return formatted online user list as a string.
    """

    with shared_state.state_lock:

        users = {
            name: peer.copy()
            for name, peer in shared_state.online_users.items()
        }

    if not users:
        return "No other peers online."

    lines = []

    lines.append("========== ONLINE USERS ==========")

    for name, peer in users.items():

        lines.append(
            f"Name: {name} | "
            f"IP: {peer['ip']} | "
            f"TCP: {peer['port']} | "
            f"Status: {peer['status']}"
        )

    lines.append("==================================")

    return "\n".join(lines)


# A4. CHANGE STATUS

def change_status(new_status, sock):

    new_status = new_status.upper()

    if new_status not in ("ONLINE", "AWAY"):
        print("Invalid status! Use ONLINE or AWAY.")
        return False

    # Update current device status
    with shared_state.state_lock:

        shared_state.my_info["status"] = new_status

        name = shared_state.my_info["name"]
        tcp_port = shared_state.my_info["tcp_port"]

    try:
        # Encode new status
        data = protocol.encode_udp_hello(
            name,
            tcp_port,
            new_status
        )

        # Broadcast immediately
        sock.sendto(
            data,
            (BROADCAST_ADDRESS, UDP_PORT)
        )

        print(f"[Status] Your status is now {new_status}")

        return True

    except OSError as error:
        print(f"[Status Broadcast Error] {error}")
        return False
