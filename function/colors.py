# Copyright 2026 Ren Y Sato
# SPDX-License-Identifier: Apache-2.0
"""Shared RGB definitions for display, registration and export."""
import re
import numpy as np

RGB = {'Gray':(255,255,255),'Red':(255,0,0),'Green':(0,255,0),'Blue':(0,0,255),
       'Magenta':(255,0,255),'Cyan':(0,255,255),'Yellow':(255,255,0)}
COLORS = {'Gray':'#666666','Red':'#c62828','Green':'#2e7d32','Blue':'#1565c0',
          'Magenta':'#b000b0','Cyan':'#008b8b','Yellow':'#9b8700'}


def valid_color(value):
    return isinstance(value,str) and (value in RGB or re.fullmatch(r'#[0-9a-fA-F]{6}',value) is not None)


def color_rgb(value):
    if value in RGB: return RGB[value]
    if not valid_color(value): raise ValueError('色はプリセットまたは#RRGGBBで指定してください')
    return tuple(int(value[i:i+2],16) for i in (1,3,5))


def color_hex(value):
    return COLORS[value] if value in COLORS else '#%02X%02X%02X'%color_rgb(value) if valid_color(value) else COLORS['Gray']


def color_weights(value):
    if value=='Gray': return np.array([.2126,.7152,.0722],np.float32)
    weights = np.array(color_rgb(value),np.float32)
    return weights/weights.sum() if weights.sum() else np.array([.2126,.7152,.0722],np.float32)


def text_color(value):
    rgb=color_rgb(color_hex(value))
    return 'black' if np.dot(rgb,[.2126,.7152,.0722])>=160 else 'white'
