# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Native TIFF pages and display conversions shared by the local workflows."""
import numpy as np
from PIL import Image,ImageOps


def is_tiff(path): return str(path).lower().endswith(('.tif','.tiff'))


def image_range(values):
    """Exact finite extrema, without allocating a full-size float mask."""
    if np.issubdtype(values.dtype,np.integer): return float(values.min()),float(values.max())
    low,high = float('inf'),float('-inf')
    rows = max(1,1_000_000//max(1,int(np.prod(values.shape[1:]))))
    for start in range(0,len(values),rows):
        block = values[start:start+rows]
        finite = block[np.isfinite(block)]
        if finite.size: low,high = min(low,float(finite.min())),max(high,float(finite.max()))
    return (low,high) if np.isfinite(low) else (0.,1.)


def describe(path):
    with Image.open(path) as image:
        count = image.n_frames if image.format=='TIFF' else 1
        size = image.size
        if image.format!='TIFF' and image.getexif().get(274,1) in (5,6,7,8): size = size[::-1]
        modes = []
        for page in range(count):
            image.seek(page)
            if image.format=='TIFF':
                bits = image.tag_v2.get(258,8)
                bits = (bits,) if isinstance(bits,int) else bits
                sample_format = image.tag_v2.get(339,1)
                sample_format = (sample_format,) if isinstance(sample_format,int) else sample_format
                if (image.mode in ('RGB','RGBA') and max(bits)>8) or max(bits)>32 or (image.mode=='I' and (max(bits)!=32 or min(sample_format)!=2)):
                    raise ValueError(f'{path.name}: このTIFF画素型は精度を保持して読み込めません。16-bitカラーはチャンネル別ページにしてください')
                if image.tag_v2.get(274,1)!=1: raise ValueError(f'{path.name}: TIFFのOrientationは1のみ対応しています')
                if image.size!=size: raise ValueError(f'{path.name}: TIFFページ間の画像サイズが一致しません')
                if image.mode not in ('L','RGB','RGBA','I','F','I;16','I;16L','I;16B'):
                    raise ValueError(f'{path.name}: 未対応のTIFF画素形式です（{image.mode}）')
            modes.append(image.mode)
        return {'size':size,'pages':count,'modes':modes,'format':image.format}


def read_page(path,page=0):
    with Image.open(path) as source:
        if source.format not in ('PNG','JPEG','TIFF'): raise ValueError('PNG/JPEG/TIFF形式の画像を選択してください')
        if source.format=='TIFF':
            if source.tag_v2.get(274,1)!=1: raise ValueError('TIFFのOrientationは1のみ対応しています')
            source.seek(page)
        with ImageOps.exif_transpose(source) as oriented: return oriented.copy()


def display_array(values,limits=None):
    values = np.asarray(values)
    if limits is None:
        finite = values[np.isfinite(values)]
        low,high = np.percentile(finite,[1,99.5]) if finite.size else (0.,1.)
        if high<=low: low,high = (float(finite.min()),float(finite.max())) if finite.size else (0.,1.)
        if high<=low: low,high = 0.,max(float(high),1.)
    else: low,high = limits
    return np.nan_to_num((values.astype(np.float32)-low)*255/max(float(high-low),1e-12),nan=0,posinf=255,neginf=0).clip(0,255).astype(np.uint8)


def display_rgb(image):
    if image.mode in ('RGB','RGBA','L','LA','P'): return image.convert('RGB')
    return Image.fromarray(display_array(np.asarray(image))).convert('RGB')


def render_native(path,page,matrix,canvas):
    with read_page(path,page) as source:
        native_mode = source.mode
        native_dtype = np.uint16 if native_mode.startswith('I;16') else None
        working = source.convert('I') if native_dtype is not None else source
        try:
            left,top = (canvas[0]-source.width)//2,(canvas[1]-source.height)//2
            inv = np.linalg.inv(matrix)
            coeff = (inv[0,0],inv[0,1],inv[0,2]-left,inv[1,0],inv[1,1],inv[1,2]-top)
            result = working.transform(tuple(canvas),Image.Transform.AFFINE,coeff,Image.Resampling.BILINEAR,fillcolor=0)
            if native_dtype is not None:
                values = np.asarray(result).clip(0,65535).astype(np.uint16)
                result.close(); result = Image.fromarray(values)
            return result
        finally:
            if working is not source: working.close()
