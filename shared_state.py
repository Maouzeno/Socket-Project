import threading


state_lock = threading.Lock()

my_info = {"name": "", "tcp_port": 0, "status": "online"}

online_users = {}
active_connections = {}
unread_counts = {}
chat_histories = {}

active_chat_user = None
