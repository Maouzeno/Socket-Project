
from peer_discovery import PeerDiscovery

username = input("Enter your name: ")

discovery = PeerDiscovery(username, tcp_port=5000)

discovery.start()

try:
    while True:

        command = input(
            "\nCommands: list / online / away / exit\n> "
        ).strip().lower()

        if command == "list":
            discovery.show_peers()

        elif command == "online":
            discovery.set_status("online")

        elif command == "away":
            discovery.set_status("away")

        elif command == "exit":
            break

        else:
            print("Unknown command!")

finally:
    discovery.stop()
