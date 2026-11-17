#!/usr/bin/env python3
"""Einmaliges Werkzeug: Gmail-Kennungen und Labels der WBM-Mails ausgeben (keine Inhalte, keine Adressen),
um zu klären, warum Claudes Gmail-Verbindung sie nicht anzeigt."""
import imaplib
import os
import re

box = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
box.login(os.environ["MAIL_USER"], os.environ["MAIL_APP_PASSWORD"].replace(" ", ""))
for folder in ('"[Gmail]/All Mail"', '"[Gmail]/Alle Nachrichten"', "INBOX"):
    typ, _ = box.select(folder, readonly=True)
    if typ == "OK":
        print("Ordner:", folder)
        break
_, data = box.search(None, "X-GM-RAW", '"from:wbm.de newer_than:40d"')
nums = data[0].split()
print("WBM-Mails:", len(nums))
for num in nums[-15:]:
    _, meta = box.fetch(num, "(X-GM-MSGID X-GM-THRID X-GM-LABELS INTERNALDATE)")
    line = meta[0].decode("utf-8", "replace") if isinstance(meta[0], bytes) else str(meta[0])
    msgid = re.search(r"X-GM-MSGID (\d+)", line)
    thrid = re.search(r"X-GM-THRID (\d+)", line)
    labels = re.search(r"X-GM-LABELS \((.*?)\)", line)
    date = re.search(r'INTERNALDATE "([^"]+)"', line)
    print(f"  {date.group(1) if date else '?'}  msg={format(int(msgid.group(1)), 'x') if msgid else '?'}"
          f"  thread={format(int(thrid.group(1)), 'x') if thrid else '?'}  labels={labels.group(1) if labels else '?'}")
box.logout()
