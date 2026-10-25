import socket
import re
import os
import json
import threading
import argparse
from datetime import datetime

lock = threading.Lock()

# All data files sit next to the script, inside a "howsapp_data/" subfolder.
_BASE_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "howsapp_data")
CONTACTS_FILE = os.path.join(_BASE_DIR, "contacts.json")
GROUPS_FILE   = os.path.join(_BASE_DIR, "groups.json")
CHAT_FILE     = os.path.join(_BASE_DIR, "chat.json")

soc = None  # the listening socket, created in handle()

# ── GetMe ID encoding ──────────────────────────────────────────────────────────
# ip:port packed into a 48-bit integer, base-62 encoded to exactly 9 chars.
# 62^9 = 13.5 × 10^15  >  2^48 = 2.8 × 10^14  ✓

_B62        = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_B62_LEN    = 62
_GETME_WIDTH = 9


def _to_base62(n: int, width: int) -> str:
    chars = []
    for _ in range(width):
        chars.append(_B62[n % _B62_LEN])
        n //= _B62_LEN
    if n != 0:
        raise OverflowError(f"Value too large for {width}-char base-62 string")
    return "".join(reversed(chars))


def _from_base62(s: str) -> int:
    result = 0
    for ch in s:
        result = result * _B62_LEN + _B62.index(ch)
    return result


def encode_getme_id(ip: str, port: int) -> str:
    octets = list(map(int, ip.split(".")))
    if len(octets) != 4 or not all(0 <= o <= 255 for o in octets):
        raise ValueError(f"Invalid IP: {ip}")
    if not (0 <= port <= 65535):
        raise ValueError(f"Invalid port: {port}")
    ip_int   = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
    combined = (ip_int << 16) | port
    return _to_base62(combined, _GETME_WIDTH)


def decode_getme_id(getme_id: str) -> tuple[str, int]:
    if len(getme_id) != _GETME_WIDTH:
        raise ValueError(f"GetMe ID must be exactly {_GETME_WIDTH} characters")
    combined = _from_base62(getme_id)
    port     = combined & 0xFFFF
    ip_int   = (combined >> 16) & 0xFFFFFFFF
    ip = ".".join([
        str((ip_int >> 24) & 0xFF),
        str((ip_int >> 16) & 0xFF),
        str((ip_int >>  8) & 0xFF),
        str( ip_int        & 0xFF),
    ])
    return ip, port


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Persistence helpers ────────────────────────────────────────────────────────

def _ensure_dir():
    os.makedirs(_BASE_DIR, exist_ok=True)


def load_contacts() -> dict:
    with lock:
        _ensure_dir()
        return _load_json_safe(CONTACTS_FILE, {})


def save_contacts():
    with lock:
        _ensure_dir()
        with open(CONTACTS_FILE, "w") as f:
            json.dump(contacts, f, indent=4)


def load_groups() -> dict:
    with lock:
        _ensure_dir()
        return _load_json_safe(GROUPS_FILE, {})


def save_groups():
    with lock:
        _ensure_dir()
        with open(GROUPS_FILE, "w") as f:
            json.dump(groups, f, indent=4)


def _load_json_safe(path: str, fallback):
    """Read a JSON file, returning fallback on missing, empty, or corrupt file."""
    if not os.path.exists(path):
        return fallback
    try:
        with open(path) as f:
            content = f.read().strip()
        if not content:
            return fallback
        return json.loads(content)
    except json.JSONDecodeError:
        # Corrupt file — back it up and start fresh
        bad = path + ".corrupt"
        os.replace(path, bad)
        print(f"\n  ⚠ {path} was corrupt — backed up to {bad}, starting fresh")
        return fallback


def storage_handler(msg: dict):
    """Append a message (already a dict) to chat history with a timestamp."""
    with lock:
        _ensure_dir()
        history = _load_json_safe(CHAT_FILE, [])
        msg.setdefault("time", now_ts())
        history.append(msg)
        with open(CHAT_FILE, "w") as f:
            json.dump(history, f, indent=4)


# ── Receiver ───────────────────────────────────────────────────────────────────

