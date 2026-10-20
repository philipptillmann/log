# log

A minimal, self-hosted encrypted journal. No slogans, prompts, streaks, or goals. Just writing, optional titles, nested folder paths, tags, and search. A quiet night-blue palette with the same book/keyhole logo on the website and home-screen icon.

## Install in your home folder

Requirements: Linux home server, Git, Bash, `flock` (util-linux), Docker Engine with the Docker Compose v2 plugin (supporting `up --wait`), and permission for your user to run Docker. Docker daemon access grants administrative control of the host; use a trusted server account. No host Python, Node, or database installation is needed.

```bash
cd ~
git clone https://github.com/philipptillmann/log.git
cd log
./update.sh
```

The app listens on `127.0.0.1:8080` by default. From your own computer, open an SSH tunnel to create the first account privately:

```bash
ssh -L 8080:127.0.0.1:8080 your-user@your-server
```

Open **http://localhost:8080** on that computer. Click **Account erstellen** and choose a strong, unique password (at least 12 characters). The first account can be created without additional configuration. Additional registrations are disabled by default. Create your account before making the service reachable by others.

For regular remote access, put your existing HTTPS reverse proxy in front of `127.0.0.1:8080`. For example, with Caddy running on the host and a domain pointing to your server:

```caddyfile
log.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

A reverse proxy in another container needs a shared network or a suitably bound host endpoint; its own `127.0.0.1` is not the host. Do not expose the journal over plain HTTP: Web Crypto requires HTTPS (except localhost), and account authentication also needs encrypted transport.

## Update

Whenever you want to deploy changes from GitHub:

```bash
~/log/update.sh
```

Or run `./update.sh` from `~/log`. The script:

- Resolves its own directory, so your current working directory does not matter.
- Prevents concurrent updates with `flock`.
- Refuses a dirty checkout and uses fast-forward-only Git updates; never resets your changes.
- Follows the checked-out branch's configured upstream (`origin/main` for the default clone).
- Creates `.env` from `.env.example` only when it does not exist.
- Builds the new Docker image before replacing the running container and waits for health.
- Preserves the `log-data` volume and local `.env`. Never runs `down`, removes volumes, or prunes images.

A build failure leaves the running container in place. A failed startup is reported with a nonzero exit code; startup rollback is not automatic. Check `docker compose logs --tail=100` and `docker compose ps` from `~/log`. Updates may briefly interrupt access and invalidate sessions. Keep only one installation with the default Compose project/volume name on a host.

To change the port or permit more accounts, edit `~/log/.env`, then run `~/log/update.sh` again. Set `LOG_ALLOW_REGISTRATION=true` only while creating additional accounts, then set it back to `false`. The first-account exception still applies if the database contains no accounts.

## Writing

Create an entry and write. Changes save automatically. Use `Ordner & Tags` to enter a folder path such as `Privat/Gedanken` and comma-separated tags. Parent folder filters include descendants. Search operates on decrypted data in your browser. The sidebar can be hidden. Only the app name, an optional greeting, and functional controls accompany the editor.

`Sperren` saves, revokes the session, and clears the page. Keys, session tokens, and plaintext notes stay in page memory, never localStorage. The page warns before leaving with unsaved changes. Conflicting saves from another browser/window are rejected; export your changes before reloading. Network errors remain visible and saving retries when the browser comes online. There is no offline persistence: do not close the page while changes are unsaved.

**Exportieren downloads an unencrypted JSON copy** of all entries, dates, folders, and tags. Store it privately. This version has no import function, password reset, password change, or recovery key. Losing your password means losing access to encrypted entries. Use a password manager.

## Encryption and limits

Encryption and decryption happen in the browser using Web Crypto. PBKDF2-SHA-256 with 600,000 iterations and a random 32-byte account salt derives 64 bytes: the first half becomes a non-extractable AES-256-GCM key; the second half authenticates the account over HTTPS. The encryption key and the password are never sent to the server. Each save uses a new random 96-bit nonce and authenticated account/version-format context. Entire vaults, including entry dates, titles, nested folder paths, and tags, are encrypted together. This first version loads the entire vault into memory and allows requests up to 8 MiB; it is intended for personal text journals, not attachments or large archives.

The server stores a scrypt verifier of the authentication secret, the public salt, username, encrypted vault, and a revision counter in SQLite. Server-side sessions last 12 hours and are held in memory. Basic per-IP throttling limits authentication endpoints (behind a reverse proxy this limit is shared by all users). The server still sees usernames, IP addresses, ciphertext sizes, access times, and revision counts. Database copies do not expose note plaintext, but weak passwords remain vulnerable to offline guessing.

This is an initial implementation, not an independently audited security product. Browser encryption protects stored data, not a compromised device or a malicious server delivering altered JavaScript. Serve the app through trusted HTTPS, keep the host secure, and review updates before deploying them. No analytics, external fonts, or third-party scripts are loaded.

## Backup and restore

Updates preserve data but are not backups. The persistent Docker volume is named `log-data`. Back up the SQLite database consistently with SQLite's online backup API (works while the app is running):

```bash
cd ~/log
mkdir -p backups
chmod 700 backups
backup="backups/log-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
(umask 077; docker compose exec -T log python -c 'import sqlite3,sys,tempfile; from pathlib import Path; src=sqlite3.connect("/data/log.sqlite3"); tmp=tempfile.TemporaryDirectory(); path=Path(tmp.name)/"backup.sqlite3"; dst=sqlite3.connect(path); src.backup(dst); dst.close(); sys.stdout.buffer.write(path.read_bytes())' > "$backup")
```

Copy backups off the server. They include account verifiers and encrypted vaults, so protect them and retain the passwords separately. Do not copy only a live `.sqlite3` file without its WAL; use the backup command above.

To restore, stop the service with `docker compose stop log`, then copy a known-good backup into the existing volume using a one-off container:

```bash
docker compose run --rm --no-deps -T log python -c 'import sys; from pathlib import Path; p=Path("/data/log.sqlite3"); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600); Path(str(p)+"-wal").unlink(missing_ok=True); Path(str(p)+"-shm").unlink(missing_ok=True)' < backups/YOUR-BACKUP.sqlite3
docker compose up -d --wait
```

Restoring replaces the current database. Take a fresh backup first. Never use `docker compose down -v` unless you intend to delete all journal data.

## Checks

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
bash -n update.sh
docker compose config --quiet
```

Optional browser checks (requires Playwright and Chromium):

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tests/browser_smoke.py
```

Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to use an existing Chromium binary instead.

## Home-screen icon

The web manifest and iPhone touch icon use the same blue book/keyhole logo as the website. In Safari, choose Share → Add to Home Screen. If an existing shortcut still shows the old icon after updating, remove that shortcut and add it again. This does not delete journal data. The home-screen shortcut requires a connection to your server; no offline note storage is added.
