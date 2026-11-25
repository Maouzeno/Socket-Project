import socket
import shared_state
import threading
import protocol
import tcp_file_transfer

# Reading and Extracting bytes received from socket
def bytes_extract(sock: socket.socket, number_of_bytes: int) -> bytes:
    return tcp_file_transfer.recv_exact(sock, number_of_bytes)



# Reading the header of received segment
def header_reader(sock: socket.socket) -> bytes:
    # Initialize a buffer
    buffer = bytearray()

    # Read the header until we see '|'
    while True:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("Lost connection!")
        if byte == b'|':
            return buffer.decode("utf-8")
        buffer.extend(byte)



# Clear up connection in active_connection from shared_state
def clear_connection(peer: str, sock: socket.socket):
    # Checking if the peer's name is existed and matched up with the socket before delete
    if peer:
        with shared_state.state_lock:
            if shared_state.active_connections.get(peer) is sock:
                del shared_state.active_connections[peer]
    try:
        sock.close()
    except OSError:
        pass



# Listening to another pear
def peer_listener(sock: socket.socket, known_peer_name: str):
    peer_name = known_peer_name
    try:
        while True:
            cmd = header_reader(sock)

            # TCP indentification header ("INDENT|<name>|<tcp_port>|")
            if cmd == "IDENT":
                sender_name = header_reader(sock)
                sender_port = header_reader(sock)
                peer_name = sender_name
                with shared_state.state_lock:
                    shared_state.active_connections[sender_name] = sock

            # TCP message header ("MSG|<byte_len>|<text>")
            elif cmd == "MSG":
                byte_len = int(header_reader(sock))
                raw_msg = bytes_extract(sock, byte_len)
                text = raw_msg.decode("utf-8")
                sender = peer_name or "Unknown"
                formatted_message = f"[{sender}]: {text}"

                # Updating shared_state with the new message and unread count
                with shared_state.state_lock:
                    if sender not in shared_state.chat_histories:
                        shared_state.chat_histories[sender] = []
                    shared_state.chat_histories[sender].append(formatted_message)

                    is_active_chat = (shared_state.active_chat_user == sender)
                    if not is_active_chat:  
                        shared_state.unread_counts[sender] = (shared_state.unread_counts.get(sender, 0) + 1)

            # TCP file transfer header ("FILE|<filename>|<size_in_bytes>|")
            elif cmd == "FILE":
                filename = header_reader(sock)
                size_in_bytes = int(header_reader(sock))
                sender = peer_name or "Unknown"
                tcp_file_transfer.receive_file(sock, filename, size_in_bytes, sender)


            elif cmd == "BYE":
                if peer_name:
                    print(f"[INFO]: {peer_name} has disconnected.")
                break
    except (ConnectionError, OSError, ValueError) as e:
        print(f"[ERROR]: Connection with {peer_name} lost: {e}")
    finally:
        clear_connection(peer_name, sock)



# TCP acceptor
def tcp_acceptor():
    with shared_state.state_lock:
        my_port = shared_state.my_info["tcp_port"]

        # Create a TCP socket object
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) #SOCK_DGRAM FOR UDP

        # Ignoring TIME_WAIT state to allow immediate reuse of the port after the program exits
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Assigning an IP address and port number to the socket
        server_socket.bind(('', my_port))        

        # Accepting maximum 5 incoming connections in the queue
        server_socket.listen(5)

        while True:
            try:
                # Accepting a new connection
                client_socket, addr = server_socket.accept()
                print(f"[INFO]: Connection established with {addr}")

                # Start a new thread to handle the incoming connection
                listener_thread = threading.Thread(target=peer_listener, args=(client_socket, None), daemon=True)
                listener_thread.start()
            except OSError:
                break



def connect_to_peer(peer_name: str) -> bool:
    with shared_state.state_lock:
        # Checking if the peer is already connected, if so, return True then exit the function
        if peer_name in shared_state.active_connections:
            return True
        
        # If not connected, get the peer's information from the online users list
        peer_info = shared_state.online_users.get(peer_name)
        my_name = shared_state.my_info["name"]
        my_port = shared_state.my_info["tcp_port"]
    
    # If the peer is not found in the online users list, they are offline
    if not peer_info:
        print(f"[ERROR]: User '{peer_name}' is not online.")
        return False

    # Attempting to establish a TCP connection with the peer
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((peer_info["ip"], peer_info["port"]))
        sock.sendall(protocol.encode_tcp_ident(my_name, my_port))

        # If the connection is successful, add the socket to the active connections dictionary
        with shared_state.state_lock:
            shared_state.active_connections[peer_name] = sock

        # Start a new thread to listen for incoming messages from the peer
        listener_thread = threading.Thread(target=peer_listener, args=(sock, peer_name), daemon=True)
        listener_thread.start()
        return True
    except OSError:
        print(f"[ERROR]: Could not connect to {peer_name}.")
        return False



# Sending messages to another peer
def send_message(peer_name: str, message: str) -> bool:
    # First check if the peer is connected
    if not connect_to_peer(peer_name):
        return False
    
    #if they are connected, retrieve the socket from the active connections dictionary
    with shared_state.state_lock: 
        sock = shared_state.active_connections.get(peer_name)
    if not sock: 
        return False

    # Attempt to send the message
    try:
        packet = protocol.encode_tcp_msg(message)
        sock.sendall(packet)

        # Update the chat history and unread count
        with shared_state.state_lock:
            shared_state.chat_histories.setdefault(peer_name, []).append(f"[Me]: {message}")
        return True
    except OSError:
        clear_connection(peer_name, sock)
        print(f"[ERROR]: Failed to send message to {peer_name}.")
        return False
    


# Closing all active connections
def close_all_connections():
    with shared_state.state_lock:
        conns = list(shared_state.active_connections.items())
        shared_state.active_connections.clear()

    bye_packet = protocol.encode_tcp_bye()
    for _name, sock in conns:
        try:
            sock.sendall(bye_packet)
            sock.close()
        except OSError:
            pass