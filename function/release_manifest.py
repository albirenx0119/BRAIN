# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Generate the public version and checksums after building the native launcher."""
import argparse
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
import re
from app_paths import APP_ROOT, FUNCTION_ROOT


def read_release_notes(number,path=None):
    path = path or FUNCTION_ROOT/'release_notes.txt'
    releases = {}
    current = None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'): continue
        if re.fullmatch(r'\[\d+\.\d+\.\d+\]',line):
            current = line[1:-1]
            if current in releases: raise ValueError('Duplicate release notes: '+current)
            releases[current] = []
        elif line.startswith('- ') and current is not None and 0<len(line[2:])<=1500:
            releases[current].append(line[2:])
        else: raise ValueError('Invalid release note line: '+line)
    if not releases.get(number): raise ValueError('Add release notes for '+number+' to function/release_notes.txt')
    version = lambda value:tuple(map(int,value.split('.')))
    return [{'version':key,'changes':releases[key]} for key in sorted(releases,key=version,reverse=True)
            if version(key)<=version(number) and releases[key]][:50]


def write_manifest(number=None):
    previous = {}
    path = APP_ROOT/'version.json'
    if path.exists(): previous = json.loads(path.read_text(encoding='utf-8-sig'))
    number = number or previous.get('version','1.1.0')
    if not re.fullmatch(r'\d+\.\d+\.\d+',number): raise ValueError('Use a version such as 1.0.0')
    notes = read_release_notes(number)
    updated=datetime.now(timezone(timedelta(hours=9)))
    readme=APP_ROOT/'README.md'
    text=readme.read_text(encoding='utf-8')
    text=re.sub(r'バージョン: \d+\.\d+\.\d+\s*(?:· 更新日時: [^·*\n]+)?\s*·',
                f'バージョン: {number} · 更新日時: {updated:%Y-%m-%d %H:%M:%S} JST ·',text)
    readme.write_text(text,encoding='utf-8',newline='\n')
    sources = [APP_ROOT/'BRAIN.exe',APP_ROOT/'README.md',APP_ROOT/'LICENSE',APP_ROOT/'NOTICE']
    extensions = {'.py','.cs','.cmd','.ico','.jpg','.png','.txt'}
    sources += [p for p in FUNCTION_ROOT.rglob('*') if p.is_file() and p.suffix.lower() in extensions
                and all(not part.startswith('.') and part!='__pycache__' for part in p.relative_to(FUNCTION_ROOT).parts)]
    # Keep packaged text identical to Git's canonical bytes on Windows as well.
    for source in sources:
        if source.suffix.lower() in {'.py','.cs','.cmd','.txt','.md'} or source.name in {'LICENSE','NOTICE'}:
            content = source.read_bytes()
            canonical = content.replace(b'\r\n',b'\n')
            if content!=canonical: source.write_bytes(canonical)
    manifest = {'version':number,'release_tag':'v'+number,
                'release_date':previous.get('release_date',str(date.today())) if number==previous.get('version') else str(date.today()),
                'repository':'https://github.com/albirenx0119/BRAIN',
                'author':'Ren Y Sato','copyright':'Copyright 2026 Ren Y Sato','updated_at':updated.isoformat(timespec='seconds'),'release_notes':notes,'files':{p.relative_to(APP_ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in sorted(sources)}}
    content = json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'
    if len(content.encode('utf-8'))>65536: raise ValueError('Release manifest exceeds the updater size limit')
    path.write_text(content,encoding='utf-8',newline='\n')
    return manifest


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version')
    args = parser.parse_args()
    result = write_manifest(args.version)
    print(f'BRAIN {result["version"]}: {len(result["files"])} verified distribution files')
