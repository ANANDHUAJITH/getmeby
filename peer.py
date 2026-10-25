import socket
import re
import os
import json
import threading
import argparse
lock = threading.Lock()

CONTACTS_FILE = "/howsapp/contacts.json"
soc = None  # the listening socket, created in handle()

# ── GetMe ID encoding ──────────────────────────────────────────────────────────
# ip:port is packed into a single 48-bit integer:
#   bits 47-16  → IP as 32-bit big-endian
#   bits 15-0   → port as 16-bit
#
# That integer is encoded in base-62 (0-9, a-z, A-Z).
#
# Capacity check:
#   max 48-bit value  = 2^48 - 1  = 281_474_976_710_655
#   62^8              = 218_340_105_584_896
#   62^9              = 13_537_086_546_263_552   ← fits easily
#
# 8 base-62 chars hold 62^8 ≈ 2.18 × 10^14, but 2^48 ≈ 2.81 × 10^14, so
# we need 9 chars to be safe for every possible IP+port.
# We use 9 chars: zero-padded, always exactly 9 alphanumeric characters.

_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_B62_LEN = 62
_GETME_WIDTH = 9          # 62^9 > 2^48  ✓


def _to_base62(n: int, width: int) -> str:
    """Encode non-negative integer n in base-62, zero-padded to `width` chars."""
    chars = []
    for _ in range(width):
        chars.append(_B62[n % _B62_LEN])
        n //= _B62_LEN
    if n != 0:
        raise OverflowError(f"Value too large for {width}-char base-62 string")
    return "".join(reversed(chars))


def _from_base62(s: str) -> int:
    """Decode a base-62 string back to an integer."""
    result = 0
    for ch in s:
        result = result * _B62_LEN + _B62.index(ch)
    return result


def encode_getme_id(ip: str, port: int) -> str:
    """Encode ip + port → 9-character case-sensitive alphanumeric GetMe ID."""
    octets = list(map(int, ip.split(".")))
    if len(octets) != 4 or not all(0 <= o <= 255 for o in octets):
        raise ValueError(f"Invalid IP: {ip}")
    if not (0 <= port <= 65535):
        raise ValueError(f"Invalid port: {port}")
    ip_int = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
    combined = (ip_int << 16) | port          # 48-bit integer
    return _to_base62(combined, _GETME_WIDTH)


def decode_getme_id(getme_id: str) -> tuple[str, int]:
    """Decode a 9-char GetMe ID back to (ip, port)."""
    if len(getme_id) != _GETME_WIDTH:
        raise ValueError(f"GetMe ID must be exactly {_GETME_WIDTH} characters")
    combined = _from_base62(getme_id)
    port = combined & 0xFFFF
    ip_int = (combined >> 16) & 0xFFFFFFFF
    ip = ".".join([
        str((ip_int >> 24) & 0xFF),
        str((ip_int >> 16) & 0xFF),
        str((ip_int >> 8)  & 0xFF),
        str(ip_int         & 0xFF),
    ])
    return ip, port


# ── Contact storage ────────────────────────────────────────────────────────────

def load_contacts():
    with lock:
        os.makedirs("/howsapp", exist_ok=True)
        if os.path.exists(CONTACTS_FILE):
            with open(CONTACTS_FILE, "r") as f:
                return json.load(f)
        return {}


def save_contacts():
    with lock:
        os.makedirs("/howsapp", exist_ok=True)
        with open(CONTACTS_FILE, "w") as f:
            json.dump(contacts, f, indent=4)


def storage_handler(msg):
    with lock:
        os.makedirs("/howsapp", exist_ok=True)
        file = "/howsapp/chat.json"
        if os.path.exists(file):
            with open(file, "r") as f:
                history = json.load(f)
        else:
            history = []
        history.append(msg)
        with open(file, "w") as f:
            json.dump(history, f, indent=4)


# ── Receiver ───────────────────────────────────────────────────────────────────

