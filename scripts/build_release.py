#!/usr/bin/env python3
"""Deterministic release archive and checksums. Does not install or publish."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from toolbelt import __version__
from build_iterm_script import build,OUTPUT

def build_release(output):
    build()
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    prefix='iterm2-tmux-toolbelt-v'+__version__
    single=output/'tmux_toolbelt.py';shutil.copyfile(OUTPUT,single)
    archive_path=output/(prefix+'.zip')
    files=[ROOT/name for name in ('README.md','README.zh-CN.md','LICENSE','NOTICE','THIRD_PARTY.md','NOTICE','CHANGELOG.md','CONTRIBUTING.md','requirements.txt','.gitignore','tmux_toolbelt.py','.github/workflows/tests.yml')]
    files.append(OUTPUT)
    for folder in ('toolbelt','scripts','tests','docs'):
        files.extend(path for path in (ROOT/folder).rglob('*') if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc' and path.name!='.DS_Store')
    with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            if path.is_symlink():raise RuntimeError('Release input must not be a symlink')
            relative=path.relative_to(ROOT).as_posix()
            entry=zipfile.ZipInfo(prefix+'/'+relative,(2020,1,1,0,0,0));entry.external_attr=0o644<<16
            archive.writestr(entry,path.read_bytes(),compress_type=zipfile.ZIP_DEFLATED)
    checksum=output/'SHA256SUMS.txt'
    checksum.write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n' for path in (archive_path,single)))
    print('Built v'+__version__+' release archive, single-file script and SHA256SUMS.txt')
    return archive_path,single,checksum

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'release'/('v'+__version__))
    build_release(parser.parse_args().output)
