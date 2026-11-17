# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Repair classic TIFF page addressing without resampling or changing pixels."""
from pathlib import Path
import struct
from PIL import Image


def repair_imagej_directory(path):
    """Repair a writable copy; never call on an original acquisition file.

    Append a first IFD with explicit top-left orientation, preserving every
    existing data pointer and the remaining IFD chain. ImageJ then uses the
    page offsets rather than assuming contiguous raw planes.
    """
    path = Path(path)
    with Image.open(path) as image:
        if image.format!='TIFF': raise ValueError('TIFFファイルではありません')
        if image.n_frames<2 or not str(image.tag_v2.get(270,'')).startswith('ImageJ='):
            return False
        if 274 in image.tag_v2:
            if image.tag_v2[274]!=1: raise ValueError('Orientationが1以外のTIFFは修復対象外です')
            return False
    with path.open('r+b') as stream:
        header = stream.read(8)
        if header[:4]==b'II*\x00': endian = '<'
        elif header[:4]==b'MM\x00*': endian = '>'
        else: raise ValueError('通常のTIFFのみ修復できます（BigTIFFは対象外）')
        offset = struct.unpack(endian+'I',header[4:8])[0]
        stream.seek(offset)
        count_bytes = stream.read(2)
        if len(count_bytes)!=2: raise ValueError('TIFFのIFDが不正です')
        count = struct.unpack(endian+'H',count_bytes)[0]
        if count>=65535: raise ValueError('TIFFのタグ数が上限です')
        raw = stream.read(12*count+4)
        if len(raw)!=12*count+4: raise ValueError('TIFFのIFDが途切れています')
        entries = [raw[i*12:(i+1)*12] for i in range(count)]
        entries.append(struct.pack(endian+'HHI',274,3,1)+struct.pack(endian+'H',1)+b'\x00\x00')
        entries.sort(key=lambda entry:struct.unpack(endian+'H',entry[:2])[0])
        stream.seek(0,2)
        new_offset = stream.tell()
        if new_offset%2:
            stream.write(b'\x00'); new_offset += 1
        directory = struct.pack(endian+'H',count+1)+b''.join(entries)+raw[-4:]
        if new_offset+len(directory)>=2**32: raise ValueError('4GBを超えるため修復できません')
        stream.write(directory)
        stream.flush()
        stream.seek(4)
        stream.write(struct.pack(endian+'I',new_offset))
    return True
