"""log: stores authentication verifiers and opaque encrypted vaults only."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get('LOG_DATA', '/data/log.sqlite3')
STATIC = Path(__file__).parent / 'static'
MAX_BODY = 8 * 1024 * 1024
SESSIONS = {}
ATTEMPTS = {}
LOCK = threading.Lock()


def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS users (name TEXT PRIMARY KEY, salt TEXT NOT NULL, verifier TEXT NOT NULL, vault TEXT, version INTEGER NOT NULL DEFAULT 0)')
    os.chmod(DB, 0o600)


def valid_b64(value, size=None):
    try:
        raw = base64.b64decode(value, validate=True)
        return (size is None or len(raw) == size)
    except (ValueError, TypeError):
        return False


def verifier(secret, salt):
    return hashlib.scrypt(secret.encode(), salt=base64.b64decode(salt), n=16384, r=8, p=1).hex()


class Handler(BaseHTTPRequestHandler):
    # Never log credentials, tokens, request bodies, or note contents.
    def log_message(self, *_):
        pass

    def reply(self, status, payload=None, mime='application/json'):
        content = json.dumps(payload).encode() if mime == 'application/json' else payload
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)

    def user(self):
        token = self.headers.get('Authorization', '').removeprefix('Bearer ')
        with LOCK:
            session = SESSIONS.get(token)
            if session and session[1] > time.time():
                return session[0]
        return None

    def do_GET(self):
        if self.path == '/health':
            with connect() as db:
                db.execute('SELECT 1').fetchone()
            return self.reply(200, {'ok': True})
        if self.path == '/api/vault':
            name = self.user()
            if not name:
                return self.reply(401, {'error': 'Bitte erneut anmelden.'})
            with connect() as db:
                row = db.execute('SELECT vault, version FROM users WHERE name=?', (name,)).fetchone()
            return self.reply(200, {'vault': json.loads(row['vault']) if row['vault'] else None, 'version': row['version']})
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8'), '/logo.svg': ('logo.svg', 'image/svg+xml')}
        if self.path in files:
            filename, mime = files[self.path]
            return self.reply(200, (STATIC / filename).read_bytes(), mime)
        self.reply(404, {'error': 'Nicht gefunden.'})

    def do_POST(self):
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.reply(415, {'error': 'JSON erforderlich.'})
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_BODY:
                return self.reply(413, {'error': 'Anfrage zu groß.'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            self.post(data)
        except (ValueError, TypeError, KeyError, OverflowError):
            self.reply(400, {'error': 'Ungültige Anfrage.'})
        except sqlite3.Error:
            self.reply(503, {'error': 'Speichern derzeit nicht möglich. Bitte erneut versuchen.'})

    def post(self, data):
        if self.path in ('/api/salt', '/api/register', '/api/login'):
            # Limit by socket IP, never trust client-supplied forwarding headers.
            ip = self.client_address[0]
            now = time.time()
            with LOCK:
                for key in list(ATTEMPTS):
                    if ATTEMPTS[key][1] <= now:
                        del ATTEMPTS[key]
                count, until = ATTEMPTS.get(ip, (0, now + 60))
                if count >= 30:
                    return self.reply(429, {'error': 'Bitte in einer Minute erneut versuchen.'})
                ATTEMPTS[ip] = (count + 1, until)
            name = data.get('name', '')
            if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,64}', name):
                raise ValueError()
            if self.path == '/api/salt':
                with connect() as db:
                    row = db.execute('SELECT salt FROM users WHERE name=?', (name,)).fetchone()
                if not row:
                    return self.reply(401, {'error': 'Anmeldung fehlgeschlagen.'})
                return self.reply(200, {'salt': row['salt']})
            secret = data.get('auth', '')
            if not valid_b64(secret, 32):
                raise ValueError()
            if self.path == '/api/register':
                salt = data.get('salt', '')
                if not valid_b64(salt, 32):
                    raise ValueError()
                digest = verifier(secret, salt)
                with connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] and os.environ.get('LOG_ALLOW_REGISTRATION', 'false').lower() != 'true':
                        return self.reply(403, {'error': 'Registrierung geschlossen.'})
                    try:
                        db.execute('INSERT INTO users(name, salt, verifier) VALUES(?,?,?)', (name, salt, digest))
                    except sqlite3.IntegrityError:
                        return self.reply(409, {'error': 'Account bereits vorhanden.'})
                return self.reply(201, {'ok': True})
            with connect() as db:
                row = db.execute('SELECT salt, verifier FROM users WHERE name=?', (name,)).fetchone()
            if not row or not hmac.compare_digest(row['verifier'], verifier(secret, row['salt'])):
                return self.reply(401, {'error': 'Anmeldung fehlgeschlagen.'})
            token = secrets.token_urlsafe(32)
            with LOCK:
                for key in list(SESSIONS):
                    if SESSIONS[key][1] <= now:
                        del SESSIONS[key]
                SESSIONS[token] = (name, now + 12 * 3600)
            return self.reply(200, {'token': token})
        name = self.user()
        if not name:
            return self.reply(401, {'error': 'Bitte erneut anmelden.'})
        if self.path == '/api/logout':
            with LOCK:
                SESSIONS.pop(self.headers.get('Authorization', '').removeprefix('Bearer '), None)
            return self.reply(200, {'ok': True})
        if self.path == '/api/vault':
            vault = data['vault']
            version = data['version']
            if not isinstance(vault, dict) or not isinstance(version, int) or version < 0:
                raise ValueError()
            if set(vault) != {'iv', 'ciphertext'} or not valid_b64(vault['iv'], 12) or not valid_b64(vault['ciphertext']):
                raise ValueError()
            if len(base64.b64decode(vault['ciphertext'])) < 16:
                raise ValueError()
            with connect() as db:
                cursor = db.execute('UPDATE users SET vault=?, version=version+1 WHERE name=? AND version=?', (json.dumps(vault), name, version))
                if not cursor.rowcount:
                    return self.reply(409, {'error': 'In einem anderen Fenster geändert. Exportiere deine Änderungen, bevor du neu lädst.'})
            return self.reply(200, {'version': version + 1})
        self.reply(404, {'error': 'Nicht gefunden.'})


if __name__ == '__main__':
    os.umask(0o077)
    initialize()
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