def reciver():
    conn, addr = soc.accept()
    with conn:
        raw = conn.recv(4096).decode()
        if not raw:
            return

        # Parse key="value" pairs
        msg = dict(re.findall(r'(\w+)="([^"]*)"', raw))
        if not msg:
            return

        kind = msg.get("kind", "msg")        # msg | sys
        grp  = msg.get("grp",  "None")
        ts   = msg.get("time", now_ts())
        user = msg.get("user", "?")
        text = msg.get("msg",  "")

        # Try to match the sender's ip:port to a saved contact name
        sender_ip, sender_port = addr   # addr is (ip, port) from soc.accept()
        saved_name = lookup_contact_name(sender_ip, sender_port)
        # Show as:  anandhu (mie) :  if saved_name differs from user
        if saved_name and saved_name != user:
            display_user = f"{user} ({saved_name})"
        else:
            display_user = user

        if kind == "creq":
            # Contact-list request — handle interactively, don't store in history
            handle_incoming_creq(user, addr)
            return

        if kind == "cack":
            # Peer accepted our request and sent their contacts
            handle_incoming_cack(user, text)
            return

        if kind == "sys":
            print(f"\n  [{ts}] *** {text}")
        else:
            prefix = f"[{grp}] " if grp != "None" else ""
            print(f"\n  [{ts}] {prefix}{display_user} : {text}")

        print("type.....: ", end="", flush=True)
        storage_handler({"kind": kind, "user": user, "msg": text,
                         "grp": grp, "time": ts})


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

