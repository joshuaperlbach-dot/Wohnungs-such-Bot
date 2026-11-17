"""Mailtexte verschlüsselt in der öffentlichen Angebotsliste ablegen.

Die Handy-Ansicht liegt öffentlich auf GitHub Pages. Mails enthalten Namen und Kontaktdaten,
deshalb landen sie nur verschlüsselt in docs/offers.json (AES-256-GCM). Der Schlüssel wird aus
einem Passwort abgeleitet (PBKDF2-SHA256), das nur als GitHub-Secret MAIL_VIEW_KEY und im
Speicher des Handys liegt. docs/index.html entschlüsselt mit denselben Werten (WebCrypto).
"""
import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SALT = b"wohnungsbot-mails-v1"
ITERATIONS = 200_000
_keys = {}


def _key(passphrase):
    if passphrase not in _keys:
        _keys[passphrase] = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), SALT, ITERATIONS, 32)
    return _keys[passphrase]


def seal(passphrase, obj):
    nonce = os.urandom(12)
    data = AESGCM(_key(passphrase)).encrypt(nonce, json.dumps(obj, ensure_ascii=False).encode("utf-8"), None)
    return base64.b64encode(nonce + data).decode("ascii")


def unseal(passphrase, box):
    raw = base64.b64decode(box)
    return json.loads(AESGCM(_key(passphrase)).decrypt(raw[:12], raw[12:], None).decode("utf-8"))
