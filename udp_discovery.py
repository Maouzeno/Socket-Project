"""
UDP Peer Discovery & Presence Module.

Uses connectionless UDP broadcasts over the LAN so devices automatically find each other
without needing a central server:
- A broadcaster thread periodically sends "HELLO|<name>|<tcp_port>|<status>" to 255.255.255.255.
- A listener thread receives incoming HELLO packets, records each peer's IP/TCP port/status,
  and removes peers whose heartbeat has expired (elapsed > PEER_TIMEOUT).
"""

import socket
import threading
import time

import shared_state
import protocol


# Import network constants from root config.py
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
    Create, configure, and bind a UDP socket capable of both sending and receiving LAN broadcasts.

    Args:
        port (int): Local UDP port to bind to (default: config.UDP_PORT = 9999).

    Returns:
        socket.socket: Bound UDP socket ready for broadcaster and listener threads.
    """
    # AF_INET = IPv4; SOCK_DGRAM = UDP (connectionless datagram transport).
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # SO_REUSEADDR allows immediate port reuse after restart (and multiple local test instances on Windows).
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # SO_BROADCAST is REQUIRED by the OS before sending packets to a broadcast address (255.255.255.255).
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    # 1-second receive timeout so udp_listener_loop wakes up periodically to check PEER_TIMEOUT and stop_event.
    sock.settimeout(1.0)

    # Bind to "" (0.0.0.0) to listen for incoming UDP broadcasts on ALL local network interfaces.
    sock.bind(("", port))
    return sock


# A1. UDP BROADCASTER


def udp_broadcaster_loop(sock, stop_event=None, udp_port=UDP_PORT, broadcast_address=BROADCAST_ADDRESS):
    """
    Background loop that broadcasts this device's presence on the LAN every BROADCAST_INTERVAL seconds.

    Args:
        sock (socket.socket): Configured UDP socket with SO_BROADCAST enabled.
        stop_event (threading.Event | None): Optional event signaled during shutdown to exit cleanly.
        udp_port (int): Destination UDP port that all peers listen on.
        broadcast_address (str): Destination IPv4 broadcast address (default: 255.255.255.255).
    """

    while stop_event is None or not stop_event.is_set():

        try:
            # Read current device info under state_lock in case status changed concurrently.
            with shared_state.state_lock:
                name = shared_state.my_info["name"]
                tcp_port = shared_state.my_info["tcp_port"]
                status = shared_state.my_info["status"]

            # Build UTF-8 wire packet: "HELLO|<name>|<tcp_port>|<status>"
            data = protocol.encode_udp_hello(
                name,
                tcp_port,
                status
            )

            # Send UDP datagram to every host listening on udp_port in the local subnet.
            sock.sendto(
                data,
                (broadcast_address, udp_port)
            )

        except OSError as error:
            print(f"[Broadcast Error] {error}")

        # Sleep until the next heartbeat. Using stop_event.wait() lets shutdown interrupt the sleep immediately.
        if stop_event is not None:
            if stop_event.wait(BROADCAST_INTERVAL):
                break
        else:
            time.sleep(BROADCAST_INTERVAL)


# A2 + A3. UDP LISTENER


def udp_listener_loop(sock, print_callback=print, stop_event=None):
    """
    Background loop that receives UDP HELLO datagrams, maintains shared_state.online_users,
    and expires peers that stop broadcasting.

    Args:
        sock (socket.socket): Bound UDP socket (with timeout set in create_udp_socket).
        print_callback (Callable[[str], None]): Thread-safe callback for UI/console status notifications.
        stop_event (threading.Event | None): Optional event signaled during shutdown to stop the loop.
    """

    while stop_event is None or not stop_event.is_set():

        try:
            # Block up to 1.0s waiting for a UDP datagram.
            # `address` is a tuple (sender_ip, sender_udp_port) from the IP/UDP packet header.
            data, address = sock.recvfrom(BUFFER_SIZE)

            # Parse and validate "HELLO|<name>|<tcp_port>|<status>"
            result = protocol.parse_udp_hello(data)

            if result is not None:

                name, tcp_port, status = result
                # Normalize status to lowercase so "ONLINE" and "online" are treated identically.
                status = status.lower()

                # Reject unexpected status values
                if status not in ("online", "away"):
                    result = None

                else:
                    # Broadcasts loop back to our own socket too, so ignore packets matching our name + TCP port.
                    with shared_state.state_lock:
                        my_name = shared_state.my_info["name"]
                        my_port = shared_state.my_info["tcp_port"]

                    if name == my_name and tcp_port == my_port:
                        result = None

                if result is not None:

                    current_time = time.time()

                    # Update shared online_users table atomically under state_lock.
                    with shared_state.state_lock:

                        is_new = name not in shared_state.online_users

                        old_status = None

                        if not is_new:
                            old_status = (
                                shared_state.online_users[name]["status"]
                            )

                        # Record the peer's IP (from UDP packet source) and TCP port (from HELLO payload).
                        shared_state.online_users[name] = {
                            "ip": address[0],
                            "port": tcp_port,
                            "status": status,
                            "last_seen": current_time
                        }

                    # Emit UI/console notifications OUTSIDE state_lock to avoid holding the lock during I/O.
                    if is_new:
                        print_callback(
                            f"[+] {name} ({address[0]}) is now online"
                        )

                    elif old_status != status:
                        print_callback(
                            f"[*] {name} changed status to {status}"
                        )

        except socket.timeout:
            # Expected every 1.0s when the LAN is quiet; lets execution fall through to timeout cleanup below.
            pass

        except OSError as error:
            # If the socket was closed intentionally during shutdown, exit cleanly without logging an error.
            if stop_event is not None and stop_event.is_set():
                break

            print_callback(f"[Listener Error] {error}")

            # fileno() == -1 indicates the underlying OS socket descriptor is closed.
            if sock.fileno() == -1:
                break

        # A3. REMOVE INACTIVE PEERS
        # Runs after every received packet OR every 1-second socket timeout.

        current_time = time.time()
        offline_users = []

        with shared_state.state_lock:

            # Snapshot items with list(...) because we cannot delete from a dict while iterating over it directly.
            for name, peer in list(
                shared_state.online_users.items()
            ):

                elapsed = current_time - peer["last_seen"]

                # If no HELLO packet was heard within PEER_TIMEOUT seconds, consider the peer offline.
                if elapsed > PEER_TIMEOUT:

                    del shared_state.online_users[name]

                    offline_users.append(name)

        # Notify UI outside the lock for each timed-out peer.
        for name in offline_users:
            print_callback(
                f"[-] {name} is now offline"
            )


# A4. GET FORMATTED ONLINE LIST


def get_formatted_online_list():
    """
    Build a human-readable summary table of all currently online peers.

    Returns:
        str: Formatted multi-line text listing each peer's name, IP, TCP port, and status.
    """

    # Copy peer dicts under state_lock so formatting happens safely outside the lock.
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

def change_status(new_status, sock, udp_port=UDP_PORT, broadcast_address=BROADCAST_ADDRESS):
    """
    Update this device's presence status and immediately broadcast a UDP HELLO packet
    so peers see the change right away instead of waiting for the next BROADCAST_INTERVAL.

    Args:
        new_status (str): Desired status ("online" or "away", case-insensitive).
        sock (socket.socket): Configured UDP socket used to send the immediate broadcast.
        udp_port (int): Destination UDP port for the broadcast.
        broadcast_address (str): Destination IPv4 broadcast address.

    Returns:
        bool: True if status was updated and broadcast succeeded, False otherwise.
    """

    new_status = new_status.lower()

    if new_status not in ("online", "away"):
        print("Invalid status! Use ONLINE or AWAY.")
        return False

    # Update local status and capture name/port atomically.
    with shared_state.state_lock:

        shared_state.my_info["status"] = new_status

        name = shared_state.my_info["name"]
        tcp_port = shared_state.my_info["tcp_port"]

    try:
        # Encode updated presence packet
        data = protocol.encode_udp_hello(
            name,
            tcp_port,
            new_status
        )

        # Broadcast immediately so peers update their UI without waiting 3 seconds.
        sock.sendto(
            data,
            (broadcast_address, udp_port)
        )

        print(f"[Status] Your status is now {new_status}")

        return True

    except OSError as error:
        print(f"[Status Broadcast Error] {error}")
        return False
