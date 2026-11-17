# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Plan portable export names; disambiguate by source identity, not counters."""
import hashlib
import unicodedata
from pathlib import Path
from collections import Counter


def component(value):
    text=unicodedata.normalize('NFKC',value)
    text=''.join('-' if ord(char)<32 or char in '<>:"/\\|?*' else char for char in text)
    text=text.strip(' .')[:100] or 'image'
    return text


def claim(filename,used,source_key):
    candidate=filename
    if candidate.casefold() in used:
        path=Path(filename)
        for length in range(5,33):
            token=hashlib.blake2s(source_key.encode('utf-8'),digest_size=length).hexdigest()
            candidate=f'{path.stem}--{token}{path.suffix}'
            if candidate.casefold() not in used:break
        else:raise ValueError('出力名を一意にできません。保存名を変えてください')
    used.add(candidate.casefold())
    return candidate


def plan(paths,prefix,extension):
    initial=[f'{prefix}_{component(path.stem)}.{extension}' for path in paths]
    counts=Counter(name.casefold() for name in initial)
    used={name.casefold() for name in initial if counts[name.casefold()]==1}
    result=[]
    for index,(path,name) in enumerate(zip(paths,initial)):
        if counts[name.casefold()]==1:result.append(name);continue
        key=f'{Path(path).resolve()}|slot={index+1}'
        stem=Path(name).stem;token=hashlib.blake2s(key.encode('utf-8'),digest_size=5).hexdigest()
        result.append(claim(f'{stem}--{token}.{extension}',used,key))
    return result
