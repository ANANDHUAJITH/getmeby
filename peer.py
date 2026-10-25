import socket
import re
import os
import json
import threading
import argparse

lock = threading.Lock()

CONTACTS_FILE = "/howsapp/contacts.json"
soc = None  # the listening socket, created in handle()


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


def reciver():
    conn, addr = soc.accept()
    with conn:
        msg_recived_total = conn.recv(2048).decode()
        if msg_recived_total:
            msg_split = re.findall(r'(\w+)="([^"]*)"', msg_recived_total)
            msg = dict(msg_split)
            grp = msg.get("grp", "None")
            print(f'{msg["user"]} : {msg["msg"]}' + (f' ,{grp}' if grp != "None" else ""))
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


def send_msg(snd_msg, conn):
    conn.sendall(snd_msg.encode())


def send_to(addr, payload):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.connect((addr["ip"], addr["port"]))
            send_msg(payload, c)
    except OSError as e:
        print(f'could not reach {addr["ip"]}:{addr["port"]} - {e}')


def add_contact(name):
    ip = input(f'ip for "{name}": ')
    port = int(input(f'port for "{name}": '))
    contacts[name] = [{"ip": ip, "port": port}]
    save_contacts()
    return contacts[name]


def keyboard_handler():
    while True:
        sndr_input_msg = input("type.....: ")
        sndr_input_recipient = input('recipient (or group name): ')
        addrs = contacts.get(sndr_input_recipient)
        if addrs is None:
            addrs = add_contact(sndr_input_recipient)
        grp_field = sndr_input_recipient if len(addrs) > 1 else "None"
        sndr_payload = f'user="{MY_USER_ID}" msg="{sndr_input_msg}" grp="{grp_field}"'
        for addr in addrs:
            send_to(addr, sndr_payload)


parser = argparse.ArgumentParser()
parser.add_argument("-p", type=int, default=6497)
parser.add_argument("--getmeby", default="me")
args = parser.parse_args()

server_ip = "127.0.0.1"
server_port = args.p
MY_USER_ID = args.getmeby

contacts = load_contacts()

threading.Thread(target=keyboard_handler, daemon=True).start()
handle(server_ip, server_port)
