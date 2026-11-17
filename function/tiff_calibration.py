# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Physical XY pixel size from TIFF, ImageJ, and OME metadata."""
import math
import re
import xml.etree.ElementTree as ET
from PIL import Image


def unit_factor(unit,factors):
    key = str(unit).strip().lower().replace('µ','u').replace('μ','u')
    if key not in factors: raise ValueError(f'TIFFの長さの単位を認識できません: {unit}')
    return factors[key]


def checked_xy(xy):
    xy = [float(v) for v in xy]
    if any(not math.isfinite(v) or v<=0 for v in xy): raise ValueError('TIFFの画素サイズは正の有限値が必要です')
    return xy


def read_page_calibration(image,factors):
    description = image.tag_v2.get(270,'')
    if isinstance(description,bytes): description = description.decode('utf-8',errors='replace')
    if not isinstance(description,str): description = str(description)
    if re.search(r'<(?:\w+:)?OME\b',description):
        if '<!DOCTYPE' in description or '<!ENTITY' in description: raise ValueError('OME XMLの外部定義には対応していません')
        root = ET.fromstring(description.strip('\x00 \r\n'))
        pixels = [node for node in root.iter() if node.tag.rsplit('}',1)[-1]=='Pixels']
        if len(pixels)!=1: raise ValueError('OME TIFFの複数シリーズの画素サイズは自動選択できません')
        attributes = pixels[0].attrib
        if 'PhysicalSizeX' in attributes or 'PhysicalSizeY' in attributes:
            if 'PhysicalSizeX' not in attributes or 'PhysicalSizeY' not in attributes:
                raise ValueError('OME TIFFのXまたはYの画素サイズが不足しています')
            xy = checked_xy([float(attributes['PhysicalSize'+axis])*unit_factor(attributes.get('PhysicalSize'+axis+'Unit','µm'),factors) for axis in ('X','Y')])
            return {'pixel_size_xy_um':xy,'metadata_kind':'OME PhysicalSizeX/Y','original_unit':[attributes.get('PhysicalSize'+axis+'Unit','µm') for axis in ('X','Y')]}
    properties = {}
    for line in description.splitlines():
        if '=' in line:
            key,value = line.split('=',1); properties[key.strip().lower()] = value.strip().strip('\x00')
    explicit_unit = properties.get('unit')
    if explicit_unit and explicit_unit.lower() in ('pixel','pixels','px'): return None
    if explicit_unit:
        factor = unit_factor(explicit_unit,factors)
        kind,unit = 'ImageJ unit + X/YResolution',explicit_unit
    else:
        resolution_unit = int(image.tag_v2.get(296,1))
        if resolution_unit not in (2,3): return None
        factor,unit = (25400.,'inch') if resolution_unit==2 else (10000.,'cm')
        kind = 'TIFF X/YResolution + ResolutionUnit'
    x,y = image.tag_v2.get(282),image.tag_v2.get(283)
    if x is None or y is None: return None
    density = checked_xy([float(x),float(y)])
    return {'pixel_size_xy_um':checked_xy([factor/density[0],factor/density[1]]),
            'metadata_kind':kind,'original_unit':unit,'pixels_per_unit_xy':density}


def detect_tiff_calibration(paths,factors):
    missing = {'status':'missing','source':None,'csv_path':None,
               'message':'CSV・TIFFの画素サイズ情報なし: X・Yを手入力してください。'}
    if not paths: return missing
    records = []
    try:
        for path in paths:
            if path.suffix.lower() not in ('.tif','.tiff'): continue
            with Image.open(path) as image:
                first = read_page_calibration(image,factors)
                if first is None: continue
                xy = first['pixel_size_xy_um']
                for page in range(1,image.n_frames):
                    image.seek(page)
                    current = read_page_calibration(image,factors)
                    if current is not None and not all(math.isclose(a,b,rel_tol=1e-6,abs_tol=1e-12) for a,b in zip(xy,current['pixel_size_xy_um'])):
                        raise ValueError(f'TIFFのページ間で画素サイズが異なります: {path.name}')
                records.append({'file':path.name,'path':str(path.resolve()),**first})
        if not records: return missing
        if len(records)!=len(paths):
            return {**missing,'images':records,'message':'一部の画像に画素サイズ情報がありません。共通のX・Yを手入力してください。'}
        xy = records[0]['pixel_size_xy_um']
        if any(not all(math.isclose(a,b,rel_tol=1e-6,abs_tol=1e-12) for a,b in zip(xy,r['pixel_size_xy_um'])) for r in records):
            raise ValueError('TIFF画像ごとに画素サイズが異なります。共通スケールの画像を使用してください')
        return {'status':'loaded','source':'TIFF','csv_path':None,'spacing_xy_um':xy,'images':records,
                'message':f'TIFF情報読込: {len(records)}枚\nX {xy[0]:.12g} / Y {xy[1]:.12g} µm/px\n切片間隔Zを入力してください。'}
    except (OSError,ValueError,TypeError,ZeroDivisionError,ET.ParseError) as exc:
        return {'status':'error','source':'TIFF','csv_path':None,'message':f'TIFF画素サイズ情報エラー: {exc}'}
