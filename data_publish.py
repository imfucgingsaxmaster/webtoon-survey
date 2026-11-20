"""Publish a consistent data checkpoint, including earlier collector outputs."""
from pathlib import Path
import subprocess
import time

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
    elif changed:raise RuntimeError('Unable to inspect data checkpoint')
    # A previous push may have failed after its commit was created. Even with
    # no new staged files, publish that pending commit. Concurrent code edits
    # can advance main between pull and push; rebase and retry without force.
    for attempt in range(3):
        git('pull','--rebase','origin','main')
        try:
            git('push','origin','HEAD:main')
            return
        except subprocess.CalledProcessError:
            if attempt==2:raise
            time.sleep(attempt+1)