def reciver():
    conn, addr = soc.accept()
    with conn:
        msg_recived_total = conn.recv(2048).decode()
        if msg_recived_total:
            msg_split = re.findall(r'(\w+)="([^"]*)"', msg_recived_total)
            msg = dict(msg_split)
            grp = msg.get("grp", "None")

            # Print received message on its own line, then re-show the prompt
            display = f'\n{msg["user"]} : {msg["msg"]}' + (f"  [{grp}]" if grp != "None" else "")
            print(display)
            print("type.....: ", end="", flush=True)   # re-show prompt

            storage_handler(msg)


def handle(ip, port):
    global soc
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            soc = s
            soc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            soc.bind((ip, port))
            soc.listen()
            while True:
                reciver()
    except OSError as e:
        print(f"server error: {e}")


# ── Sender ─────────────────────────────────────────────────────────────────────

def send_msg(snd_msg, conn):
    conn.sendall(snd_msg.encode())


def send_to(addr, payload):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.settimeout(5)
            c.connect((addr["ip"], addr["port"]))
            c.settimeout(None)
            send_msg(payload, c)
            return True
    except (OSError, socket.timeout) as e:
        print(f'\n  ✗ could not reach {addr["ip"]}:{addr["port"]} - {e}')
        print("type.....: ", end="", flush=True)
        return False


# ── Contact helpers ────────────────────────────────────────────────────────────

def add_contact_by_getme(name: str):
    """
    Ask the user for a GetMe ID (or fall back to raw ip/port),
    store the contact and return its address list.
    """
    raw = input(f'GetMe ID (8 chars) or IP for "{name}": ').strip()

    if len(raw) == _GETME_WIDTH and all(c in _B62 for c in raw):
        try:
            ip, port = decode_getme_id(raw)
            print(f'  → decoded: {ip}:{port}')
        except ValueError as e:
            print(f"  ✗ bad GetMe ID ({e}), falling back to manual entry")
            ip = raw  # treat as IP
            port = int(input(f'port for "{name}": '))
    else:
        ip = raw
        port = int(input(f'port for "{name}": '))

    contacts[name] = [{"ip": ip, "port": port}]
    save_contacts()
    return contacts[name]


# ── Management commands ────────────────────────────────────────────────────────
#
#  /add <name>             add or update a contact
#  /del <name>             delete one contact by name
#  /contacts               list all saved contacts
#  /delcontacts            delete the entire contacts file
#  /history                print chat history
#  /delhistory             delete chat history file
#  /help                   show this list
#
# Anything that does NOT start with "/" is treated as a message to send.

CMD_RE = re.compile(r'^/(\w+)(?:\s+(.+))?$')   # /command  optional_arg

HELP_TEXT = """
  commands:
    /add <name>        add or update a contact
    /del <name>        delete a contact
    /contacts          list all contacts
    /delcontacts       delete the contacts file
    /history           read chat history
    /delhistory        delete chat history
    /help              show this help
  anything else → send a message
"""


def cmd_add(name: str):
    if not name:
        print("  usage: /add <name>")
        return
    add_contact_by_getme(name.strip())
    print(f'  ✓ contact "{name.strip()}" saved')


def cmd_del(name: str):
    if not name:
        print("  usage: /del <name>")
        return
    name = name.strip()
    if name in contacts:
        del contacts[name]
        save_contacts()
        print(f'  ✓ contact "{name}" deleted')
    else:
        print(f'  ✗ no contact named "{name}"')


def cmd_list_contacts():
    if not contacts:
        print("  (no saved contacts)")
        return
    print("  saved contacts:")
    for name, addrs in contacts.items():
        for addr in addrs:
            gid = encode_getme_id(addr["ip"], addr["port"])
            print(f'    {name:20s}  {gid}  ({addr["ip"]}:{addr["port"]})')


def cmd_del_contacts():
    confirm = input("  delete entire contacts file? (yes/no): ").strip().lower()
    if confirm == "yes":
        with lock:
            if os.path.exists(CONTACTS_FILE):
                os.remove(CONTACTS_FILE)
                contacts.clear()
                print("  ✓ contacts file deleted")
            else:
                print("  (contacts file not found)")
    else:
        print("  cancelled")


