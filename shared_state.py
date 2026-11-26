import threading

state_lock = threading.Lock()

# current information of the device
my_info = {
    "name": "",
    "tcp_port": 0,
    "status": "ONLINE"
}

# struture: { "peer_name": {"ip": str, "port": int, "status": str, "last_seen": float} } (online peer)
online_users = {}

# struture: { "peer_name": socket.socket } (Connected peer)
active_connections = {}

# struture: { "peer_name": int }
unread_counts = {}


# struture: { "peer_name": [str, str, ...] }
chat_histories = {}

# selected peer
active_chat_user = None