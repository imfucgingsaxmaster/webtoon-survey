import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from data_publish import publish_data


@unittest.skipUnless(shutil.which('git'), 'git is required')
class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.remote=self.base/'remote.git'
        self.writer=self.base/'writer'
        self.editor=self.base/'editor'
        self.git(self.base,'init','--bare','--initial-branch=main',str(self.remote))
        self.git(self.base,'clone',str(self.remote),str(self.writer))
        self.configure(self.writer)
        (self.writer/'metadata_latest.json').write_text('{}')
        self.git(self.writer,'add','.')
        self.git(self.writer,'commit','-m','initial')
        self.git(self.writer,'push','origin','main')
        self.git(self.base,'clone',str(self.remote),str(self.editor))
        self.configure(self.editor)

    @staticmethod
    def git(root,*args):
        return subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True)

    def configure(self,root):
        self.git(root,'config','user.name','Test')
        self.git(root,'config','user.email','test@example.com')

    def test_retries_remote_advance_without_losing_code_or_observations(self):
        (self.writer/'metadata_latest.json').write_text('{"fresh": 1}')
        original_run=subprocess.run
        raced=False
        def race(args,**kwargs):
            nonlocal raced
            if args[:2]==['git','push'] and Path(kwargs.get('cwd',''))==self.writer and not raced:
                raced=True
                (self.editor/'app.py').write_text('# concurrent code edit')
                for command in [('add','app.py'),('commit','-m','code edit'),('push','origin','main')]:
                    original_run(['git',*command],cwd=self.editor,check=True,capture_output=True)
            return original_run(args,**kwargs)
        with patch('data_publish.subprocess.run',side_effect=race),patch('data_publish.time.sleep'):
            publish_data(self.writer,'data update')
        self.assertTrue(raced)
        self.git(self.editor,'pull','--ff-only')
        self.assertEqual(json.loads((self.editor/'metadata_latest.json').read_text()),{'fresh':1})
        self.assertEqual((self.writer/'app.py').read_text(),'# concurrent code edit')

    def test_final_save_publishes_already_committed_checkpoint(self):
        (self.writer/'metadata_latest.json').write_text('{"fresh": 2}')
        self.git(self.writer,'add','metadata_latest.json')
        self.git(self.writer,'commit','-m','previous unpushed checkpoint')
        publish_data(self.writer,'final save')
        self.git(self.editor,'pull','--ff-only')
        self.assertEqual(json.loads((self.editor/'metadata_latest.json').read_text()),{'fresh':2})


if __name__=='__main__':unittest.main()
