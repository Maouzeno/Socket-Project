"""
Network and discovery configuration constants shared across the application.
"""

# Default UDP port used by ALL peers to broadcast and listen for "HELLO" discovery packets.
# Every peer on the LAN must use the same UDP_PORT to discover each other.
UDP_PORT = 9999

# Default TCP port for incoming peer-to-peer chat and file-transfer connections.
# Each instance running on the SAME machine must choose a different TCP_PORT (e.g., 5001, 5002).
TCP_PORT = 5000

# How often (in seconds) this device broadcasts a UDP "HELLO" heartbeat to announce it is online.
BROADCAST_INTERVAL = 3

# How long (in seconds) we wait without hearing a peer's UDP heartbeat before marking them offline.
# Set higher than BROADCAST_INTERVAL (e.g., 10s > 3s) to tolerate occasional dropped UDP packets.
PEER_TIMEOUT = 10

# IPv4 limited broadcast address: packets sent here are delivered to all devices on the local LAN subnet.
BROADCAST_ADDRESS = "255.255.255.255"

# Maximum number of bytes read per UDP datagram in recvfrom().
# 1024 bytes is plenty for small UTF-8 discovery packets like "HELLO|Alice|5000|online".
BUFFER_SIZE = 1024
