#!/usr/bin/env python3
"""
Simple test client for the OpenMW bridge socket.
Connects to the bridge, sends a ping, and prints any received messages.

Usage: python test_connection.py [port]
Default port: 21003
"""

import socket
import json
import sys
import time


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 21003

    print(f"Connecting to 127.0.0.1:{port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)

    try:
        sock.connect(("127.0.0.1", port))
        print("Connected!")

        # Send a ping
        msg = json.dumps({"type": "ping", "id": "test_001"}) + "\n"
        sock.sendall(msg.encode("utf-8"))
        print(f"Sent: {msg.strip()}")

        # Read responses
        sock.settimeout(2.0)
        buffer = ""
        try:
            while True:
                data = sock.recv(4096).decode("utf-8")
                if not data:
                    print("Connection closed by server")
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        print(f"Received: {line}")
        except socket.timeout:
            print("No more data (timeout)")
    except ConnectionRefusedError:
        print(f"Connection refused. Is OpenMW running with the bridge mod on port {port}?")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sock.close()
        print("Disconnected.")


if __name__ == "__main__":
    main()
