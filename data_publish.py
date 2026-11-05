"""Publish a consistent data checkpoint, including earlier collector outputs."""
from pathlib import Path
import subprocess

DATA_PATHS=('catalog_latest.json','history','metadata_latest.json','metadata_history','history_index.json.gz')

def publish_data(root,message):
    root=Path(root)
    def git(*args):return subprocess.run(['git',*args],cwd=root,check=True)
    paths=[p for p in DATA_PATHS if (root/p).exists()]
    if not paths:return
    git('add','--',*paths)
    changed=subprocess.run(['git','diff','--cached','--quiet'],cwd=root).returncode
    if changed==1:
        git('commit','-m',message)
        git('pull','--rebase','origin','main')
        git('push','origin','HEAD:main')
    elif changed:raise RuntimeError('Unable to inspect data checkpoint')
