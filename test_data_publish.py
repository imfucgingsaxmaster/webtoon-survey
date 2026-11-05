import subprocess,tempfile,unittest,shutil
from pathlib import Path
from data_publish import publish_data

@unittest.skipUnless(shutil.which('git'),'Git is needed for checkpoint integration test')
class DataPublishTests(unittest.TestCase):
    def test_pending_catalogue_and_history_are_committed_with_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);remote=base/'remote.git';root=base/'work';root.mkdir()
            def git(*args,cwd=root):
                return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout
            git('init','--bare',str(remote),cwd=base);git('init','-b','main')
            git('config','user.name','Checkpoint Test');git('config','user.email','test@example.invalid')
            git('config','commit.gpgsign','false');git('remote','add','origin',str(remote))
            (root/'catalog_latest.json').write_text('{}');(root/'metadata_latest.json').write_text('{}')
            git('add','.');git('commit','-m','initial');git('push','-u','origin','main')
            (root/'catalog_latest.json').write_text('{"observed":"today"}')
            (root/'metadata_latest.json').write_text('{"publisher":"updated"}')
            (root/'history').mkdir();(root/'history'/'today.json.gz').write_bytes(b'observation')
            (root/'private-note.txt').write_text('must not be published')
            publish_data(root,'data checkpoint')
            self.assertEqual(git('diff','--name-only'),'')
            self.assertEqual(git('ls-files','private-note.txt'),'')
            self.assertEqual(git('rev-parse','HEAD'),git('rev-parse','refs/heads/main',cwd=remote))
            self.assertIn('today',git('show','HEAD:catalog_latest.json'))
            self.assertEqual(git('show','HEAD:history/today.json.gz'),'observation')

if __name__=='__main__':unittest.main()
