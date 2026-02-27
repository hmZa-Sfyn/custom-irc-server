#!/usr/bin/env python3
"""
Very simple colorful IRC-like chat server
One global channel + direct messages
Colors can be toggled per user
"""

import asyncio
import sqlite3
import datetime
from typing import Dict, Optional

# ==================== CONFIG ====================
HOST = '0.0.0.0'
PORT = 6667
DB_PATH = "simple_chat.db"

MOTD = [
    "==============================",
    "  Welcome to Simple Chat     ",
    "  /help    → commands        ",
    "  /color on/off → toggle ansi",
    "=============================="
]

# ANSI colors
COLORS = {
    "reset": "\033[0m",
    "red":   "\033[31m",
    "green": "\033[32m",
    "yellow":"\033[33m",
    "blue":  "\033[34m",
    "purple":"\033[35m",
    "cyan":  "\033[36m",
    "white": "\033[97m",
}

# ==================== DATABASE ====================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_nick TEXT NOT NULL,
            to_nick TEXT,                   -- NULL = public chat
            message TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# ==================== STATE ====================
class User:
    def __init__(self, writer: asyncio.StreamWriter, nick: str = "Guest"):
        self.writer = writer
        self.nick = nick
        self.colors_enabled = False

class ChatServer:
    def __init__(self):
        self.users: Dict[str, User] = {}  # lower_nick → User

    async def broadcast(self, message: str, skip_nick: Optional[str] = None):
        for nick_lower, user in list(self.users.items()):
            if skip_nick and nick_lower == skip_nick.lower():
                continue
            try:
                colored = self.apply_color(user, message)
                user.writer.write(f"{colored}\r\n".encode())
                await user.writer.drain()
            except:
                pass

    async def send_to(self, nick: str, message: str):
        nick_lower = nick.lower()
        if nick_lower in self.users:
            try:
                user = self.users[nick_lower]
                colored = self.apply_color(user, message)
                user.writer.write(f"{colored}\r\n".encode())
                await user.writer.drain()
            except:
                pass

    def apply_color(self, user: User, text: str) -> str:
        if not user.colors_enabled:
            return text
        # Very simple coloring
        return text.replace("<", f"{COLORS['green']}<").replace(">", f">{COLORS['reset']}") \
                   .replace("[", f"{COLORS['yellow']}[") .replace("]", f"]{COLORS['reset']}") \
                   .replace("→", f"{COLORS['cyan']}→{COLORS['reset']}") \
                   .replace("←", f"{COLORS['purple']}←{COLORS['reset']}")

chat_server = ChatServer()


