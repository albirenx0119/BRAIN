# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Session-local low-resolution display cache; never used for registration."""
from collections import OrderedDict
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image, ImageOps
from image_sources import is_tiff,describe,read_page,display_rgb,image_range

MAX_SIDE = 768


class PreviewCache:
    def __init__(self, root, paths):
        self.root = Path(root).resolve()
        self.paths = tuple(Path(p) for p in paths)
        self.temporary = tempfile.TemporaryDirectory(prefix='.preview-cache-', dir=self.root)
        self.directory = Path(self.temporary.name).resolve()
        if self.directory.parent != self.root:
            raise ValueError('Invalid preview cache directory')
        self.entries = {}
        self.decoded = OrderedDict()
        self.results = OrderedDict()
        self.page_counts = {}
        self.page_ranges = {}

    def prepare(self, index):
        path = self.paths[index]
        try:
            stat = path.stat()
            fingerprint = (stat.st_size, stat.st_mtime_ns)
            if is_tiff(path):
                info = describe(path)
                self.page_counts[index] = info['pages']
                for page in range(info['pages']):
                    with read_page(path,page) as source:
                        stride = max(1,int(np.ceil(max(source.size)/MAX_SIDE)))
                        original = np.asarray(source)
                        self.page_ranges[index,page] = image_range(original)
                        pixels = np.array(original[::stride,::stride],copy=True)
                        del original
                        np.save(self.directory/f'{index}_{page}.npy',pixels,allow_pickle=False)
                        if page==0:
                            with Image.fromarray(pixels) as small:
                                with display_rgb(small) as rgb: rgb.save(self.directory/f'{index}.jpg',quality=75)
                self.entries[index] = (info['size'],info['modes'][0],fingerprint,None)
                old = self.decoded.pop(index,None)
                if old is not None: old.close()
                return
            self.page_counts[index] = 1
            with Image.open(path) as source:
                if source.format not in ('PNG', 'JPEG'):
                    raise ValueError('PNG / JPEG形式の画像ではありません')
                size, mode = source.size, source.mode
                if source.getexif().get(274, 1) in (5, 6, 7, 8):
                    size = (size[1], size[0])
                # JPEG draft reduces decode work without touching the source file.
                source.draft('RGB', (MAX_SIDE, MAX_SIDE))
                converted = source.convert('RGBA') if source.mode not in ('RGB', 'RGBA', 'L', 'LA', 'P') else None
                working = converted if converted is not None else source
                working.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.BILINEAR)
                with ImageOps.exif_transpose(working) as oriented:
                    rgba = oriented.convert('RGBA')
                if converted is not None:
                    converted.close()
                rgb = Image.new('RGB', rgba.size, 'white')
                rgb.paste(rgba, mask=rgba.getchannel('A'))
                rgba.close()
                rgb.save(self.directory / f'{index}.jpg', quality=75)
                rgb.close()
            self.entries[index] = (size, mode, fingerprint, None)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            self.entries[index] = (None, None, None, str(exc))
        old = self.decoded.pop(index, None)
        if old is not None:
            old.close()

    def get(self, index):
        if index not in self.entries:
            self.prepare(index)
        size, mode, fingerprint, error = self.entries[index]
        if error:
            raise ValueError(error)
        stat = self.paths[index].stat()
        if fingerprint != (stat.st_size, stat.st_mtime_ns):
            self.prepare(index)
            return self.get(index)
        if index not in self.decoded:
            with Image.open(self.directory / f'{index}.jpg') as image:
                self.decoded[index] = image.copy()
        self.decoded.move_to_end(index)
        while len(self.decoded) > 12:
            _, image = self.decoded.popitem(last=False)
            image.close()
        return self.decoded[index].copy(), size

    def get_plane(self,index,page):
        if index not in self.entries: self.prepare(index)
        if self.entries[index][3]: raise ValueError(self.entries[index][3])
        stat = self.paths[index].stat()
        if self.entries[index][2]!=(stat.st_size,stat.st_mtime_ns):
            raise ValueError('元TIFFが変更されました。Go analyzeで読み込み直してください')
        return np.load(self.directory/f'{index}_{page}.npy',allow_pickle=False)

    def render(self, index, matrix, canvas):
        thumbnail, original_size = self.get(index)
        key = (index, tuple(np.asarray(matrix).ravel()), tuple(canvas), self.entries[index][2])
        if key in self.results:
            thumbnail.close()
            self.results.move_to_end(key)
            return self.results[key].copy()
        w, h = canvas
        scale = min(1., MAX_SIDE/max(w, h))
        sw, sh = original_size
        pad = np.array([[1., 0., (w-sw)//2], [0., 1., (h-sh)//2], [0., 0., 1.]])
        source_scale = np.diag([sw/thumbnail.width, sh/thumbnail.height, 1.])
        output_scale = np.diag([scale, scale, 1.])
        transform = output_scale @ matrix @ pad @ source_scale
        coeff = tuple(np.linalg.inv(transform)[:2].ravel())
        size = (max(1, int(np.ceil(w*scale))), max(1, int(np.ceil(h*scale))))
        result = thumbnail.transform(size, Image.Transform.AFFINE, coeff, Image.Resampling.BILINEAR, fillcolor='white')
        thumbnail.close()
        self.results[key] = result
        while len(self.results) > 12:
            _, image = self.results.popitem(last=False)
            image.close()
        return result.copy()

    def clear_results(self):
        for image in self.results.values():
            image.close()
        self.results.clear()

    def close(self):
        for image in self.decoded.values():
            image.close()
        self.decoded.clear()
        self.clear_results()
        # Recursive cleanup is restricted to this generated session directory.
        if self.directory.parent != self.root or not self.directory.name.startswith('.preview-cache-'):
            raise ValueError('Invalid preview cache cleanup target')
        self.temporary.cleanup()
