import os
import queue
import re
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Callable, Dict, List, Optional, Tuple


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

# Dark slate theme palette
COLORS = {
    "bg_dark": "#0f172a",
    "bg_panel": "#1e293b",
    "bg_input": "#0b1220",
    "bg_hover": "#334155",
    "bg_selected": "#2563eb",
    "border": "#334155",
    "text_primary": "#f8fafc",
    "text_secondary": "#94a3b8",
    "text_muted": "#64748b",
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "online": "#22c55e",
    "away": "#f59e0b",
    "file": "#38bdf8",
    "error": "#f87171",
    "me": "#60a5fa",
    "peer": "#34d399",
}


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _center_window(window: tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


def prompt_login(default_name: str = "", default_port: int = 5000) -> Optional[Tuple[str, int]]:
    """
    Display a startup login dialog to collect username and TCP port.
    Returns (username, tcp_port) or None if the user cancels.
    """
    result: Dict[str, Optional[Tuple[str, int]]] = {"value": None}

    dialog = tk.Tk()
    dialog.title("LAN Chat — Sign In")
    dialog.configure(bg=COLORS["bg_dark"])
    dialog.resizable(False, False)
    _center_window(dialog, 380, 290)

    container = tk.Frame(dialog, bg=COLORS["bg_dark"], padx=28, pady=24)
    container.pack(fill="both", expand=True)

    tk.Label(
        container,
        text="LAN Chat & File Sharing",
        font=("Segoe UI", 15, "bold"),
        fg=COLORS["text_primary"],
        bg=COLORS["bg_dark"],
    ).pack(anchor="w")

    tk.Label(
        container,
        text="Enter your display name and listening TCP port.",
        font=("Segoe UI", 9),
        fg=COLORS["text_secondary"],
        bg=COLORS["bg_dark"],
    ).pack(anchor="w", pady=(2, 16))

    # Username field
    tk.Label(
        container,
        text="Username",
        font=("Segoe UI", 9, "bold"),
        fg=COLORS["text_secondary"],
        bg=COLORS["bg_dark"],
    ).pack(anchor="w")

    name_var = tk.StringVar(value=default_name)
    name_entry = tk.Entry(
        container,
        textvariable=name_var,
        font=("Segoe UI", 10),
        bg=COLORS["bg_panel"],
        fg=COLORS["text_primary"],
        insertbackground=COLORS["text_primary"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
    )
    name_entry.pack(fill="x", ipady=6, pady=(4, 12))

    # TCP Port field
    tk.Label(
        container,
        text="TCP Port (1 - 65535)",
        font=("Segoe UI", 9, "bold"),
        fg=COLORS["text_secondary"],
        bg=COLORS["bg_dark"],
    ).pack(anchor="w")

    port_var = tk.StringVar(value=str(default_port))
    port_entry = tk.Entry(
        container,
        textvariable=port_var,
        font=("Segoe UI", 10),
        bg=COLORS["bg_panel"],
        fg=COLORS["text_primary"],
        insertbackground=COLORS["text_primary"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=COLORS["border"],
        highlightcolor=COLORS["accent"],
    )
    port_entry.pack(fill="x", ipady=6, pady=(4, 8))

    error_label = tk.Label(
        container,
        text="",
        font=("Segoe UI", 9),
        fg=COLORS["error"],
        bg=COLORS["bg_dark"],
    )
    error_label.pack(anchor="w", pady=(0, 8))

    def submit(_event=None):
        name = name_var.get().strip()
        port_raw = port_var.get().strip()

        if not name:
            error_label.config(text="Username cannot be empty.")
            name_entry.focus_set()
            return
        if "|" in name:
            error_label.config(text="Username cannot contain the '|' character.")
            name_entry.focus_set()
            return

        try:
            port = int(port_raw)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            error_label.config(text="TCP Port must be an integer between 1 and 65535.")
            port_entry.focus_set()
            return

        result["value"] = (name, port)
        dialog.destroy()

    start_btn = tk.Button(
        container,
        text="Start Chat",
        command=submit,
        font=("Segoe UI", 10, "bold"),
        bg=COLORS["accent"],
        fg=COLORS["text_primary"],
        activebackground=COLORS["accent_hover"],
        activeforeground=COLORS["text_primary"],
        relief="flat",
        cursor="hand2",
    )
    start_btn.pack(fill="x", ipady=6)

    dialog.bind("<Return>", submit)
    if default_name:
        port_entry.focus_set()
    else:
        name_entry.focus_set()

    dialog.mainloop()
    return result["value"]


class UIManager:
    """
    Tkinter UI wrapper for LAN Chat & File Sharing.
    Keeps all GUI logic decoupled from networking modules.
    """

    def __init__(self, my_name: str, my_port: int):
        self.my_name = my_name
        self.my_port = my_port
        self.my_status = "online"

        # Thread-safe queue for events/logs emitted by background threads
        self._event_queue: "queue.Queue[str]" = queue.Queue()
        self._progress_clear_job: Optional[str] = None
        self._closed = False

        # Callbacks injected by main.py
        self._on_select_peer: Optional[Callable[[str], None]] = None
        self._on_send_message: Optional[Callable[[str, str], None]] = None
        self._on_send_file: Optional[Callable[[str, str], None]] = None
        self._on_status_change: Optional[Callable[[str], None]] = None
        self._on_close: Optional[Callable[[], None]] = None

        # Cached render state to avoid unnecessary redraws / flicker
        self._peer_order: List[str] = []
        self._last_peer_snapshot: Optional[Tuple] = None
        self._last_chat_snapshot: Optional[Tuple] = None
        self._active_peer: Optional[str] = None

        # Build main window
        self.root = tk.Tk()
        self.root.title(f"LAN Chat — {my_name} (:{my_port})")
        self.root.configure(bg=COLORS["bg_dark"])
        self.root.minsize(780, 520)
        _center_window(self.root, 960, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_window_close)

        self._build_layout()
        self._update_status_buttons("online")
        self._drain_event_queue()

    # ------------------------------------------------------------------
    # Public wrapper API for main.py
    # ------------------------------------------------------------------

    def set_callbacks(
        self,
        on_select_peer: Optional[Callable[[str], None]] = None,
        on_send_message: Optional[Callable[[str, str], None]] = None,
        on_send_file: Optional[Callable[[str, str], None]] = None,
        on_status_change: Optional[Callable[[str], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_select_peer = on_select_peer
        self._on_send_message = on_send_message
        self._on_send_file = on_send_file
        self._on_status_change = on_status_change
        self._on_close = on_close

    def log_event(self, text: str) -> None:
        """
        Thread-safe callback compatible with udp_discovery, tcp_chat, and tcp_file_transfer.
        """
        self._event_queue.put(str(text))

    def schedule_poll(self, interval_ms: int, callback: Callable[[], None]) -> None:
        """
        Run `callback` repeatedly on the Tkinter main thread every `interval_ms`.
        """
        def _tick():
            if self._closed:
                return
            try:
                callback()
            finally:
                if not self._closed:
                    self.root.after(interval_ms, _tick)

        self.root.after(interval_ms, _tick)

    def refresh_state(
        self,
        online_users: Dict[str, dict],
        active_peer: Optional[str],
        chat_history: List[str],
        unread_counts: Dict[str, int],
        my_status: str,
    ) -> None:
        """
        Update sidebar peers, unread badges, active chat header, and messages.
        Must be called on the Tkinter main thread (e.g. via schedule_poll).
        """
        if self._closed:
            return

        self._active_peer = active_peer

        # Update own status indicator if changed
        normalized_status = (my_status or "online").lower()
        if normalized_status != self.my_status:
            self._update_status_buttons(normalized_status)

        # Build sorted peer list (online users + any peer with unread or active chat)
        sorted_names = sorted(online_users.keys(), key=lambda n: n.lower())
        peer_snapshot = tuple(
            (
                name,
                online_users[name].get("ip", ""),
                online_users[name].get("port", 0),
                online_users[name].get("status", "online").lower(),
                unread_counts.get(name, 0),
                name == active_peer,
            )
            for name in sorted_names
        )

        if peer_snapshot != self._last_peer_snapshot:
            self._last_peer_snapshot = peer_snapshot
            self._render_peer_list(sorted_names, online_users, unread_counts, active_peer)

        # Update active chat header & input availability
        self._update_chat_header(active_peer, online_users)

        # Update chat history only when peer or messages changed
        chat_snapshot = (active_peer, tuple(chat_history))
        if chat_snapshot != self._last_chat_snapshot:
            self._last_chat_snapshot = chat_snapshot
            self._render_chat_history(active_peer, chat_history)

    def show_error(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self.root)

    def show_info(self, title: str, message: str) -> None:
        messagebox.showinfo(title, message, parent=self.root)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        # Root grid: left sidebar (col 0) + right chat area (col 1)
        self.root.grid_columnconfigure(0, weight=0, minsize=260)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # ================= LEFT SIDEBAR =================
        sidebar = tk.Frame(self.root, bg=COLORS["bg_panel"], width=260)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        # User profile card
        profile_frame = tk.Frame(sidebar, bg=COLORS["bg_panel"], padx=14, pady=14)
        profile_frame.pack(fill="x")

        tk.Label(
            profile_frame,
            text=self.my_name,
            font=("Segoe UI", 13, "bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_panel"],
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            profile_frame,
            text=f"TCP Port: {self.my_port}",
            font=("Segoe UI", 9),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_panel"],
            anchor="w",
        ).pack(fill="x", pady=(1, 10))

        # Status toggle buttons
        status_row = tk.Frame(profile_frame, bg=COLORS["bg_panel"])
        status_row.pack(fill="x")

        self.online_btn = tk.Button(
            status_row,
            text="● Online",
            command=lambda: self._trigger_status_change("online"),
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
        )
        self.online_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        self.away_btn = tk.Button(
            status_row,
            text="● Away",
            command=lambda: self._trigger_status_change("away"),
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
        )
        self.away_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

        # Divider
        tk.Frame(sidebar, bg=COLORS["border"], height=1).pack(fill="x")

        # Online peers section header
        self.peers_header_label = tk.Label(
            sidebar,
            text="ONLINE PEERS (0)",
            font=("Segoe UI", 8, "bold"),
            fg=COLORS["text_muted"],
            bg=COLORS["bg_panel"],
            anchor="w",
            padx=14,
            pady=10,
        )
        self.peers_header_label.pack(fill="x")

        # Peer listbox + scrollbar
        list_container = tk.Frame(sidebar, bg=COLORS["bg_panel"], padx=8)
        list_container.pack(fill="both", expand=True, pady=(0, 10))

        self.peer_listbox = tk.Listbox(
            list_container,
            font=("Segoe UI", 10),
            bg=COLORS["bg_panel"],
            fg=COLORS["text_primary"],
            selectbackground=COLORS["bg_selected"],
            selectforeground=COLORS["text_primary"],
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
        )
        peer_scroll = tk.Scrollbar(list_container, orient="vertical", command=self.peer_listbox.yview)
        self.peer_listbox.configure(yscrollcommand=peer_scroll.set)

        self.peer_listbox.pack(side="left", fill="both", expand=True)
        peer_scroll.pack(side="right", fill="y")
        self.peer_listbox.bind("<<ListboxSelect>>", self._handle_peer_click)

        # ================= RIGHT CHAT AREA =================
        right_panel = tk.Frame(self.root, bg=COLORS["bg_dark"])
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        # Chat header bar
        header = tk.Frame(right_panel, bg=COLORS["bg_panel"], padx=16, pady=12)
        header.grid(row=0, column=0, sticky="ew")

        header_left = tk.Frame(header, bg=COLORS["bg_panel"])
        header_left.pack(side="left", fill="x", expand=True)

        self.chat_title_label = tk.Label(
            header_left,
            text="No peer selected",
            font=("Segoe UI", 12, "bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_panel"],
            anchor="w",
        )
        self.chat_title_label.pack(fill="x")

        self.chat_subtitle_label = tk.Label(
            header_left,
            text="Select an online peer from the sidebar to start chatting",
            font=("Segoe UI", 9),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_panel"],
            anchor="w",
        )
        self.chat_subtitle_label.pack(fill="x")

        header_actions = tk.Frame(header, bg=COLORS["bg_panel"])
        header_actions.pack(side="right")

        self.send_file_btn = tk.Button(
            header_actions,
            text="Send File",
            command=self._handle_send_file_click,
            font=("Segoe UI", 9, "bold"),
            bg=COLORS["bg_hover"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["accent"],
            activeforeground=COLORS["text_primary"],
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=6,
            state="disabled",
        )
        self.send_file_btn.pack(side="left", padx=(0, 8))

        self.downloads_btn = tk.Button(
            header_actions,
            text="Downloads",
            command=self._open_downloads_folder,
            font=("Segoe UI", 9),
            bg=COLORS["bg_hover"],
            fg=COLORS["text_secondary"],
            activebackground=COLORS["border"],
            activeforeground=COLORS["text_primary"],
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=6,
        )
        self.downloads_btn.pack(side="left")

        # Chat history area
        chat_frame = tk.Frame(right_panel, bg=COLORS["bg_dark"], padx=14, pady=10)
        chat_frame.grid(row=1, column=0, sticky="nsew")
        chat_frame.grid_rowconfigure(0, weight=1)
        chat_frame.grid_columnconfigure(0, weight=1)

        self.chat_text = tk.Text(
            chat_frame,
            wrap="word",
            font=("Segoe UI", 10),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_primary"],
            borderwidth=0,
            highlightthickness=0,
            state="disabled",
            padx=6,
            pady=6,
        )
        chat_scroll = tk.Scrollbar(chat_frame, orient="vertical", command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=chat_scroll.set)

        self.chat_text.grid(row=0, column=0, sticky="nsew")
        chat_scroll.grid(row=0, column=1, sticky="ns")

        # Text tags for chat messages
        self.chat_text.tag_configure("me_label", foreground=COLORS["me"], font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_configure("peer_label", foreground=COLORS["peer"], font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_configure("file_msg", foreground=COLORS["file"], font=("Segoe UI", 10, "italic"))
        self.chat_text.tag_configure("body", foreground=COLORS["text_primary"], font=("Segoe UI", 10))
        self.chat_text.tag_configure("empty", foreground=COLORS["text_muted"], font=("Segoe UI", 10, "italic"))

        # Live file transfer progress banner
        self.progress_label = tk.Label(
            right_panel,
            text="",
            font=("Consolas", 9),
            fg=COLORS["file"],
            bg=COLORS["bg_dark"],
            anchor="w",
            padx=16,
        )
        self.progress_label.grid(row=2, column=0, sticky="ew")

        # Message input bar
        input_bar = tk.Frame(right_panel, bg=COLORS["bg_panel"], padx=14, pady=10)
        input_bar.grid(row=3, column=0, sticky="ew")
        input_bar.grid_columnconfigure(0, weight=1)

        self.msg_var = tk.StringVar()
        self.msg_entry = tk.Entry(
            input_bar,
            textvariable=self.msg_var,
            font=("Segoe UI", 10),
            bg=COLORS["bg_input"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            disabledbackground=COLORS["bg_input"],
            disabledforeground=COLORS["text_muted"],
            relief="flat",
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            state="disabled",
        )
        self.msg_entry.grid(row=0, column=0, sticky="ew", ipady=7, padx=(0, 10))
        self.msg_entry.bind("<Return>", self._handle_send_message_click)

        self.send_btn = tk.Button(
            input_bar,
            text="Send",
            command=self._handle_send_message_click,
            font=("Segoe UI", 10, "bold"),
            bg=COLORS["accent"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["accent_hover"],
            activeforeground=COLORS["text_primary"],
            relief="flat",
            cursor="hand2",
            padx=18,
            pady=5,
            state="disabled",
        )
        self.send_btn.grid(row=0, column=1)

        # Compact system activity log at the bottom
        log_frame = tk.Frame(right_panel, bg=COLORS["bg_input"], padx=12, pady=6)
        log_frame.grid(row=4, column=0, sticky="ew")
        log_frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            log_frame,
            text="ACTIVITY LOG",
            font=("Segoe UI", 7, "bold"),
            fg=COLORS["text_muted"],
            bg=COLORS["bg_input"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        self.log_text = tk.Text(
            log_frame,
            height=4,
            wrap="word",
            font=("Consolas", 8),
            bg=COLORS["bg_input"],
            fg=COLORS["text_secondary"],
            borderwidth=0,
            highlightthickness=0,
            state="disabled",
        )
        log_scroll = tk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=1, column=0, sticky="ew")
        log_scroll.grid(row=1, column=1, sticky="ns")

        self.log_text.tag_configure("log_ok", foreground=COLORS["online"])
        self.log_text.tag_configure("log_err", foreground=COLORS["error"])
        self.log_text.tag_configure("log_info", foreground=COLORS["text_secondary"])

    # ------------------------------------------------------------------
    # Internal render helpers
    # ------------------------------------------------------------------

    def _update_status_buttons(self, status: str) -> None:
        self.my_status = status
        if status == "online":
            self.online_btn.config(bg=COLORS["online"], fg=COLORS["bg_dark"])
            self.away_btn.config(bg=COLORS["bg_hover"], fg=COLORS["text_secondary"])
        else:
            self.online_btn.config(bg=COLORS["bg_hover"], fg=COLORS["text_secondary"])
            self.away_btn.config(bg=COLORS["away"], fg=COLORS["bg_dark"])

    def _render_peer_list(
        self,
        sorted_names: List[str],
        online_users: Dict[str, dict],
        unread_counts: Dict[str, int],
        active_peer: Optional[str],
    ) -> None:
        self.peers_header_label.config(text=f"ONLINE PEERS ({len(sorted_names)})")
        self._peer_order = sorted_names

        self.peer_listbox.delete(0, tk.END)
        for idx, name in enumerate(sorted_names):
            info = online_users[name]
            status = info.get("status", "online").lower()
            unread = unread_counts.get(name, 0)
            dot = "●" if status == "online" else "◐"
            badge = f"  ({unread} new)" if unread > 0 else ""
            label = f" {dot}  {name}  [{status}]{badge}"
            self.peer_listbox.insert(tk.END, label)

            if status == "away":
                self.peer_listbox.itemconfig(idx, fg=COLORS["away"])
            else:
                self.peer_listbox.itemconfig(idx, fg=COLORS["text_primary"])

            if name == active_peer:
                self.peer_listbox.selection_set(idx)

    def _update_chat_header(self, active_peer: Optional[str], online_users: Dict[str, dict]) -> None:
        if not active_peer:
            self.chat_title_label.config(text="No peer selected")
            self.chat_subtitle_label.config(text="Select an online peer from the sidebar to start chatting")
            self.msg_entry.config(state="disabled")
            self.send_btn.config(state="disabled")
            self.send_file_btn.config(state="disabled")
            return

        self.chat_title_label.config(text=active_peer)
        peer_info = online_users.get(active_peer)
        if peer_info:
            status = peer_info.get("status", "online").lower()
            self.chat_subtitle_label.config(
                text=f"{peer_info['ip']}:{peer_info['port']}  •  {status}",
                fg=COLORS["online"] if status == "online" else COLORS["away"],
            )
            self.msg_entry.config(state="normal")
            self.send_btn.config(state="normal")
            self.send_file_btn.config(state="normal")
        else:
            self.chat_subtitle_label.config(
                text="Offline (peer is not currently broadcasting)",
                fg=COLORS["error"],
            )
            self.msg_entry.config(state="disabled")
            self.send_btn.config(state="disabled")
            self.send_file_btn.config(state="disabled")

    def _render_chat_history(self, active_peer: Optional[str], chat_history: List[str]) -> None:
        self.chat_text.config(state="normal")
        self.chat_text.delete("1.0", tk.END)

        if not active_peer:
            self.chat_text.insert(tk.END, "Pick someone from the Online Peers list to begin chatting.\n", "empty")
        elif not chat_history:
            self.chat_text.insert(tk.END, f"No messages with {active_peer} yet. Say hello!\n", "empty")
        else:
            for line in chat_history:
                if line.startswith("[Me]: "):
                    body = line[len("[Me]: "):]
                    self.chat_text.insert(tk.END, "Me: ", "me_label")
                    tag = "file_msg" if body.startswith("[FILE]") else "body"
                    self.chat_text.insert(tk.END, body + "\n\n", tag)
                elif line.startswith("[") and "]: " in line:
                    prefix, body = line.split("]: ", 1)
                    sender = prefix[1:]
                    self.chat_text.insert(tk.END, f"{sender}: ", "peer_label")
                    tag = "file_msg" if body.startswith("[FILE]") else "body"
                    self.chat_text.insert(tk.END, body + "\n\n", tag)
                else:
                    self.chat_text.insert(tk.END, line + "\n\n", "body")

        self.chat_text.see(tk.END)
        self.chat_text.config(state="disabled")

    # ------------------------------------------------------------------
    # Event queue processing (background threads -> UI thread)
    # ------------------------------------------------------------------

    def _drain_event_queue(self) -> None:
        if self._closed:
            return

        while True:
            try:
                raw_msg = self._event_queue.get_nowait()
            except queue.Empty:
                break

            is_progress = raw_msg.startswith("\r")
            cleaned = strip_ansi(raw_msg).strip()
            if not cleaned:
                continue

            if is_progress:
                self._show_progress(cleaned, auto_clear=False)
            else:
                if cleaned.startswith("[OK]") or cleaned.startswith("[ERROR]"):
                    self._show_progress(cleaned, auto_clear=True)
                self._append_activity_log(cleaned)

        self.root.after(100, self._drain_event_queue)

    def _show_progress(self, text: str, auto_clear: bool) -> None:
        if self._progress_clear_job is not None:
            try:
                self.root.after_cancel(self._progress_clear_job)
            except tk.TclError:
                pass
            self._progress_clear_job = None

        color = COLORS["file"]
        if text.startswith("[OK]"):
            color = COLORS["online"]
        elif text.startswith("[ERROR]"):
            color = COLORS["error"]

        self.progress_label.config(text=text, fg=color)

        if auto_clear:
            self._progress_clear_job = self.root.after(
                5000, lambda: self.progress_label.config(text="")
            )

    def _append_activity_log(self, text: str) -> None:
        tag = "log_info"
        if text.startswith("[OK]") or text.startswith("[+]"):
            tag = "log_ok"
        elif text.startswith("[ERROR]") or "Error" in text or text.startswith("[-]"):
            tag = "log_err"

        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ------------------------------------------------------------------
    # Widget event handlers
    # ------------------------------------------------------------------

    def _handle_peer_click(self, _event=None) -> None:
        selection = self.peer_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if 0 <= idx < len(self._peer_order):
            peer_name = self._peer_order[idx]
            if self._on_select_peer:
                self._on_select_peer(peer_name)
            self.msg_entry.focus_set()

    def _handle_send_message_click(self, _event=None) -> None:
        if not self._active_peer:
            return
        text = self.msg_var.get().strip()
        if not text:
            return
        self.msg_var.set("")
        if self._on_send_message:
            self._on_send_message(self._active_peer, text)

    def _handle_send_file_click(self) -> None:
        if not self._active_peer:
            return
        filepath = filedialog.askopenfilename(
            title=f"Send file to {self._active_peer}",
            parent=self.root,
        )
        if filepath and self._on_send_file:
            self._on_send_file(self._active_peer, filepath)

    def _trigger_status_change(self, new_status: str) -> None:
        if new_status == self.my_status:
            return
        self._update_status_buttons(new_status)
        if self._on_status_change:
            self._on_status_change(new_status)

    def _open_downloads_folder(self) -> None:
        downloads_dir = os.path.abspath("downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(downloads_dir)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", downloads_dir])
            else:
                subprocess.Popen(["xdg-open", downloads_dir])
        except OSError as error:
            self.log_event(f"[ERROR] Could not open downloads folder: {error}")

    def _handle_window_close(self) -> None:
        if self._on_close:
            self._on_close()
        else:
            self.close()
