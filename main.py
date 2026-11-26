"""
Main Application Entry Point & Controller for LAN Chat & File Sharing.

Responsibilities:
- Parse optional command-line arguments (--name, --port, --udp-port, --broadcast)
  or prompt the user interactively via the Tkinter login dialog.
- Initialize shared_state.my_info and bind the UDP discovery socket.
- Start the 3 core background networking daemon threads:
    1. udp_broadcaster_loop  (broadcasts our HELLO heartbeat every 3s)
    2. udp_listener_loop     (discovers LAN peers & removes timed-out peers)
    3. tcp_acceptor          (accepts incoming TCP chat & file connections)
- Connect UIManager user actions (select peer, send message, send file, change status, close)
  to the underlying TCP/UDP modules, and periodically sync shared_state into the UI.
"""

import argparse
import threading
import tkinter as tk
from tkinter import messagebox

import config
import shared_state
import tcp_chat
import tcp_file_transfer
import udp_discovery
from ui_manager import UIManager, prompt_login


def parse_args():
    """
    Parse optional command-line flags so users can either launch interactively
    (`python main.py`) or skip the login prompt (`python main.py --name Alice --port 5001`).

    Returns:
        argparse.Namespace: Parsed CLI options (name, port, udp_port, broadcast).
    """
    parser = argparse.ArgumentParser(description="LAN Chat & File Sharing")
    parser.add_argument("--name", type=str, default="", help="Your display name")
    parser.add_argument("--port", type=int, default=0, help="Your listening TCP port")
    parser.add_argument(
        "--udp-port",
        type=int,
        default=config.UDP_PORT,
        help=f"UDP discovery port (default: {config.UDP_PORT})",
    )
    parser.add_argument(
        "--broadcast",
        type=str,
        default=config.BROADCAST_ADDRESS,
        help=f"UDP broadcast address (default: {config.BROADCAST_ADDRESS})",
    )
    return parser.parse_args()


def get_startup_credentials(args):
    """
    Resolve the local device's username and listening TCP port.
    Uses CLI args directly if both are valid; otherwise opens the interactive login dialog.

    Args:
        args (argparse.Namespace): Parsed CLI arguments from parse_args().

    Returns:
        tuple[str, int] | None: (username, tcp_port), or None if the user closed the login dialog.
    """
    name = args.name.strip()
    port = args.port

    # Disallow '|' in username because protocol.py uses '|' as the wire header delimiter.
    if name and "|" not in name and 1 <= port <= 65535:
        return name, port

    default_port = port if 1 <= port <= 65535 else config.TCP_PORT
    return prompt_login(default_name=name, default_port=default_port)


