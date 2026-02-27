#!/usr/bin/env python3
"""
Very simple chat server + crude /ssh @nick feature using netcat
"""

import asyncio
import sqlite3
import datetime
import secrets
import socket
from typing import Dict, Optional

HOST = '127.0.0.1'
PORT = 6667
DB_PATH = "simple_chat.db"

MOTD = [
    "==============================",
    "  Welcome to Simple Chat     ",
    "  /help          → commands  ",
    "  /color on/off  → ansi      ",
    "  /ssh @nick     → crude rsh ",
    "=============================="
]

class User:
    def __init__(self, writer: asyncio.StreamWriter, nick: str = "Guest"):
        self.writer = writer
        self.nick = nick
        self.colors_enabled = False

class ChatServer:
    def __init__(self):
        self.users: Dict[str, User] = {}  # lower_nick → User

    async def broadcast(self, message: str, skip_nick: Optional[str] = None):
        for nick_lower, u in list(self.users.items()):
            if skip_nick and nick_lower == skip_nick.lower():
                continue
            try:
                text = self._maybe_color(u, message)
                u.writer.write(f"{text}\r\n".encode())
                await u.writer.drain()
            except:
                pass

    async def send(self, nick: str, message: str):
        nick_lower = nick.lower()
        if nick_lower in self.users:
            try:
                u = self.users[nick_lower]
                text = self._maybe_color(u, message)
                u.writer.write(f"{text}\r\n".encode())
                await u.writer.drain()
            except:
                pass

    def _maybe_color(self, user: User, text: str) -> str:
        if not user.colors_enabled:
            return text
        # Very naive coloring
        return (
            text.replace("<", "\033[32m<")
                .replace(">", ">\033[0m")
                .replace("[", "\033[33m[")
                .replace("]", "]\033[0m")
                .replace("→", "\033[36m→\033[0m")
                .replace("←", "\033[35m←\033[0m")
        )

chat_server = ChatServer()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_nick TEXT NOT NULL,
            to_nick TEXT,
            message TEXT NOT NULL,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

