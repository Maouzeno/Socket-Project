"""
Central in-memory state shared across the UI thread, UDP discovery threads,
TCP acceptor/listener threads, and file-transfer worker threads.

IMPORTANT (Thread Safety):
Because multiple threads read and modify these dictionaries concurrently,
EVERY access must be wrapped in `with shared_state.state_lock:` to prevent
race conditions or RuntimeError ("dictionary changed size during iteration").
"""

import threading

# Global mutual-exclusion lock (mutex) protecting all shared variables below.
# Only one thread can hold this lock at a time.
state_lock = threading.Lock()

# Information about THIS local device (populated at startup in main.py).
# - "name": Display username shown to other peers.
# - "tcp_port": Port where our TCP server (tcp_acceptor) listens for chat & file connections.
# - "status": Current presence status ("online" or "away"), broadcast via UDP.
my_info = {
    "name": "",
    "tcp_port": 0,
    "status": "ONLINE"
}

# Peers currently discovered on the LAN via UDP "HELLO" broadcasts.
# Structure: { "peer_name": {"ip": str, "port": int, "status": str, "last_seen": float} }
# - "ip": Source IPv4 address extracted from the UDP packet header.
# - "port": Peer's listening TCP port announced inside the HELLO payload.
# - "last_seen": Unix timestamp (time.time()) of the most recent heartbeat, used for timeout cleanup.
online_users = {}

# Active persistent TCP chat sockets, keyed by peer username.
# Structure: { "peer_name": socket.socket }
# Note: Dedicated temporary sockets used for file transfers are NOT stored here.
active_connections = {}

# Number of unread messages/files received from each peer while not viewing their chat.
# Structure: { "peer_name": int }
unread_counts = {}

# In-memory log of sent and received messages/file notifications per peer.
# Structure: { "peer_name": [str, str, ...] }
chat_histories = {}

# Username of the peer currently selected in the UI chat window (or None if none selected).
# Used by tcp_chat.peer_listener() to decide whether to increment unread_counts.
active_chat_user = None
