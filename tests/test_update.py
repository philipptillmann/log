from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parents[1]


class Update(unittest.TestCase):
    def command(self, *args, cwd=None, check=True):
        return subprocess.run(args, cwd=cwd or self.clone, env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.origin, self.source, self.clone = root/'origin.git', root/'source', root/'home'/'log'
        self.clone.parent.mkdir()
        self.env = dict(os.environ)
        self.env.update(GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.test', GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.test')
        self.env.pop('LOG_UPDATE_REEXEC', None)
        self.command('git', 'init', '--bare', '--initial-branch=main', str(self.origin), cwd=root)
        self.command('git', 'clone', str(self.origin), str(self.source), cwd=root)
        for filename in ('update.sh', '.gitignore', '.env.example', 'compose.yaml'):
            shutil.copy2(ROOT/filename, self.source/filename)
        self.command('git', 'add', '.', cwd=self.source)
        self.command('git', 'commit', '-m', 'Initial', cwd=self.source)
        self.command('git', 'push', '-u', 'origin', 'main', cwd=self.source)
        self.command('git', 'clone', str(self.origin), str(self.clone), cwd=root)
        self.bin = root/'bin'
        self.bin.mkdir()
        self.log = root/'docker-calls'
        docker = self.bin/'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_LOG"\nif [ "$*" = "compose build --pull" ] && [ "${TEST_BUILD_FAIL:-0}" = 1 ]; then exit 1; fi\n')
        docker.chmod(0o755)
        self.env['PATH'] = str(self.bin) + os.pathsep + self.env['PATH']
        self.env['TEST_DOCKER_LOG'] = str(self.log)

    def tearDown(self):
        self.temp.cleanup()

    def update(self):
        return self.command(str(self.clone/'update.sh'), cwd=self.clone.parent, check=False)

    def test_first_install_and_remote_update_preserve_config(self):
        self.assertEqual(self.update().returncode, 0)
        self.assertTrue((self.clone/'.env').exists())
        (self.clone/'.env').write_text('LOG_PORT=9876\n')
        (self.source/'new.txt').write_text('remote update')
        self.command('git','add','.',cwd=self.source)
        self.command('git','commit','-m','Update',cwd=self.source)
        self.command('git','push',cwd=self.source)
        self.assertEqual(self.update().returncode,0)
        self.assertEqual((self.clone/'new.txt').read_text(),'remote update')
        self.assertEqual((self.clone/'.env').read_text(),'LOG_PORT=9876\n')
        calls = self.log.read_text()
        self.assertIn('compose up -d --wait', calls)
        self.assertNotIn('down', calls)
        self.assertNotIn('prune', calls)

    def test_dirty_checkout_is_not_overwritten(self):
        (self.clone/'compose.yaml').write_text('local changes')
        self.assertNotEqual(self.update().returncode,0)
        self.assertEqual((self.clone/'compose.yaml').read_text(),'local changes')
        self.assertNotIn('compose build', self.log.read_text())

    def test_failed_build_does_not_restart_running_app(self):
        self.env['TEST_BUILD_FAIL']='1'
        self.assertNotEqual(self.update().returncode,0)
        self.assertNotIn('compose up',self.log.read_text())


if __name__ == '__main__':
    unittest.main()
