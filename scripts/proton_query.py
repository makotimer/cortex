#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv
from imapclient import IMAPClient

# Load .env from project root or script dir
load_dotenv()  # or load_dotenv(Path(__file__).parent.parent / ".env")


def get_client() -> IMAPClient:
    # Smart default for Docker vs local
    default_host = "cortex_bridge" if os.path.exists("/.dockerenv") else "127.0.0.1"

    host = os.getenv("PROTON_IMAP_HOST", default_host)
    port = int(os.getenv("PROTON_IMAP_PORT", "143"))
    user = os.getenv("BRIDGE_USERNAME")
    pwd = os.getenv("BRIDGE_PASSWORD")

    if not user or not pwd:
        raise ValueError("Missing BRIDGE_USERNAME or BRIDGE_PASSWORD in .env")

    print(f"Connecting to {host}:{port} ...")  # helpful debug
    client = IMAPClient(host, port, ssl=False)
    client.login(user, pwd)
    return client


def list_folders() -> None:
    with get_client() as client:
        folders = client.list_folders()
        for flags, delim, name in folders:
            name_str = name.decode() if isinstance(name, bytes) else name
            print(f"{name_str}  (flags: {flags})")


def get_subjects(
    folder: str = "INBOX",
    limit: int = 30,
    criteria: str | list = "ALL",
) -> None:
    with get_client() as client:
        # Resolve folder (handles "Incoming", "INBOX", etc.)
        folders = [n.decode() if isinstance(n, bytes) else str(n) for _, _, n in client.list_folders()]

        resolved = folder
        for n in folders:
            if n.lower() == folder.lower() or n.lower().endswith("/" + folder.lower()):
                resolved = n
                break

        client.select_folder(resolved)
        uids = client.search(criteria)
        uids = sorted(uids, reverse=True)[:limit]  # newest first

        print(f"\n=== {resolved} ===  ({len(uids)} messages)\n")
        print(f"{'Date':<16} {'From':<35} {'To':<35} Subject")
        print("-" * 120)

        for uid in uids:
            data = client.fetch(uid, ["ENVELOPE"])[uid]
            env = data.get(b"ENVELOPE")
            if env:
                date = env.date.strftime("%Y-%m-%d %H:%M") if env.date else "?"

                # From
                from_addr = ""
                if env.from_ and env.from_[0]:
                    f = env.from_[0]
                    mailbox = f.mailbox.decode(errors="replace") if f.mailbox else ""
                    host = f.host.decode(errors="replace") if f.host else ""
                    from_addr = f"{mailbox}@{host}"[:34]

                # To (first recipient)
                to_addr = ""
                if env.to and env.to[0]:
                    t = env.to[0]
                    mailbox = t.mailbox.decode(errors="replace") if t.mailbox else ""
                    host = t.host.decode(errors="replace") if t.host else ""
                    to_addr = f"{mailbox}@{host}"[:34]

                subject = env.subject.decode("utf-8", errors="replace") if env.subject else "(no subject)"

                print(f"{date:<16} {from_addr:<35} {to_addr:<35} {subject}")
            else:
                print(f"UID {uid}: (no envelope data)")


def main():
    parser = argparse.ArgumentParser(description="Standalone Proton Mail (Bridge) query tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-folders", help="List all mailboxes/labels")

    p = sub.add_parser("subjects", help="Get email subjects")
    p.add_argument("folder", nargs="?", default="INBOX")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--unseen", action="store_true", help="Only unseen messages")
    p.add_argument("--search", help="Custom search criteria, e.g. 'SINCE 1-May-2026'")

    args = parser.parse_args()

    if args.cmd == "list-folders":
        list_folders()
    elif args.cmd == "subjects":
        criteria = ["UNSEEN"] if getattr(args, "unseen", False) else "ALL"
        if getattr(args, "search", None):
            criteria = args.search.split()
        get_subjects(folder=args.folder, limit=args.limit, criteria=criteria)


if __name__ == "__main__":
    main()
