# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Read per-image spatial calibration exported by an ImageJ macro."""
import csv
import math
from pathlib import Path
from PIL import Image
from tiff_calibration import detect_tiff_calibration


REQUIRED = {'png_file','pixel_width','pixel_height','unit'}
FACTORS = {'um':1.,'micron':1.,'microns':1.,'micrometer':1.,'micrometers':1.,
           'micrometre':1.,'micrometres':1.,'nm':.001,'mm':1000.,'cm':10000.,'m':1000000.,
           'inch':25400.,'inches':25400.,'in':25400.,'meter':1000000.,'metre':1000000.}


def detect_calibration(folder,paths):
    """Prefer the latest pixel_sizes*.csv; never silently use an invalid scale."""
    result = {'status':'missing','csv_path':None,'message':'CSVなし: X・Yの画素サイズを手入力してください。'}
    try:
        files = [p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower()=='.csv']
        preferred = [p for p in files if p.stem.lower()=='pixel_sizes' or p.stem.lower().startswith('pixel_sizes_')]
        candidates = preferred or files
        candidates.sort(key=lambda p:(p.stat().st_mtime_ns,p.name),reverse=True)
        for path in candidates:
            result['csv_path'] = str(path.resolve())
            with path.open(encoding='utf-8-sig',newline='') as stream:
                reader = csv.DictReader(stream)
                if not REQUIRED.issubset(reader.fieldnames or []):
                    if preferred: raise ValueError('必要な列がありません（png_file, pixel_width, pixel_height, unit）')
                    continue
                rows = list(reader)
            selected = {}
            names = {p.name.casefold() for p in paths}
            tiff_names = {p.stem.casefold():p.name.casefold() for p in paths if p.suffix.lower() in ('.tif','.tiff')}
            for row in rows:
                name = (row.get('png_file') or '').strip().casefold()
                if name not in names and Path(name).stem in tiff_names: name = tiff_names[Path(name).stem]
                if name not in names: continue
                if name in selected: raise ValueError(f'画像名が重複しています: {row["png_file"]}')
                selected[name] = row
            if not preferred and not selected: continue
            records = []
            for image_path in paths:
                row = selected.get(image_path.name.casefold())
                if row is None: raise ValueError(f'画素サイズがありません: {image_path.name}')
                if row.get('calibration_status','calibrated').strip().lower()!='calibrated':
                    raise ValueError(f'未校正の画像です: {image_path.name}')
                unit = row['unit'].strip().lower().replace('µ','u').replace('μ','u')
                if unit not in FACTORS: raise ValueError(f'長さの単位を認識できません: {row["unit"]}')
                xy = [float(row[key])*FACTORS[unit] for key in ('pixel_width','pixel_height')]
                if any(not math.isfinite(v) or v<=0 for v in xy): raise ValueError('画素サイズは正の有限値が必要です')
                if row.get('width_px') and row.get('height_px'):
                    with Image.open(image_path) as image:
                        if (int(row['width_px']),int(row['height_px']))!=image.size:
                            raise ValueError(f'CSVと画像の画素数が一致しません: {image_path.name}')
                records.append({'file':image_path.name,'pixel_size_xy_um':xy,'original_unit':row['unit']})
            if not records: raise ValueError('対象画像がありません')
            xy = records[0]['pixel_size_xy_um']
            if any(not all(math.isclose(a,b,rel_tol=1e-8,abs_tol=1e-12) for a,b in zip(xy,r['pixel_size_xy_um'])) for r in records):
                raise ValueError('画像ごとに画素サイズが異なります。共通スケールの画像を使用してください')
            return {'status':'loaded','source':'CSV','csv_path':str(path.resolve()),'spacing_xy_um':xy,'images':records,
                    'message':f'CSV読込: {path.name}\nX {xy[0]:.12g} / Y {xy[1]:.12g} µm/px\n切片間隔Zを入力してください。'}
        return detect_tiff_calibration(paths,FACTORS)
    except (OSError,UnicodeError,csv.Error,ValueError,TypeError) as exc:
        result.update(status='error',message=f'画素サイズCSVエラー: {exc}')
        return result
