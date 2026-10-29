"""Immutable cached view models and nonblocking public synchronization."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from radar import ROOT,validate_snapshot
from live_data import load_live_metadata,sync_public_observations
from works import build_works

def signature(root=ROOT):
    root=Path(root);result=[]
    for name in ('catalog_latest.json','metadata_latest.json','detail_seeds.json','.radar_cache/details.json'):
        p=root/name
        result.append((name,p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else (name,0,0))
    return tuple(result)

@lru_cache(maxsize=2)
def _dataset(root,version):
    root=Path(root);snapshot=json.loads((root/'catalog_latest.json').read_bytes());validate_snapshot(snapshot)
    metadata=load_live_metadata(root)
    return snapshot,metadata,build_works(snapshot,metadata)

def dataset(root=ROOT):return _dataset(str(Path(root).resolve()),signature(root))

_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='radar-sync')
_guard=threading.Lock()
_future=None

def background_sync(root=ROOT,force=False):
    global _future
    with _guard:
        if _future is not None and _future.done():
            future=_future;_future=None
            try:result=future.result()
            except Exception:return {'state':'unavailable','updated':False}
            if not force:return result
        if _future is None:_future=_pool.submit(sync_public_observations,root,force)
        return {'state':'pending','updated':False}