# ==================== COMMANDS ====================
async def handle_command(user: User, line: str):
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()[1:] if parts[0].startswith("/") else ""
    args = parts[1] if len(parts) > 1 else ""

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    if cmd == "help":
        await send_lines(user.writer, [
            "/nick <name>          → change nickname",
            "/msg <nick> <text>    → send private message",
            "/dm <nick> <text>     → same as /msg",
            "/history [n]          → show last n messages (default 10)",
            "/color on / off       → toggle colored output",
            "/quit                 → disconnect",
        ])

    elif cmd == "nick":
        if not args:
            await send_msg(user.writer, "Usage: /nick NewName")
            return
        newnick = args.split()[0][:24]
        if not newnick.isalnum() and "_" not in newnick and "-" not in newnick:
            await send_msg(user.writer, "Nick can contain letters, numbers, _, -")
            return
        new_lower = newnick.lower()
        if new_lower in chat_server.users and new_lower != user.nick.lower():
            await send_msg(user.writer, "Nickname already in use")
            return

        old = user.nick
        chat_server.users.pop(user.nick.lower(), None)
        user.nick = newnick
        chat_server.users[new_lower] = user

        await chat_server.broadcast(f"* {old} is now known as {newnick}")
        await send_msg(user.writer, f"You are now {newnick}")

    elif cmd in ("msg", "dm"):
        if not args or " " not in args:
            await send_msg(user.writer, "Usage: /msg nickname message here")
            return
        target, msg = args.split(" ", 1)
        target_lower = target.lower()

        if target_lower == user.nick.lower():
            await send_msg(user.writer, "Can't message yourself")
            return
        if target_lower not in chat_server.users:
            await send_msg(user.writer, f"{target} is not online")
            return

        ts = datetime.datetime.now().strftime("%H:%M")
        await send_msg(user.writer,    f"[{ts}] → {target} {msg}")
        await chat_server.send_to(target, f"[{ts}] ← {user.nick} {msg}")

        c.execute("INSERT INTO messages (from_nick, to_nick, message) VALUES (?,?,?)",
                  (user.nick, target, msg))
        conn.commit()

    elif cmd == "history":
        try:
            limit = min(int(args) if args.strip().isdigit() else 10, 300)
        except:
            limit = 10

        if " " in args and args.split()[1].lower() == "dm":
            # last DMs involving me
            c.execute("""
                SELECT from_nick, to_nick, message, timestamp
                FROM messages
                WHERE from_nick = ? OR to_nick = ?
                ORDER BY id DESC LIMIT ?
            """, (user.nick, user.nick, limit))
            rows = c.fetchall()[::-1]
            await send_msg(user.writer, f"Recent private messages ({len(rows)}):")
            for fr, to, msg, ts in rows:
                t = ts[11:16]
                arrow = "→" if fr == user.nick else "←"
                who = to if fr == user.nick else fr
                await send_msg(user.writer, f"[{t}] {arrow} {who} {msg}")
        else:
            # public chat
            c.execute("""
                SELECT from_nick, message, timestamp
                FROM messages
                WHERE to_nick IS NULL
                ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = c.fetchall()[::-1]
            await send_msg(user.writer, f"Recent public messages ({len(rows)}):")
            for nick, msg, ts in rows:
                t = ts[11:16]
                await send_msg(user.writer, f"[{t}] <{nick}> {msg}")

    elif cmd == "color":
        if args.lower() == "on":
            user.colors_enabled = True
            await send_msg(user.writer, "Colored output → enabled")
        elif args.lower() == "off":
            user.colors_enabled = False
            await send_msg(user.writer, "Colored output → disabled")
        else:
            await send_msg(user.writer, f"Current: {'on' if user.colors_enabled else 'off'}   Usage: /color on|off")

    conn.close()


# ==================== HELPERS ====================
async def send_msg(writer, text: str):
    try:
        writer.write(f":{text}\r\n".encode())
        await writer.drain()
    except:
        pass

async def send_lines(writer, lines):
    for line in lines:
        await send_msg(writer, line)


# ==================== CLIENT HANDLER ====================
async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    user = User(writer)
    chat_server.users[user.nick.lower()] = user

    addr = writer.get_extra_info('peername')
    print(f"Connected: {addr} → {user.nick}")

    await send_lines(writer, [f":server 001 {user.nick} :Welcome!"] + MOTD)

    # Welcome message in chat
    await chat_server.broadcast(f"* {user.nick} has joined the chat")

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

                # Normal message → public chat
                if len(line) > 400:
                    await send_msg(writer, "Message too long (max ~400 chars)")
                    continue

                ts = datetime.datetime.now().strftime("%H:%M")
                msg_line = f"[{ts}] <{user.nick}> {line}"

                await chat_server.broadcast(msg_line, skip_nick=user.nick)

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
        await chat_server.broadcast(f"* {user.nick} has left")
        writer.close()
        await writer.wait_closed()
        print(f"Disconnected: {addr}")


# ==================== MAIN ====================
async def main():
    init_db()
    server = await asyncio.start_server(handle_client, HOST, PORT)
    addr = server.sockets[0].getsockname()
    print(f"Simple chat running → {addr}")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped.")