"""Verify or install the versioned evaluation data bundle, including offline installation."""
import argparse,hashlib,json,tarfile,tempfile,shutil,urllib.request,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation.paths import ROOT,data_root

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def verify(root):
    manifest=json.loads((root/'manifest.json').read_text());assert manifest['version']=='v1'
    names=set()
    for item in manifest['files']:
        rel=Path(item['path'])
        if rel.is_absolute() or '..' in rel.parts or str(rel) in names:raise ValueError('Invalid manifest path')
        names.add(str(rel));p=root/rel
        if not p.is_file() or p.is_symlink() or p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']:
            raise ValueError(f'Missing or altered bundle input: {rel}')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name!='manifest.json'}
    if actual!=names:raise ValueError('Bundle contains unlisted files')
    print(f"Verified {len(names)} files, {sum(x['bytes'] for x in manifest['files'])} bytes")
    return manifest

def install(archive,destination,expected):
    if digest(archive)!=expected:raise ValueError('Archive checksum mismatch; refusing extraction')
    target=destination/'observe-data'
    if target.exists():raise ValueError('Destination bundle already exists; use a new destination')
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='observe-install-',dir=destination) as td:
        temp=Path(td)
        with tarfile.open(archive,'r:gz') as tar:
            names=set()
            for member in tar:
                path=Path(member.name)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='observe-data' or member.name in names or not (member.isdir() or member.isfile()):raise ValueError('Unsafe archive member')
                names.add(member.name);out=temp/path
                if member.isdir():out.mkdir(parents=True,exist_ok=True)
                else:
                    out.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(member) as src,out.open('xb') as dst:shutil.copyfileobj(src,dst)
        meta=json.loads((ROOT/'evaluation/data_bundle.json').read_text())
        if digest(temp/'observe-data/manifest.json')!=meta['manifest_sha256']:raise ValueError('Manifest checksum mismatch')
        verify(temp/'observe-data');(temp/'observe-data').rename(target)
    print('Installed observe-data')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('mode',choices=['verify','install','download']);ap.add_argument('--data-root',type=Path);ap.add_argument('--archive',type=Path,default=ROOT/'generated-results/observe-evaluation-data-v1.tar.gz');ap.add_argument('--destination',type=Path,default=ROOT);a=ap.parse_args()
    if a.mode=='verify':verify(data_root(a.data_root))
    else:
        meta=json.loads((ROOT/'evaluation/data_bundle.json').read_text())
        if a.mode=='download':
            if not meta['download_url']:ap.error('No download URL is configured; use the offline install command')
            a.archive.parent.mkdir(parents=True,exist_ok=True)
            urllib.request.urlretrieve(meta['download_url'],a.archive)
        install(a.archive,a.destination,meta['archive_sha256'])
if __name__=='__main__':main()