def cmd_history():
    file = "/howsapp/chat.json"
    with lock:
        if not os.path.exists(file):
            print("  (no chat history)")
            return
        with open(file, "r") as f:
            history = json.load(f)
    if not history:
        print("  (chat history is empty)")
        return
    print(f"\n  ── chat history ({len(history)} messages) ──")
    for entry in history:
        grp = entry.get("grp", "None")
        line = f'  {entry["user"]} : {entry["msg"]}'
        if grp != "None":
            line += f'  [{grp}]'
        print(line)
    print("  ── end ──\n")


def cmd_del_history():
    file = "/howsapp/chat.json"
    confirm = input("  delete chat history? (yes/no): ").strip().lower()
    if confirm == "yes":
        with lock:
            if os.path.exists(file):
                os.remove(file)
                print("  ✓ chat history deleted")
            else:
                print("  (history file not found)")
    else:
        print("  cancelled")


def dispatch_command(cmd: str, arg: str | None):
    """Route a parsed /command to the right function."""
    cmd = cmd.lower()
    if cmd == "add":
        cmd_add(arg or "")
    elif cmd == "del":
        cmd_del(arg or "")
    elif cmd == "contacts":
        cmd_list_contacts()
    elif cmd == "delcontacts":
        cmd_del_contacts()
    elif cmd == "history":
        cmd_history()
    elif cmd == "delhistory":
        cmd_del_history()
    elif cmd == "help":
        print(HELP_TEXT)
    else:
        print(f'  ✗ unknown command "/{cmd}" — type /help')


# ── Keyboard handler ───────────────────────────────────────────────────────────

def keyboard_handler():
    print("  type /help for commands")
    while True:
        raw_input = input("type.....: ").strip()

        # ── command branch ──────────────────────────────────────────────────
        m = CMD_RE.match(raw_input)
        if m:
            dispatch_command(m.group(1), m.group(2))
            continue

        # ── message branch ──────────────────────────────────────────────────
        sndr_input_msg = raw_input
        if not sndr_input_msg:
            continue

        sndr_input_recipient = input("recipient (name or GetMe ID): ").strip()

        # Allow addressing directly by GetMe ID without a saved contact
        if (
            len(sndr_input_recipient) == _GETME_WIDTH
            and all(c in _B62 for c in sndr_input_recipient)
            and sndr_input_recipient not in contacts
        ):
            try:
                ip, port = decode_getme_id(sndr_input_recipient)
                addrs = [{"ip": ip, "port": port}]
                display_name = sndr_input_recipient
            except ValueError:
                addrs = None
                display_name = sndr_input_recipient
        else:
            addrs = contacts.get(sndr_input_recipient)
            display_name = sndr_input_recipient

        if addrs is None:
            addrs = add_contact_by_getme(sndr_input_recipient)

        grp_field = display_name if len(addrs) > 1 else "None"
        sndr_payload = f'user="{MY_USER_ID}" msg="{sndr_input_msg}" grp="{grp_field}"'
        any_failed = False
        for addr in addrs:
            ok = send_to(addr, sndr_payload)
            if not ok:
                any_failed = True

        # If a named contact failed, offer to update their address
        if any_failed and sndr_input_recipient in contacts:
            fix = input(f'  update saved address for "{sndr_input_recipient}"? (y/n): ').strip().lower()
            if fix == "y":
                add_contact_by_getme(sndr_input_recipient)
                print(f'  ✓ "{sndr_input_recipient}" updated. retry sending? (y/n): ', end="")
                retry = input().strip().lower()
                if retry == "y":
                    addrs = contacts[sndr_input_recipient]
                    for addr in addrs:
                        send_to(addr, sndr_payload)


# ── Entry point ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("-p", type=int, default=6497)
parser.add_argument("--getmeby", default="me")
args = parser.parse_args()

server_ip = "127.0.0.1"
server_port = args.p
MY_USER_ID = args.getmeby

MY_GETME_ID = encode_getme_id(server_ip, server_port)

# Print GetMe ID on startup
print("=" * 36)
print(f"  Your GetMe ID : {MY_GETME_ID}")
print(f"  ({server_ip}:{server_port})")
print("=" * 36)

contacts = load_contacts()

threading.Thread(target=keyboard_handler, daemon=True).start()
handle(server_ip, server_port)
