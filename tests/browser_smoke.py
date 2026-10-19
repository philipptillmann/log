"""Optional browser integration check: pip install playwright; playwright install chromium."""
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('server', root / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
with tempfile.TemporaryDirectory() as temp:
    server.DB = temp + '/log.sqlite3'
    server.initialize()
    http = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread = threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
            context = browser.new_context(viewport={'width':1440,'height':1000})
            page = context.new_page()
            errors = []
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.on('dialog',lambda dialog: dialog.accept())
            url = f'http://localhost:{http.server_port}'
            page.goto(url)
            page.locator('#username').fill('alice')
            page.locator('#password').fill('correct horse battery staple')
            page.locator('#register').click()
            page.locator('#app').wait_for(state='visible')
            page.locator('#body').fill('A very private journal entry 918276')
            page.locator('#title').fill('Secret title 4826')
            page.locator('#details-toggle').click()
            page.locator('#folder').fill('Private/Thoughts')
            page.locator('#tags').fill('secret-tag, daily')
            expect(page.locator("#status")).to_have_text("Gespeichert")
            page.locator('#folder-filter').select_option('Private')
            assert page.locator('#notes button').count() == 1
            if os.environ.get('LOG_SCREENSHOT'):
                page.screenshot(path=os.environ['LOG_SCREENSHOT'],full_page=True)
            with server.connect() as db:
                stored = str([tuple(row) for row in db.execute('SELECT * FROM users')])
            for secret in ['A very private journal entry 918276','Secret title 4826','Private/Thoughts','secret-tag','correct horse battery staple']:
                assert secret not in stored, f'Plaintext leaked: {secret}'
            page.locator('#logout').click()
            page.locator('#auth-view').wait_for(state='visible')
            page.locator('#username').fill('alice')
            page.locator('#password').fill('wrong password')
            page.locator('#login').click()
            expect(page.locator("#auth-error")).not_to_be_empty()
            page.locator('#password').fill('correct horse battery staple')
            page.locator('#login').click()
            page.locator('#app').wait_for(state='visible')
            assert page.locator('#body').input_value() == 'A very private journal entry 918276'
            assert page.locator('#folder').input_value() == 'Private/Thoughts'
            second = context.new_page()
            second.goto(url)
            second.locator('#username').fill('alice')
            second.locator('#password').fill('correct horse battery staple')
            second.locator('#login').click()
            second.locator('#app').wait_for(state='visible')
            page.locator('#body').fill('Updated in first window')
            expect(page.locator("#status")).to_have_text("Gespeichert")
            second.locator('#body').fill('Unsaved conflict in second window')
            expect(second.locator("#status")).to_contain_text("anderen Fenster")
            assert second.locator('#body').input_value() == 'Unsaved conflict in second window'
            # A separate phone-size view can reach the editor without the sidebar.
            mobile = browser.new_context(viewport={'width':390,'height':844})
            phone = mobile.new_page()
            phone.goto(url)
            phone.locator('#username').fill('alice')
            phone.locator('#password').fill('correct horse battery staple')
            phone.locator('#login').click()
            phone.locator('#app').wait_for(state='visible')
            phone.locator('#notes button').first.click()
            assert phone.locator('#body').is_visible()
            assert not errors, errors
            browser.close()
            print('Browser checks passed: encryption at rest, login/decryption, nested folders, save conflicts, mobile editor.')
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
