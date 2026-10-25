# GetMeBy

A peer-to-peer terminal messaging program written in Python. No servers, no accounts. Each peer runs the script directly and communicates over TCP using GetMe IDs instead of raw IP addresses and ports.

---

## Requirements

- Python 3.10 or later
- No third-party packages

---

## Running

```
python3 howsapp.py --getmeby <your_name> -p <port>
```

| Argument | Default | Description |
|---|---|---|
| `--getmeby` | `me` | Your display name, shown to peers when you send a message |
| `-p` | `6497` | The port this instance listens on |

On startup the program prints your GetMe ID, which encodes your IP and port into a 9-character alphanumeric string. Share this with anyone you want to message.

```
====================================
  Your GetMe ID : 0DEp6o82G
  (127.0.0.1:1990)
====================================
  type /help for commands
```

---

## GetMe ID

A GetMe ID is a 9-character case-sensitive alphanumeric string that encodes an IP address and port. It is the only thing a peer needs to reach you. There is no central registry; the encoding and decoding happen locally using base-62 arithmetic.

Characters used: `0-9 a-z A-Z` (62 symbols). One character difference changes the decoded address, so copy-paste rather than retype.

---

## Sending a Message

Type anything that does not start with `/` and press Enter. You will then be prompted for the recipient.

```
type.....: hello
recipient (name / group / GetMe ID): mie
```

The recipient can be:

- A saved contact name
- A group name
- A raw GetMe ID (9 characters)

If the name is not in your contacts, you will be asked to provide a GetMe ID or IP for it, and it will be saved.

---

## Data Storage

All data is stored in a `howsapp_data/` folder created next to the script.

| File | Contents |
|---|---|
| `contacts.json` | Saved contacts (name to IP/port) |
| `groups.json` | Groups with members and owner |
| `chat.json` | Full message history with timestamps |

If a file becomes corrupt it is renamed to `<file>.corrupt` and a fresh one is started automatically.

---

## Commands

All commands start with `/`. Type `/help` at any time to see the full list.

### Contacts

| Command | Description |
|---|---|
| `/add <name>` | Add or update a contact by GetMe ID or IP |
| `/del <name>` | Delete a saved contact |
| `/contacts` | List all saved contacts with their GetMe IDs |
| `/delcontacts` | Delete the entire contacts file (requires confirmation) |
| `/sharec <name>` | Request the contact list from a peer |

### Chat History

| Command | Description |
|---|---|
| `/history` | Print full chat history with timestamps |
| `/delhistory` | Delete chat history (requires confirmation) |

### Groups

| Command | Description |
|---|---|
| `/gcreate <group>` | Create a new group |
| `/gadd <group> <contact>` | Add a saved contact to a group |
| `/gremove <group> <contact>` | Remove a contact from a group |
| `/gleave <group>` | Leave a group |
| `/gbroadcast <group> <message>` | Send a message to all group members |
| `/grename <old> <new>` | Rename a group |
| `/glist` | List all groups and their members |
| `/gdelete <group>` | Delete a group (requires confirmation) |

---

## Contact Sharing

One peer requests the other's contact list using `/sharec`. The receiving peer is prompted to accept or decline. If they accept, their contacts are sent back and merged into the requester's `contacts.json`. Existing contacts are never overwritten during a merge.

```
# Requester
type.....: /sharec anandhu
  -> contact request sent to "anandhu"

# Receiver (prompted automatically)
  anandhu wants your contact list. share? (y/n): y
  contacts sent to anandhu

# Requester (after acceptance)
  contacts received from anandhu: ketch, hya
```

---

## Groups

Group names must not match any existing contact name or your own username. When a member is added or removed, all other members receive a notification. The same applies when someone leaves or the group is renamed or deleted.

Group messages show the group name in brackets:

```
  [2025-10-25 14:33:10] [devs] anandhu : meeting at 3
```

---

## Wire Format

Messages are sent as plain UTF-8 strings in `key="value"` pairs:

```
user="hya" msg="hello" grp="None" kind="msg" time="2025-10-25 14:30:00"
```

| Field | Values |
|---|---|
| `user` | Sender's display name |
| `msg` | Message text |
| `grp` | Group name, or `None` for direct messages |
| `kind` | `msg`, `sys`, `creq`, or `cack` |
| `time` | Timestamp at send time |

`sys` messages are event notifications (added to group, removed, renamed, etc.). `creq` and `cack` are used internally for contact sharing and are not stored in chat history.

---

## Known Limitations

- Only `127.0.0.1` (localhost) is used as the listening address. To communicate across a network, change `server_ip` in the entry point to `0.0.0.0` or the machine's LAN IP.
- Each connection is single-message. There is no persistent session between peers.
- Message size is limited to 4096 bytes per receive call. Very large contact lists sent via `/sharec` may be truncated.
- No encryption. Messages are sent in plaintext over TCP.