def send_to(addr: dict, payload: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.settimeout(5)
            c.connect((addr["ip"], addr["port"]))
            c.settimeout(None)
            c.sendall(payload.encode())
            return True
    except (OSError, socket.timeout) as e:
        print(f'\n  ✗ could not reach {addr["ip"]}:{addr["port"]} - {e}')
        print("type.....: ", end="", flush=True)
        return False


def build_payload(user: str, text: str, grp: str = "None",
                  kind: str = "msg") -> str:
    ts = now_ts()
    return f'user="{user}" msg="{text}" grp="{grp}" kind="{kind}" time="{ts}"'


def broadcast(addrs: list[dict], payload: str) -> bool:
    """Send payload to every address. Returns True if all succeeded."""
    results = [send_to(a, payload) for a in addrs]
    return all(results)




_QE = "\u201c"   # " → ‹ open curly quote  (encode)
_QD = "\u201d"   # " → › close curly quote (decode)


def _encode_contacts_payload(c: dict) -> str:
    """Serialise contacts dict to a string safe to embed in msg="…"."""
    raw = json.dumps(c, separators=(",", ":"))
    return raw.replace('"', _QE)          # escape all double-quotes


def _decode_contacts_payload(s: str) -> dict:
    """Reverse _encode_contacts_payload back to a dict."""
    restored = s.replace(_QE, '"').replace(_QD, '"')
    return json.loads(restored)


def _merge_contacts(incoming: dict, from_user: str):

    added = []
    for name, addrs in incoming.items():
        if name in contacts:
            continue                      # don't overwrite existing
        if name == MY_USER_ID:
            continue                      # don't add ourselves
        contacts[name] = addrs
        added.append(name)
    if added:
        save_contacts()
        print(f"\n  ✓ contacts received from {from_user}: {', '.join(added)}")
    else:
        print(f"\n  (no new contacts from {from_user} — all already known)")
    print("type.....: ", end="", flush=True)


def send_contact_request(peer_name: str):
    """Send a contact-list request to a saved contact."""
    if peer_name not in contacts:
        print(f'  ✗ "{peer_name}" not in contacts')
        return
    payload = build_payload(MY_USER_ID, MY_USER_ID,
                            grp="None", kind="creq")
    for addr in contacts[peer_name]:
        send_to(addr, payload)
    print(f'  → contact request sent to "{peer_name}"')


def handle_incoming_creq(sender_user: str, sender_addr: tuple):

    def _ask():
        ans = input(f'\n  {sender_user} wants your contact list. share? (y/n): ').strip().lower()
        print("type.....: ", end="", flush=True)
        if ans != "y":
            return
        # Look up the sender's address: prefer saved contact, fall back to socket addr
        saved = lookup_contact_name(sender_addr[0], sender_addr[1])
        target_name = saved or sender_user
        addrs = contacts.get(target_name)
        if not addrs:
            # Build a one-shot addr from the socket connection
            addrs = [{"ip": sender_addr[0], "port": sender_addr[1]}]
        payload = build_payload(
            MY_USER_ID,
            _encode_contacts_payload(contacts),
            grp="None",
            kind="cack"
        )
        for addr in addrs:
            send_to(addr, payload)
        print(f'  ✓ contacts sent to {target_name}')
        print("type.....: ", end="", flush=True)

    threading.Thread(target=_ask, daemon=True).start()


def handle_incoming_cack(sender_user: str, encoded: str):
    """Called from reciver when kind=="cack" — merge received contacts."""
    try:
        incoming = _decode_contacts_payload(encoded)
    except (json.JSONDecodeError, Exception) as e:
        print(f'\n  ✗ could not parse contacts from {sender_user}: {e}')
        print("type.....: ", end="", flush=True)
        return
    _merge_contacts(incoming, sender_user)


# ── Contact helpers ────────────────────────────────────────────────────────────

def lookup_contact_name(ip: str, port: int) -> str | None:
    """Return the saved contact name for a given ip:port, or None."""
    for name, addrs in contacts.items():
        for addr in addrs:
            if addr["ip"] == ip and addr["port"] == port:
                return name
    return None


def resolve_name_to_addrs(name: str) -> list[dict] | None:
    """Return address list for a contact OR a group, or None if unknown."""
    if name in contacts:
        return contacts[name]
    if name in groups:
        return _group_addrs(name)
    return None


def add_contact_by_getme(name: str) -> list[dict]:
    raw = input(f'  GetMe ID (9 chars) or IP for "{name}": ').strip()
    if len(raw) == _GETME_WIDTH and all(c in _B62 for c in raw):
        try:
            ip, port = decode_getme_id(raw)
            print(f'    → decoded: {ip}:{port}')
        except ValueError as e:
            print(f"    ✗ bad GetMe ID ({e}), enter IP instead")
            ip   = input(f'  IP for "{name}": ').strip()
            port = int(input(f'  port for "{name}": '))
    else:
        ip   = raw
        port = int(input(f'  port for "{name}": '))
    contacts[name] = [{"ip": ip, "port": port}]
    save_contacts()
    return contacts[name]


# ── Group helpers ──────────────────────────────────────────────────────────────
# groups = { "groupname": { "members": ["alice","bob"], "owner": "me" } }
# Member names must exist in contacts so we can look up their addresses.

def _group_addrs(grp_name: str) -> list[dict]:
    """Collect all contact addresses for every member of a group."""
    addrs = []
    for member in groups.get(grp_name, {}).get("members", []):
        addrs.extend(contacts.get(member, []))
    return addrs


def _notify_group(grp_name: str, text: str, exclude: str | None = None):
    """Send a system notification to all group members (except exclude)."""
    for member in groups.get(grp_name, {}).get("members", []):
        if member == exclude:
            continue
        for addr in contacts.get(member, []):
            send_to(addr, build_payload(MY_USER_ID, text,
                                        grp=grp_name, kind="sys"))


def grp_create(grp_name: str):
    """Create a new empty group owned by this user."""
    if grp_name in groups:
        print(f'  ✗ group "{grp_name}" already exists')
        return
    if grp_name in contacts:
        print(f'  ✗ "{grp_name}" is already a contact name — choose a different group name')
        return
    if grp_name == MY_USER_ID:
        print(f'  ✗ "{grp_name}" is your own username — choose a different group name')
        return
    groups[grp_name] = {"owner": MY_USER_ID, "members": []}
    save_groups()
    print(f'  ✓ group "{grp_name}" created (you are the owner)')


def grp_add_peer(grp_name: str, peer_name: str):
    """Add a contact to a group and notify everyone."""
    if grp_name not in groups:
        print(f'  ✗ group "{grp_name}" not found')
        return
    if peer_name not in contacts:
        print(f'  ✗ "{peer_name}" not in contacts — add them first with /add')
        return
    if peer_name in groups[grp_name]["members"]:
        print(f'  ✗ "{peer_name}" is already in "{grp_name}"')
        return
    groups[grp_name]["members"].append(peer_name)
    save_groups()
    print(f'  ✓ "{peer_name}" added to "{grp_name}"')
    # Notify existing members
    _notify_group(grp_name,
                  f'{peer_name} was added to {grp_name} by {MY_USER_ID}',
                  exclude=peer_name)
    # Notify the new member
    for addr in contacts.get(peer_name, []):
        send_to(addr, build_payload(MY_USER_ID,
                                    f'You were added to group "{grp_name}" by {MY_USER_ID}',
                                    grp=grp_name, kind="sys"))


def grp_remove_peer(grp_name: str, peer_name: str):
    """Remove a contact from a group and notify everyone."""
    if grp_name not in groups:
        print(f'  ✗ group "{grp_name}" not found')
        return
    if peer_name not in groups[grp_name]["members"]:
        print(f'  ✗ "{peer_name}" is not in "{grp_name}"')
        return
    groups[grp_name]["members"].remove(peer_name)
    save_groups()
    print(f'  ✓ "{peer_name}" removed from "{grp_name}"')
    # Notify remaining members
    _notify_group(grp_name,
                  f'{peer_name} was removed from {grp_name} by {MY_USER_ID}')
    # Notify the removed member
    for addr in contacts.get(peer_name, []):
        send_to(addr, build_payload(MY_USER_ID,
                                    f'You were removed from group "{grp_name}" by {MY_USER_ID}',
                                    grp=grp_name, kind="sys"))


def grp_leave(grp_name: str):
    """Leave a group and notify remaining members."""
    if grp_name not in groups:
        print(f'  ✗ group "{grp_name}" not found locally')
        return
    members = groups[grp_name]["members"]
    _notify_group(grp_name,
                  f'{MY_USER_ID} left the group "{grp_name}"',
                  exclude=MY_USER_ID)
    if MY_USER_ID in members:
        members.remove(MY_USER_ID)
    # If owner leaves and group is empty, delete it
    if not members:
        del groups[grp_name]
        print(f'  ✓ left and deleted empty group "{grp_name}"')
    else:
        save_groups()
        print(f'  ✓ left group "{grp_name}"')


def grp_broadcast(grp_name: str, text: str):
    """Send a message to every member of a group."""
    if grp_name not in groups:
        print(f'  ✗ group "{grp_name}" not found')
        return
    addrs = _group_addrs(grp_name)
    if not addrs:
        print(f'  ✗ group "{grp_name}" has no reachable members')
        return
    payload = build_payload(MY_USER_ID, text, grp=grp_name)
    ok = broadcast(addrs, payload)
    storage_handler({"kind": "msg", "user": MY_USER_ID, "msg": text,
                     "grp": grp_name, "time": now_ts()})
    if ok:
        print(f'  ✓ broadcast to "{grp_name}" sent')


def grp_rename(old_name: str, new_name: str):
    """Rename a group and notify all members."""
    if old_name not in groups:
        print(f'  ✗ group "{old_name}" not found')
        return
    if new_name in groups:
        print(f'  ✗ "{new_name}" already exists')
        return
    groups[new_name] = groups.pop(old_name)
    save_groups()
    print(f'  ✓ group renamed "{old_name}" → "{new_name}"')
    _notify_group(new_name,
                  f'Group "{old_name}" was renamed to "{new_name}" by {MY_USER_ID}')


def grp_list():
    """List all groups and their members."""
    if not groups:
        print("  (no groups)")
        return
    for name, data in groups.items():
        members = ", ".join(data["members"]) or "(empty)"
        owner   = data.get("owner", "?")
        print(f'  {name}  [owner: {owner}]  members: {members}')


def grp_delete(grp_name: str):
    """Delete a group entirely after confirmation."""
    if grp_name not in groups:
        print(f'  ✗ group "{grp_name}" not found')
        return
    confirm = input(f'  delete group "{grp_name}"? (yes/no): ').strip().lower()
    if confirm != "yes":
        print("  cancelled")
        return
    _notify_group(grp_name,
                  f'Group "{grp_name}" was deleted by {MY_USER_ID}')
    del groups[grp_name]
    save_groups()
    print(f'  ✓ group "{grp_name}" deleted')


# ── Management commands ────────────────────────────────────────────────────────

CMD_RE = re.compile(r'^/(\w+)(?:\s+(.+))?$')

HELP_TEXT = """
  ── contacts ───────────────────────────────
    /add <name>                add or update a contact
    /del <name>                delete a contact
    /contacts                  list all contacts
    /delcontacts               delete entire contacts file
    /sharec <name>             request contact list from a peer

  ── chat history ───────────────────────────
    /history                   read chat history (with timestamps)
    /delhistory                delete chat history

  ── groups ─────────────────────────────────
    /gcreate  <group>          create a new group
    /gadd     <group> <name>   add a contact to a group
    /gremove  <group> <name>   remove a contact from a group
    /gleave   <group>          leave a group
    /gbroadcast <group> <msg>  broadcast a message to a group
    /grename  <old>   <new>    rename a group
    /glist                     list all groups and members
    /gdelete  <group>          delete a group

    /help                      show this help
  ── anything else → send a message ─────────
"""


def cmd_add(arg: str):
    if not arg:
        print("  usage: /add <name>")
        return
    add_contact_by_getme(arg.strip())
    print(f'  contact "{arg.strip()}" saved')


def cmd_del(arg: str):
    if not arg:
        print("  usage: /del <name>")
        return
    name = arg.strip()
    if name in contacts:
        del contacts[name]
        save_contacts()
        print(f'  contact "{name}" deleted')
    else:
        print(f'  no contact named "{name}"')


def cmd_list_contacts():
    if not contacts:
        print("  (no saved contacts)")
        return
    print("  saved contacts:")
    for name, addrs in contacts.items():
        for addr in addrs:
            try:
                gid = encode_getme_id(addr["ip"], addr["port"])
            except Exception:
                gid = "?????????"
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
    with lock:
        history = _load_json_safe(CHAT_FILE, [])
    if not history:
        print("  (chat history is empty)")
        return
    print(f"\n  ── chat history ({len(history)} messages) ──")
    for entry in history:
        ts   = entry.get("time", "??:??:??")
        grp  = entry.get("grp",  "None")
        kind = entry.get("kind", "msg")
        user = entry.get("user", "?")
        text = entry.get("msg",  "")
        if kind == "sys":
            print(f'  [{ts}] *** {text}')
        else:
            prefix = f"[{grp}] " if grp != "None" else ""
            print(f'  [{ts}] {prefix}{user} : {text}')
    print("  ── end ──\n")


def cmd_del_history():
    confirm = input("  delete chat history? (yes/no): ").strip().lower()
    if confirm == "yes":
        with lock:
            if os.path.exists(CHAT_FILE):
                os.remove(CHAT_FILE)
                print("  ✓ chat history deleted")
            else:
                print("  (history file not found)")
    else:
        print("  cancelled")


# Group command parsers — split arg into 1 or 2 tokens
_TWO_RE = re.compile(r'^(\S+)\s+(.+)$')   # first-word  rest


def _split2(arg: str) -> tuple[str, str]:
    m = _TWO_RE.match(arg or "")
    return (m.group(1), m.group(2).strip()) if m else (arg or "", "")


def dispatch_command(cmd: str, arg: str | None):
    arg = (arg or "").strip()
    cmd = cmd.lower()

    # ── contacts
    if   cmd == "add":          cmd_add(arg)
    elif cmd == "del":          cmd_del(arg)
    elif cmd == "contacts":     cmd_list_contacts()
    elif cmd == "delcontacts":  cmd_del_contacts()
    elif cmd == "sharec":
        if not arg: print("  usage: /sharec <contact-name>")
        else: send_contact_request(arg.strip())

    # ── history
    elif cmd == "history":      cmd_history()
    elif cmd == "delhistory":   cmd_del_history()

    # ── groups
    elif cmd == "gcreate":
        if not arg: print("  usage: /gcreate <group>")
        else: grp_create(arg)

    elif cmd == "gadd":
        grp, name = _split2(arg)
        if not grp or not name: print("  usage: /gadd <group> <contact>")
        else: grp_add_peer(grp, name)

    elif cmd == "gremove":
        grp, name = _split2(arg)
        if not grp or not name: print("  usage: /gremove <group> <contact>")
        else: grp_remove_peer(grp, name)

    elif cmd == "gleave":
        if not arg: print("  usage: /gleave <group>")
        else: grp_leave(arg)

    elif cmd == "gbroadcast":
        grp, text = _split2(arg)
        if not grp or not text: print("  usage: /gbroadcast <group> <message>")
        else: grp_broadcast(grp, text)

    elif cmd == "grename":
        old, new = _split2(arg)
        if not old or not new: print("  usage: /grename <old> <new>")
        else: grp_rename(old, new)

    elif cmd == "glist":        grp_list()

    elif cmd == "gdelete":
        if not arg: print("  usage: /gdelete <group>")
        else: grp_delete(arg)

    elif cmd == "help":         print(HELP_TEXT)
    else: print(f'  ✗ unknown command "/{cmd}" — type /help')


# ── Keyboard handler ───────────────────────────────────────────────────────────

def keyboard_handler():
    print("  type /help for commands")
    while True:
        raw_input = input("type.....: ").strip()

        # ── command branch
        m = CMD_RE.match(raw_input)
        if m:
            dispatch_command(m.group(1), m.group(2))
            continue

        # ── message branch
        sndr_input_msg = raw_input
        if not sndr_input_msg:
            continue

        while True:
            sndr_input_recipient = input("recipient (name / group / GetMe ID): ").strip()
            # Warn if the name exists as BOTH a contact and a group (ambiguous)
            if sndr_input_recipient in contacts and sndr_input_recipient in groups:
                print(f'  ⚠ "{sndr_input_recipient}" is both a contact and a group name — this should not happen.')
                print('    rename the group with /grename, then re-enter.')
                continue
            break

        # Direct GetMe ID (not in contacts or groups)
        if (
            len(sndr_input_recipient) == _GETME_WIDTH
            and all(c in _B62 for c in sndr_input_recipient)
            and sndr_input_recipient not in contacts
            and sndr_input_recipient not in groups
        ):
            try:
                ip, port = decode_getme_id(sndr_input_recipient)
                addrs        = [{"ip": ip, "port": port}]
                display_name = sndr_input_recipient
            except ValueError:
                addrs        = None
                display_name = sndr_input_recipient
        else:
            addrs        = resolve_name_to_addrs(sndr_input_recipient)
            display_name = sndr_input_recipient

        if addrs is None:
            addrs = add_contact_by_getme(sndr_input_recipient)

        is_group  = sndr_input_recipient in groups
        grp_field = display_name if (len(addrs) > 1 or is_group) else "None"
        payload   = build_payload(MY_USER_ID, sndr_input_msg, grp=grp_field)

        any_failed = False
        for addr in addrs:
            if not send_to(addr, payload):
                any_failed = True

        storage_handler({"kind": "msg", "user": MY_USER_ID,
                         "msg": sndr_input_msg, "grp": grp_field,
                         "time": now_ts()})

        # Offer to fix a bad single contact
        if any_failed and sndr_input_recipient in contacts:
            fix = input(f'  update address for "{sndr_input_recipient}"? (y/n): ').strip().lower()
            if fix == "y":
                add_contact_by_getme(sndr_input_recipient)
                retry = input("  retry sending? (y/n): ").strip().lower()
                if retry == "y":
                    for addr in contacts[sndr_input_recipient]:
                        send_to(addr, payload)


# ── Entry point ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("-p", type=int, default=6497)
parser.add_argument("--getmeby", default="me")
args = parser.parse_args()

server_ip   = "127.0.0.1"
server_port = args.p
MY_USER_ID  = args.getmeby

MY_GETME_ID = encode_getme_id(server_ip, server_port)

print("=" * 36)
print(f"  Your GetMe ID : {MY_GETME_ID}")
print(f"  ({server_ip}:{server_port})")
print("=" * 36)

contacts = load_contacts()
groups   = load_groups()

threading.Thread(target=keyboard_handler, daemon=True).start()
handle(server_ip, server_port)
