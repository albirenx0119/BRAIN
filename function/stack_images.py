# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Disk-backed aligned image stacks, preserving TIFF sample types and pages."""
import json
import shutil
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
from alignment import check, read_rgb
from image_sources import describe,render_native,image_range


class AlignedTiffStack:
    """Reuse loaded TIFF previews; resolve full-size samples only when needed."""
    def __init__(self,paths,matrices,canvas,cache,provenance):
        self.paths,self.matrices,self.canvas,self.cache = tuple(paths),[m.copy() for m in matrices],tuple(canvas),cache
        self.array = self
        first = cache.get_plane(0,0)
        self.shape = (len(paths),cache.page_counts[0],canvas[1],canvas[0])+first.shape[2:]
        self.dtype = first.dtype
        self.params = {'format':'BRAIN aligned TIFF source','canvas_xy':list(canvas),'analysis':provenance,
                       'aligned':provenance.get('alignment') is not None,
                       'images':[{'stack_image':str(path),'alignment_image':str(path),'final_matrix':m.tolist()}
                                 for path,m in zip(paths,self.matrices)]}

    def preview(self,index,page):
        pixels = self.cache.get_plane(index,page)
        sw,sh = self.cache.entries[index][0]
        scale = min(1.,768/max(self.canvas))
        pad = np.array([[1.,0.,(self.canvas[0]-sw)//2],[0.,1.,(self.canvas[1]-sh)//2],[0.,0.,1.]])
        transform = np.diag([scale,scale,1.]) @ self.matrices[index] @ pad @ np.diag([sw/pixels.shape[1],sh/pixels.shape[0],1.])
        inv = np.linalg.inv(transform)
        with Image.fromarray(pixels) as source:
            working = source.convert('I') if source.mode.startswith('I;16') else source
            try:
                with working.transform((int(np.ceil(self.canvas[0]*scale)),int(np.ceil(self.canvas[1]*scale))),
                    Image.Transform.AFFINE,tuple(inv[:2].ravel()),Image.Resampling.BILINEAR,fillcolor=0) as image:
                    return np.array(image)
            finally:
                if working is not source: working.close()

    def page_range(self,index,page):
        return self.cache.page_ranges[index,page]

    def __getitem__(self,key):
        index,page,*slices = key
        with render_native(self.paths[index],page,self.matrices[index],self.canvas) as image:
            data = np.array(image)
        return np.array(data[tuple(slices)],copy=True) if slices else data

    def close(self):
        self.cache = None


class ImageStack:
    def __init__(self,directory,array,params):
        self.directory,self.array,self.params = directory,array,params

    def close(self):
        if self.array is not None:
            self.array.flush()
            self.array._mmap.close()
            self.array = None
        self.directory.cleanup()


def match_tiffs(paths,folder):
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir(): raise ValueError('TIFFのフォルダを選択してください')
    candidates = {}
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in ('.tif','.tiff'):
            candidates.setdefault(path.stem.casefold(),[]).append(path)
    matched = []
    for path in paths:
        found = candidates.get(path.stem.casefold(),[])
        if not found: raise ValueError(f'同名TIFFがありません: {path.stem}')
        if len(found)!=1: raise ValueError(f'同名TIFFが複数あります: {path.stem}')
        matched.append(found[0])
    return matched


def inspect_tiffs(paths,folder):
    """Compare full file sets without assigning a transform by position."""
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir(): raise ValueError('TIFFのフォルダを選択してください')
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.tif','.tiff')),key=lambda p:p.name.casefold())
    candidates = {}
    for path in files: candidates.setdefault(path.stem.casefold(),[]).append(path)
    expected = {}
    for i,path in enumerate(paths): expected.setdefault(path.stem.casefold(),[]).append(i)
    missing,extra,duplicates,indices = [],[],[],[]
    for name,positions in expected.items():
        found = candidates.get(name,[])
        if not found: missing.extend(paths[i].name for i in positions)
        elif len(found)!=1 or len(positions)!=1:
            duplicates.append(' / '.join([paths[i].name for i in positions]+[p.name for p in found]))
        else: indices.append(positions[0])
    extra = [p.name for p in files if p.stem.casefold() not in expected]
    indices.sort()
    return {'folder':str(folder),'expected_count':len(paths),'tiff_count':len(files),
            'matched_indices':indices,'missing':missing,'extra':extra,'duplicates':duplicates,
            'consistent':len(files)==len(paths) and not missing and not extra and not duplicates,
            'reference_names':[p.name for p in paths],'tiff_names':[p.name for p in files]}


def page_type(image):
    if image.mode in ('L','RGB','RGBA'): return np.dtype('uint8'),len(image.getbands())
    if image.mode.startswith('I;16'): return np.dtype('uint16'),1
    if image.mode=='I': return np.dtype('int32'),1
    if image.mode=='F': return np.dtype('float32'),1
    raise ValueError(f'未対応のTIFF画素形式です: {image.mode}')


def build_stack(paths,matrices,canvas,root,tiff_folder=None,provenance=None,cancel=None,progress=None):
    if len(paths)<2 or len(matrices)!=len(paths) or any(m is None for m in matrices):
        raise ValueError('全画像のアライン実行後にスタックしてください')
    canvas = tuple(canvas)
    sources = match_tiffs(paths,tiff_folder) if tiff_folder is not None else list(paths)
    descriptors = []
    for index,(path,source_path) in enumerate(zip(paths,sources)):
        check(cancel)
        if progress: progress(index,len(paths),'確認: '+source_path.name)
        with Image.open(path) as original:
            size = original.size
            if original.getexif().get(274,1) in (5,6,7,8): size = size[::-1]
        if tiff_folder is None:
            descriptors.append((size,1,np.dtype('uint8'),3))
            continue
        with Image.open(source_path) as source:
            describe(source_path)
            if source.format!='TIFF': raise ValueError(f'TIFFではありません: {source_path.name}')
            pages = source.n_frames
            kind = None
            for page in range(pages):
                check(cancel); source.seek(page)
                if source.tag_v2.get(274,1)!=1:
                    raise ValueError(f'TIFFのOrientationは1のみ対応しています: {source_path.name}')
                if source.size!=size: raise ValueError(f'元画像とTIFFの画素数が一致しません: {source_path.name}')
                current = page_type(source)
                if kind is not None and current!=kind: raise ValueError('TIFF内のページ形式が一致しません')
                kind = current
            descriptors.append((size,pages,*kind))
    _,pages,dtype,bands = descriptors[0]
    if any(d[1:]!=descriptors[0][1:] for d in descriptors):
        raise ValueError('全画像でTIFFページ数・画素形式・チャンネル構成をそろえてください')
    shape = (len(paths),pages,canvas[1],canvas[0])+((bands,) if bands>1 else ())
    required = int(np.prod(shape,dtype=np.int64))*dtype.itemsize
    root = Path(root).resolve(strict=True)
    if shutil.disk_usage(root).free<required+256*1024*1024:
        raise ValueError(f'作業ディスクの空き容量が不足しています（スタック約{required/1024**3:.2f} GB）')
    temporary = tempfile.TemporaryDirectory(prefix='.stack-cache-',dir=root)
    folder = Path(temporary.name).resolve()
    if folder.parent!=root: raise ValueError('不正な作業フォルダです')
    array = None
    try:
        array = np.lib.format.open_memmap(folder/'stack.npy',mode='w+',dtype=dtype,shape=shape)
        records = []
        for index,(path,source_path,descriptor,matrix) in enumerate(zip(paths,sources,descriptors,matrices)):
            check(cancel)
            size = descriptor[0]
            matrix = np.asarray(matrix,float)
            if matrix.shape!=(3,3) or not np.all(np.isfinite(matrix)): raise ValueError('変換行列が不正です')
            pad = np.array([[1.,0.,(canvas[0]-size[0])//2],[0.,1.,(canvas[1]-size[1])//2],[0.,0.,1.]])
            inv = np.linalg.inv(matrix @ pad)
            source = Image.open(source_path) if tiff_folder is not None else read_rgb(path)
            page_ranges = []
            try:
                for page in range(pages):
                    check(cancel)
                    if tiff_folder is not None: source.seek(page)
                    # Prepare untransformed inspection previews in the worker.
                    stride = max(1,int(np.ceil(max(source.size)/768)))
                    original = np.asarray(source)
                    page_ranges.append(image_range(original))
                    np.save(folder/f'raw_{index}_{page}.npy',original[::stride,::stride],allow_pickle=False)
                    del original
                    working = source.convert('I') if source.mode.startswith('I;16') else source
                    try:
                        for top in range(0,canvas[1],128):
                            check(cancel)
                            if progress:
                                done = index*pages+page+top/canvas[1]
                                progress(done,len(paths)*pages,f'{index+1}/{len(paths)} · ページ {page+1}/{pages} · {source_path.name}')
                            coeff = (inv[0,0],inv[0,1],inv[0,2]+inv[0,1]*top,
                                     inv[1,0],inv[1,1],inv[1,2]+inv[1,1]*top)
                            with working.transform((canvas[0],min(128,canvas[1]-top)),Image.Transform.AFFINE,coeff,
                                                   Image.Resampling.BILINEAR,fillcolor=0) as block:
                                pixels = np.asarray(block)
                                if dtype==np.dtype('uint16'): pixels = np.clip(pixels,0,65535)
                                array[index,page,top:top+block.height] = pixels.astype(dtype,copy=False)
                    finally:
                        if working is not source: working.close()
            finally: source.close()
            records.append({'index':index+1,'alignment_image':str(path.resolve()),'stack_image':str(source_path.resolve()),
                            'source_size_xy':list(size),'page_ranges':page_ranges,
                            'final_matrix':matrix.tolist(),'source_to_stack_matrix':(matrix @ pad).tolist()})
        check(cancel)
        array.flush()
        params = {'format':'BRAIN image stack','axes':'NPYXS' if bands>1 else 'NPYX',
                  'axis_description':'N: image order; P: original TIFF page order (not inferred as channels); Y/X: original pixels; S: color samples',
                  'shape':list(shape),'dtype':str(dtype),'canvas_xy':list(canvas),'source_mode':'TIFF' if tiff_folder else 'loaded images',
                  'background':0,'images':records,'analysis':provenance or {}}
        (folder/'params.json').write_text(json.dumps(params,ensure_ascii=False,indent=2),encoding='utf-8')
        if progress: progress(len(paths)*pages,len(paths)*pages,'スタック完了')
        return ImageStack(temporary,array,params)
    except BaseException:
        if array is not None: array._mmap.close()
        temporary.cleanup()
        raise