async def handle_command(user: User, line: str):
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()[1:] if parts[0].startswith("/") else ""
    args = parts[1] if len(parts) > 1 else ""

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if cmd == "help":
        await send_lines(user.writer, [
            "/nick <name>",
            "/msg <nick> <text>   or   /dm ...",
            "/history [n]          or   /history dm",
            "/color on / off",
            "/ssh @nick            → ask someone to give you shell",
            "/sshyes <port>        → accept incoming shell request",
            "/quit"
        ])

    elif cmd == "nick":
        if not args:
            await send_msg(user.writer, "Usage: /nick NewName")
            return
        newnick = args.split()[0][:24]
        new_lower = newnick.lower()
        if new_lower in chat_server.users and new_lower != user.nick.lower():
            await send_msg(user.writer, "Nick in use")
            return
        old = user.nick
        chat_server.users.pop(user.nick.lower(), None)
        user.nick = newnick
        chat_server.users[new_lower] = user
        await chat_server.broadcast(f"* {old} → {newnick}")
        await send_msg(user.writer, f"Now known as {newnick}")

    elif cmd in ("msg", "dm"):
        if " " not in args:
            await send_msg(user.writer, "Usage: /msg nick message")
            return
        target, msg = args.split(" ", 1)
        tlow = target.lower()
        if tlow not in chat_server.users:
            await send_msg(user.writer, f"{target} not online")
            return
        ts = datetime.datetime.now().strftime("%H:%M")
        await send_msg(user.writer,    f"[{ts}] → {target} {msg}")
        await chat_server.send(target, f"[{ts}] ← {user.nick} {msg}")
        c.execute("INSERT INTO messages (from_nick, to_nick, message) VALUES (?,?,?)",
                  (user.nick, target, msg))
        conn.commit()

    elif cmd == "history":
        limit = 10
        if args.isdigit():
            limit = min(int(args), 300)
        elif args.strip().lower().startswith("dm"):
            limit = 10
            try:
                limit = min(int(args.split()[1]), 300)
            except:
                pass

        if "dm" in args.lower():
            c.execute("""
                SELECT from_nick, to_nick, message, ts
                FROM messages WHERE from_nick = ? OR to_nick = ?
                ORDER BY id DESC LIMIT ?
            """, (user.nick, user.nick, limit))
            rows = c.fetchall()[::-1]
            await send_msg(user.writer, f"Recent DMs ({len(rows)}):")
            for fr, to, m, ts in rows:
                t = ts[11:16]
                arr = "→" if fr == user.nick else "←"
                who = to if fr == user.nick else fr
                await send_msg(user.writer, f"[{t}] {arr} {who} {m}")
        else:
            c.execute("SELECT from_nick, message, ts FROM messages WHERE to_nick IS NULL ORDER BY id DESC LIMIT ?", (limit,))
            rows = c.fetchall()[::-1]
            await send_msg(user.writer, f"Recent chat ({len(rows)}):")
            for nick, m, ts in rows:
                t = ts[11:16]
                await send_msg(user.writer, f"[{t}] <{nick}> {m}")

    elif cmd == "color":
        if args.lower() in ("on", "yes", "true"):
            user.colors_enabled = True
            await send_msg(user.writer, "Colors → ON")
        elif args.lower() in ("off", "no", "false"):
            user.colors_enabled = False
            await send_msg(user.writer, "Colors → OFF")
        else:
            await send_msg(user.writer, f"Colors currently {'ON' if user.colors_enabled else 'OFF'}")

    elif cmd == "ssh":
        if not args.startswith("@"):
            await send_msg(user.writer, "Usage: /ssh @nickname")
            return
        target_nick = args[1:].split()[0].strip()
        tlow = target_nick.lower()
        if tlow == user.nick.lower():
            await send_msg(user.writer, "Cannot ssh yourself")
            return
        if tlow not in chat_server.users:
            await send_msg(user.writer, f"@{target_nick} not online")
            return

        target = chat_server.users[tlow]

        # Pick random high port
        port = secrets.randbelow(10000) + 50000

        await send_msg(target.writer,
            f"!!! SHELL REQUEST from @{user.nick} !!!\n"
            f"Reply with   /sshyes {port}   to ACCEPT (dangerous!)\n"
            f"Ignore or close window to refuse."
        )
        await send_msg(user.writer,
            f"Shell request sent to @{target_nick} — waiting for /sshyes ...")

    elif cmd == "sshyes":
        if not args.isdigit():
            await send_msg(user.writer, "Usage: /sshyes PORT   (only use the port you were given)")
            return
        port = int(args)
        if not (50000 <= port <= 59999):
            await send_msg(user.writer, "Port out of allowed range")
            return

        # Try to guess our public IP (very unreliable on NAT)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 53))
            my_ip = s.getsockname()[0]
            s.close()
        except:
            my_ip = "127.0.0.1"

        await chat_server.broadcast(
            f"*{user.nick} ACCEPTED shell request!\n"
            f"Connect with:\n"
            f"   nc {my_ip} {port}\n"
            f"(requester should run this now)",
            skip_nick=user.nick
        )

        await send_msg(user.writer,
            f"Run this in a NEW terminal to listen for shell:\n"
            f"   nc -l -p {port} -e /bin/sh     (Linux/macOS)\n"
            f"or\n"
            f"   nc -l -p {port} -e cmd.exe     (Windows)\n"
            f"Waiting for connection..."
        )

    conn.close()

async def send_msg(w, text: str):
    try:
        w.write(f":{text}\r\n".encode())
        await w.drain()
    except:
        pass

async def send_lines(w, lines):
    for line in lines:
        await send_msg(w, line)

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    user = User(writer)
    chat_server.users[user.nick.lower()] = user

    addr = writer.get_extra_info('peername')
    print(f"Connected: {addr} → {user.nick}")

    await send_lines(writer, [f":server 001 {user.nick} :Welcome!"] + MOTD)
    await chat_server.broadcast(f"* {user.nick} joined")

    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
            for line in data.decode(errors='ignore').splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("/"):
                    await handle_command(user, line)
                    continue
                # public message
                if len(line) > 400:
                    await send_msg(writer, "Message too long")
                    continue
                ts = datetime.datetime.now().strftime("%H:%M")
                msg = f"[{ts}] <{user.nick}> {line}"
                await chat_server.broadcast(msg, skip_nick=user.nick)

                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT INTO messages (from_nick, message) VALUES (?,?)",
                          (user.nick, line))
                conn.commit()
                conn.close()

    except Exception as e:
        print(f"Error {addr}: {e}")
    finally:
        nick_lower = user.nick.lower()
        if nick_lower in chat_server.users:
            del chat_server.users[nick_lower]
        await chat_server.broadcast(f"* {user.nick} left")
        writer.close()
        await writer.wait_closed()
        print(f"Disconnected: {addr}")

async def main():
    init_db()
    server = await asyncio.start_server(handle_client, HOST, PORT)
    print(f"Listening on {server.sockets[0].getsockname()}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")