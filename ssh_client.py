#!/usr/bin/env python3
"""
Simple threaded chat client with crude /ssh support
"""

import sys
import socket
import threading
import signal

HOST = "127.0.0.1"
PORT = 6667

sock = None

def receive():
    while True:
        try:
            data = sock.recv(4096).decode('utf-8', errors='replace')
            if not data:
                print("\nDisconnected from server.")
                break
            print(data, end='', flush=True)
        except:
            print("\nReceive error.")
            break

def main():
    global sock

    if len(sys.argv) == 3:
        host = sys.argv[1]
        port = int(sys.argv[2])
    else:
        host = HOST
        port = PORT

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        print(f"Connected to {host}:{port}")
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # Start receive thread
    threading.Thread(target=receive, daemon=True).start()

    print("Type messages directly. Commands start with /")
    print("Special: /ssh @nick    /sshyes <port>")

    def shutdown(sig=None, frame=None):
        print("\nClosing...")
        if sock:
            sock.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            try:
                msg = input().rstrip()
                if not msg:
                    continue
                sock.send((msg + "\r\n").encode())
                if msg.strip() == "/quit":
                    break
            except EOFError:
                break
            except KeyboardInterrupt:
                break
    finally:
        shutdown()

if __name__ == "__main__":
    main()
