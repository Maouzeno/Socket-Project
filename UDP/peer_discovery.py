
import socket
import json
import threading
import time
import uuid

from config import (
    UDP_PORT,
    TCP_PORT,
    BROADCAST_INTERVAL,
    PEER_TIMEOUT,
    BUFFER_SIZE
)


class PeerDiscovery:

    def __init__(self, username, tcp_port=TCP_PORT):

        self.username = username
        self.tcp_port = tcp_port

        # Unique ID for each running instance
        self.peer_id = str(uuid.uuid4())

        # Current user status
        self.status = "online"

        # Store discovered peers
        self.peers = {}

        # Thread synchronization
        self.lock = threading.Lock()

        # Control threads
        self.running = False

        # Broadcast address
        self.broadcast_address = "255.255.255.255"

        # UDP socket
        self.listen_socket = None

    # 1. BROADCAST PRESENCE (UDP)

    def broadcast_presence(self):

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BROADCAST,
            1
        )

        while self.running:

            try:
                with self.lock:
                    current_status = self.status

                message = {
                    "type": "presence",
                    "peer_id": self.peer_id,
                    "name": self.username,
                    "tcp_port": self.tcp_port,
                    "status": current_status
                }

                data = json.dumps(message).encode("utf-8")

                sock.sendto(
                    data,
                    (self.broadcast_address, UDP_PORT)
                )

            except OSError as e:
                if self.running:
                    print("[Broadcast Error]", e)

            # Broadcast every 3 seconds
            time.sleep(BROADCAST_INTERVAL)

        sock.close()

    # 2. LISTEN FOR OTHER PEERS (UDP)

    def listen_for_peers(self):

        sock = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM
        )

        self.listen_socket = sock

        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )

        try:
            sock.bind(("", UDP_PORT))
            sock.settimeout(1)

            while self.running:

                try:
                    data, address = sock.recvfrom(BUFFER_SIZE)

                    message = json.loads(
                        data.decode("utf-8")
                    )

                    # Check message type
                    if message.get("type") != "presence":
                        continue

                    # Ignore own broadcast
                    if message.get("peer_id") == self.peer_id:
                        continue

                    name = message.get("name")
                    peer_id = message.get("peer_id")
                    tcp_port = message.get("tcp_port")
                    status = message.get("status")

                    # Validate message
                    if not isinstance(name, str) or not name.strip():
                        continue

                    if not isinstance(peer_id, str) or not peer_id:
                        continue

                    if (
                        not isinstance(tcp_port, int)
                        or isinstance(tcp_port, bool)
                        or not 1 <= tcp_port <= 65535
                    ):
                        continue

                    if status not in ("online", "away"):
                        continue

                    self.update_peer(
                        peer_id,
                        name,
                        address[0],
                        tcp_port,
                        status
                    )

                except socket.timeout:
                    pass

                except (ValueError, UnicodeDecodeError, AttributeError):
                    print("[Error] Invalid UDP message")

                except OSError as e:
                    if self.running:
                        print("[Listen Error]", e)

                self.remove_inactive_peers()

        except OSError as e:
            print("[UDP Socket Error]", e)

        finally:
            sock.close()
            self.listen_socket = None

    # 3. UPDATE PEER INFORMATION

    def update_peer(self, peer_id, name, ip, tcp_port, status):

        with self.lock:

            is_new = peer_id not in self.peers

            old_status = (
                self.peers[peer_id]["status"]
                if not is_new else None
            )

            self.peers[peer_id] = {
                "name": name,
                "ip": ip,
                "tcp_port": tcp_port,
                "status": status,
                "last_seen": time.monotonic()
            }

            if is_new:
                print(f"\n[+] {name} is now online")

            elif old_status != status:
                print(
                    f"\n[*] {name} changed status to {status}"
                )

    # 4. REMOVE INACTIVE PEERS

    def remove_inactive_peers(self):

        current_time = time.monotonic()

        with self.lock:

            inactive_peers = []

            for peer_id, peer in self.peers.items():

                elapsed = (
                    current_time - peer["last_seen"]
                )

                if elapsed > PEER_TIMEOUT:
                    inactive_peers.append(peer_id)

            for peer_id in inactive_peers:

                peer = self.peers.pop(peer_id)

                print(
                    f"\n[-] {peer['name']} is now offline"
                )

    # 5. CHANGE ONLINE STATUS

    def set_status(self, new_status):

        if new_status not in ("online", "away"):
            print("Invalid status! Use online or away.")
            return

        with self.lock:
            if self.status == new_status:
                print(f"Your status is already {new_status}")
                return

            self.status = new_status

        print(f"Your status is now: {new_status}")

    # 6. DISPLAY ONLINE USERS

    def show_peers(self):

        with self.lock:

            print("\n========== ONLINE USERS ==========")

            if not self.peers:
                print("No other peers online.")

            for peer in self.peers.values():

                print(
                    f"Name: {peer['name']} | "
                    f"IP: {peer['ip']} | "
                    f"TCP: {peer['tcp_port']} | "
                    f"Status: {peer['status']}"
                )

            print("==================================")

    # 7. GET PEER LIST

    def get_peers(self):

        with self.lock:
            return {
                peer_id: peer.copy()
                for peer_id, peer in self.peers.items()
            }

    # 8. START PEER DISCOVERY

    def start(self):

        if self.running:
            return

        self.running = True

        self.broadcast_thread = threading.Thread(
            target=self.broadcast_presence,
            daemon=True
        )

        self.listen_thread = threading.Thread(
            target=self.listen_for_peers,
            daemon=True
        )

        self.broadcast_thread.start()
        self.listen_thread.start()

        print(
            f"[Peer Discovery] Started as {self.username}"
        )

    # 9. STOP PEER DISCOVERY

    def stop(self):

        if not self.running:
            return

        self.running = False

        # Close socket to stop listening
        if self.listen_socket:
            self.listen_socket.close()

        self.broadcast_thread.join(timeout=4)
        self.listen_thread.join(timeout=4)

        print("[Peer Discovery] Stopped")