def main():
    """
    Bootstrap networking threads, wire UIManager callbacks, and run the Tkinter main loop.
    """
    args = parse_args()
    credentials = get_startup_credentials(args)
    if credentials is None:
        return

    my_name, my_port = credentials
    udp_port = args.udp_port
    broadcast_address = args.broadcast

    # Populate shared device identity BEFORE launching network threads so UDP broadcasts
    # and tcp_acceptor() immediately see the chosen username and TCP port.
    with shared_state.state_lock:
        shared_state.my_info["name"] = my_name
        shared_state.my_info["tcp_port"] = my_port
        shared_state.my_info["status"] = "online"

    # Bind the shared UDP discovery socket (used by both broadcaster and listener threads).
    try:
        udp_sock = udp_discovery.create_udp_socket(port=udp_port)
    except OSError as error:
        err_root = tk.Tk()
        err_root.withdraw()
        messagebox.showerror(
            "Startup Error",
            f"Could not bind UDP discovery socket on port {udp_port}:\n{error}",
        )
        err_root.destroy()
        return

    # Shared stop flag signaled when closing the window so UDP loops terminate cleanly.
    stop_event = threading.Event()
    ui = UIManager(my_name=my_name, my_port=my_port)

    # ------------------------------------------------------------------
    # State snapshot & UI sync (runs on the Tkinter main thread)
    # ------------------------------------------------------------------

    def sync_ui():
        """
        Take a thread-safe snapshot of shared_state under state_lock and pass plain copies
        to UIManager.refresh_state() so GUI rendering never holds the network lock.
        """
        with shared_state.state_lock:
            online_copy = {
                peer: info.copy()
                for peer, info in shared_state.online_users.items()
            }
            active_peer = shared_state.active_chat_user
            history_copy = list(shared_state.chat_histories.get(active_peer, [])) if active_peer else []
            unread_copy = dict(shared_state.unread_counts)
            my_status = shared_state.my_info["status"]

        ui.refresh_state(
            online_users=online_copy,
            active_peer=active_peer,
            chat_history=history_copy,
            unread_counts=unread_copy,
            my_status=my_status,
        )

    # ------------------------------------------------------------------
    # UI action callbacks (invoked by UIManager when the user interacts)
    # ------------------------------------------------------------------

    def handle_select_peer(peer_name: str):
        """
        Switch the active chat window to `peer_name` and clear their unread counter.

        Args:
            peer_name (str): Username clicked in the Online Peers sidebar.
        """
        with shared_state.state_lock:
            shared_state.active_chat_user = peer_name
            shared_state.unread_counts[peer_name] = 0
        sync_ui()

    def handle_send_message(peer_name: str, text: str):
        """
        Send a TCP chat message in a short-lived background thread so a slow TCP connect()
        (up to 5s timeout) never freezes the Tkinter UI.

        Args:
            peer_name (str): Target peer's username.
            text (str): Message content entered by the user.
        """
        def _worker():
            tcp_chat.send_message(peer_name, text, print_callback=ui.log_event)

        threading.Thread(target=_worker, daemon=True).start()

    def handle_send_file(peer_name: str, filepath: str):
        """
        Trigger an asynchronous dedicated-TCP-socket file transfer to `peer_name`.

        Args:
            peer_name (str): Target peer's username.
            filepath (str): Local path picked from the file dialog.
        """
        tcp_file_transfer.send_file_async(
            peer_name=peer_name,
            filepath=filepath,
            print_callback=ui.log_event,
        )

    def handle_status_change(new_status: str):
        """
        Update local presence status ("online" / "away") and immediately broadcast a UDP HELLO.

        Args:
            new_status (str): New status chosen from the sidebar toggle buttons.
        """
        def _worker():
            ok = udp_discovery.change_status(
                new_status,
                udp_sock,
                udp_port=udp_port,
                broadcast_address=broadcast_address,
            )
            if ok:
                ui.log_event(f"[INFO] Your status is now {new_status}")
            else:
                ui.log_event(f"[ERROR] Failed to change status to {new_status}")

        threading.Thread(target=_worker, daemon=True).start()

    def handle_close():
        """
        Gracefully shut down UDP discovery, send TCP BYE to all connected chat peers,
        close open sockets, and destroy the Tkinter window.
        """
        stop_event.set()
        try:
            udp_sock.close()
        except OSError:
            pass
        tcp_chat.close_all_connections()
        ui.close()

    # Inject controller callbacks into the decoupled UI wrapper.
    ui.set_callbacks(
        on_select_peer=handle_select_peer,
        on_send_message=handle_send_message,
        on_send_file=handle_send_file,
        on_status_change=handle_status_change,
        on_close=handle_close,
    )

    # ------------------------------------------------------------------
    # Start background networking daemon threads
    # (daemon=True guarantees threads exit automatically when the main UI exits)
    # ------------------------------------------------------------------

    broadcaster_thread = threading.Thread(
        target=udp_discovery.udp_broadcaster_loop,
        args=(udp_sock,),
        kwargs={
            "stop_event": stop_event,
            "udp_port": udp_port,
            "broadcast_address": broadcast_address,
        },
        name="udp-broadcaster",
        daemon=True,
    )
    listener_thread = threading.Thread(
        target=udp_discovery.udp_listener_loop,
        args=(udp_sock,),
        kwargs={
            "print_callback": ui.log_event,
            "stop_event": stop_event,
        },
        name="udp-listener",
        daemon=True,
    )
    acceptor_thread = threading.Thread(
        target=tcp_chat.tcp_acceptor,
        kwargs={"print_callback": ui.log_event},
        name="tcp-acceptor",
        daemon=True,
    )

    broadcaster_thread.start()
    listener_thread.start()
    acceptor_thread.start()

    ui.log_event(f"[OK] Started as '{my_name}' (TCP :{my_port}, UDP :{udp_port})")
    sync_ui()
    # Poll shared_state every 200ms on the UI thread to reflect incoming peers, messages, and files.
    ui.schedule_poll(200, sync_ui)
    ui.run()


if __name__ == "__main__":
    main()
