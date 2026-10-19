import base64
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location('server', Path(__file__).parents[1] / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class API(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        server.DB = self.temp.name + '/log.sqlite3'
        server.SESSIONS.clear()
        server.ATTEMPTS.clear()
        os.environ['LOG_ALLOW_REGISTRATION'] = 'false'
        server.initialize()
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.auth = base64.b64encode(b'a' * 32).decode()
        self.salt = base64.b64encode(b's' * 32).decode()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, token=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        conn.request('GET' if data is None else 'POST', path, None if data is None else json.dumps(data), headers)
        response = conn.getresponse()
        status, body = response.status, response.read()
        conn.close()
        return status, json.loads(body)

    def register(self, name='alice'):
        return self.request('/api/register', {'name': name, 'auth': self.auth, 'salt': self.salt})

    def login(self, name='alice'):
        return self.request('/api/login', {'name': name, 'auth': self.auth})[1]['token']

    def test_auth_encrypted_storage_conflict_and_logout(self):
        self.assertEqual(self.register()[0], 201)
        status, stored_salt = self.request('/api/salt', {'name': 'alice'})
        self.assertEqual(stored_salt['salt'], self.salt)
        self.assertEqual(self.request('/api/vault')[0], 401)
        self.assertEqual(self.request('/api/login', {'name': 'alice', 'auth': base64.b64encode(b'b'*32).decode()})[0], 401)
        token = self.login()
        self.assertEqual(self.request('/api/vault', token=token)[1], {'vault': None, 'version': 0})
        encrypted = {'iv': base64.b64encode(b'i'*12).decode(), 'ciphertext': base64.b64encode(b'opaque-ciphertext-and-tag').decode()}
        self.assertEqual(self.request('/api/vault', {'vault': encrypted, 'version': 0}, token)[0], 200)
        self.assertEqual(self.request('/api/vault', {'vault': encrypted, 'version': 0}, token)[0], 409)
        self.assertEqual(self.request('/api/vault', token=token)[1], {'vault': encrypted, 'version': 1})
        with server.connect() as db:
            row = db.execute('SELECT * FROM users').fetchone()
            self.assertNotEqual(row['verifier'], self.auth)
            self.assertNotIn(self.auth, json.dumps(dict(row)))
        self.assertEqual(self.request('/api/logout', {}, token)[0], 200)
        self.assertEqual(self.request('/api/vault', token=token)[0], 401)

    def test_registration_closed_and_account_isolation(self):
        self.register()
        self.assertEqual(self.register('bob')[0], 403)
        os.environ['LOG_ALLOW_REGISTRATION'] = 'true'
        self.assertEqual(self.register('bob')[0], 201)
        alice, bob = self.login(), self.login('bob')
        data = {'iv': base64.b64encode(b'x'*12).decode(), 'ciphertext': base64.b64encode(b'y'*30).decode()}
        self.request('/api/vault', {'vault': data, 'version': 0}, alice)
        self.assertIsNone(self.request('/api/vault', token=bob)[1]['vault'])

    def test_reject_invalid_data_and_throttle(self):
        self.assertEqual(self.request('/api/register', {'name': '../bad', 'auth': self.auth, 'salt': self.salt})[0], 400)
        self.register()
        token = self.login()
        self.assertEqual(self.request('/api/vault', {'vault': {'iv':'bad', 'ciphertext':'plaintext'}, 'version':0}, token)[0], 400)
        for _ in range(31):
            status, _ = self.request('/api/salt', {'name': 'alice'})
        self.assertEqual(status, 429)

    def test_session_expiry_and_static_headers(self):
        self.register()
        token = self.login()
        server.SESSIONS[token] = ('alice', 0)
        self.assertEqual(self.request('/api/vault', token=token)[0], 401)
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port)
        conn.request('GET', '/')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("script-src 'self'", response.getheader('Content-Security-Policy'))
        response.read()
        conn.close()
        self.assertEqual(self.request('/../server.py')[0], 404)


if __name__ == '__main__':
    unittest.main()
