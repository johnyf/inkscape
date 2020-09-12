#!/usr/bin/env python3
#
# Based on:
# https://github.com/johnbartholomew/svg2latex/blob/
# b77623b617b9b92c131a8eafe09ec1b1abed93f2/svg2latex.py
#
# BSD 3-Clause License
#
# Copyright (c) 2017, California Institute of Technology
# Copyright (c) 2017, John Bartholomew
# Copyright (c) 2017, Ioannis Filippidis
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
#    Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#    Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
"""Export SVG to PDF + LaTeX."""
import argparse
import math
import os
import pprint
import re
import subprocess
import sys
import shutil
import tempfile

import cairosvg
import lxml.etree as etree


FONT_MAP = {
    'CMU Serif': 'rm',
    'CMU Sans Serif': 'sf',
    'CMU Typewriter Text': 'tt',
    'Calibri': 'rm'}

FONT_SIZE_MAP = {
    '9px': r'\scriptsize',
    '10px': r'\footnotesize',
    '11px': r'\small',
    '12px': r'\normalsize',
    '13px': r'\large'}

# 72 big-points (PostScript points) (72 bp) per inch,
# 90 SVG "User Units" (90 px) per inch
DPI = 90
SVG_UNITS_TO_BIG_POINTS = 72.0 / DPI

PICTURE_PREAMBLE = r"""% Picture generated by svglatex
\makeatletter
\providecommand\color[2][]{%
  \errmessage{(svglatex) Color is used for the text in Inkscape,
    but the package 'color.sty' is not loaded}%
  \renewcommand\color[2][]{}}%
\providecommand\transparent[1]{%
  \errmessage{
    (svglatex) Transparency is used for the text in Inkscape,
    but the package 'transparent.sty' is not loaded}%
  \renewcommand\transparent[1]{}}%
\setlength{\unitlength}{\svgwidth}%
\global\let\svgwidth\undefined%
\makeatother
"""

ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2

WEIGHT_NORMAL = 500
WEIGHT_BOLD = 700

STYLE_NORMAL = 0
STYLE_ITALIC = 1
STYLE_OBLIQUE = 2

TEXTEXT_NS = r"http://www.iki.fi/pav/software/textext/"
TEXTEXT_PREFIX = '{' + TEXTEXT_NS + '}'
INKSVG_NAMESPACES = {
    'dc': r"http://purl.org/dc/elements/1.1/",
    'cc': r"http://creativecommons.org/ns#",
    'rdf': r"http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    'svg': r"http://www.w3.org/2000/svg",
    'textext': TEXTEXT_NS,
    'xlink': r"http://www.w3.org/1999/xlink",
    'sodipodi': (r"http://sodipodi.sourceforge.net/"
                 r"DTD/sodipodi-0.dtd"),
    'inkscape': r"http://www.inkscape.org/namespaces/inkscape",
}

RX_TRANSFORM = re.compile('^\s*(\w+)\(([0-9,\s\.-]*)\)\s*$')


class AffineTransform(object):

    def __init__(s, t=None, m=None):
        s.t = (0.0, 0.0) if t is None else t
        s.m = (1.0, 0.0, 0.0, 1.0) if m is None else m

    def clone(s):
        nt = AffineTransform()
        nt.t = s.t
        nt.m = s.m
        return nt

    def translate(s, tx, ty):
        s.matrix(1.0, 0.0, 0.0, 1.0, tx, ty)

    def rotate_degrees(s, angle, cx=0.0, cy=0.0):
        angle = math.radians(angle)
        sin, cos = math.sin(angle), math.cos(angle)
        if cx != 0.0 or cy != 0.0:
            s.translate(cx, cy)
            s.matrix(cos, sin, -sin, cos, 0.0, 0.0)
            s.translate(-cx, -cy)
        else:
            s.matrix(cos, sin, -sin, cos, 0.0, 0.0)

    def scale(s, sx, sy=None):
        if sy is None:
            sy = sx
        s.matrix(sx, 0.0, 0.0, sy)

    def matrix(s, a, b, c, d, e=0.0, f=0.0):
        sa, sb, sc, sd = s.m
        se, sf = s.t

        ma = sa * a + sc * b
        mb = sb * a + sd * b
        mc = sa * c + sc * d
        md = sb * c + sd * d
        me = sa * e + sc * f + se
        mf = sb * e + sd * f + sf
        s.m = (ma, mb, mc, md)
        s.t = (me, mf)

    def applyTo(s, x, y=None):
        if y is None:
            x, y = x
        xx = s.t[0] + s.m[0] * x + s.m[2] * y
        yy = s.t[1] + s.m[1] * x + s.m[3] * y
        return (xx, yy)

    def __str__(s):
        return '[{},{},{}  ;  {},{},{}]'.format(
            s.m[0], s.m[2], s.t[0], s.m[1], s.m[3], s.t[1])

    def __mul__(a, b):
        a11, a21, a12, a22 = a.m
        a13, a23 = a.t
        b11, b21, b12, b22 = b.m
        b13, b23 = b.t

        # cIJ = aI1*b1J + aI2*b2J + aI3*b3J
        c11 = a11 * b11 + a12 * b21
        c12 = a11 * b12 + a12 * b22
        c13 = a11 * b13 + a12 * b23 + a13
        c21 = a21 * b11 + a22 * b21
        c22 = a21 * b12 + a22 * b22
        c23 = a21 * b13 + a22 * b23 + a23
        return AffineTransform((c13, c23), (c11, c21, c12, c22))

    def get_rotation(s):
        m11, m21, m12, m22 = s.m
        len1 = math.sqrt(m11 * m11 + m21 * m21)
        len2 = math.sqrt(m12 * m12 + m22 * m22)
        # TODO check that len1 and len2 are close to 1
        # TODO check that the matrix is orthogonal
        # TODO do a real matrix decomposition here!
        return math.degrees(math.atan2(m21, m11))


class RawTeXLabel(object):

    def __init__(s, pos, texcode):
        s.pos = pos
        s.code = texcode

    def texcode(s):
        return (
            '\\scalebox{' + str(SVG_UNITS_TO_BIG_POINTS) +
            '}{\\makebox(0,0)[bl]{%\n' + s.code + '%\n}}')


class TeXLabel(object):

    def __init__(s, pos, text):
        s.text = text
        s.color = (0, 0, 0)
        s.pos = pos
        s.angle = 0.0
        s.align = ALIGN_LEFT
        s.fontsize = None
        s.fontfamily = 'rm'
        s.fontweight = WEIGHT_NORMAL
        s.fontstyle = STYLE_NORMAL
        s.scale = 1.0

    def texcode(s):
        font, color, align = '', '', ''

        r, g, b = s.color
        if (r != 0) or (g != 0) or (b != 0):
            color = '\\color[RGB]{{{},{},{}}}'.format(r, g, b)

        font = '\\' + s.fontfamily + 'family'
        if s.fontweight >= WEIGHT_BOLD:
            font = font + r'\bfseries'
        if s.fontstyle == STYLE_ITALIC:
            font = font + r'\itshape'
        elif s.fontstyle == STYLE_OBLIQUE:
            font = font + r'\slshape'
        if s.fontsize is not None:
            font = font + s.fontsize

        if s.align == ALIGN_LEFT:
            align = r'\makebox(0,0)[bl]'
        elif s.align == ALIGN_CENTER:
            align = r'\makebox(0,0)[b]'
        elif s.align == ALIGN_RIGHT:
            align = r'\makebox(0,0)[br]'

        if s.text is None:
            s.text = ''
        texcode = font + color + align + r'{\smash{' + s.text + '}}'

        if s.angle != 0.0:
            texcode = '\\rotatebox{{{}}}{{{}}}'.format(
                s.angle, texcode)

        return texcode


class TeXPicture(object):

    def __init__(self, xmin, xmax, ymin, ymax,
                 xpdf, ypdf):
        self.xmin = xmin
        self.xmax = xmax
        self.ymin = ymin
        self.ymax = ymax
        self.xpdf = xpdf
        self.ypdf = ypdf
        self.backgroundGraphic = None
        self.labels = list()

    def emit_picture(self, stream, wpdf):
        w = self.xmax - self.xmin
        h = self.ymax - self.ymin
        c = list()
        if self.backgroundGraphic is not None:
            ypdf = w - self.ypdf + self.ymin
            s = (
                '\\put({x}, {y}){{'
                '\\includegraphics[width=\\unitlength]{{{img}}}'
                '}}%').format(
                    x=self.xpdf / w,
                    y=ypdf / w,
                    width=wpdf,
                    img=self.backgroundGraphic)
            c.append(s)
        for label in self.labels:
            x, y = label.pos
            y = w - y + self.ymin
            s = '\\put({x}, {y}){{{text}}}%'.format(
                x=round(x, 3) / w,
                y=round(y, 3) / w,
                text=label.texcode())
            c.append(s)
        s = (
            '\\begingroup%\n' +
            PICTURE_PREAMBLE +
            ('\\begin{{picture}}'
             '({width}, {height})'
             '({xmin}, {ymin})%\n').format(
                xmin=self.xmin / w,
                ymin=0,
                width=1,
                height=h / w) +
            '\n'.join(c) + '\n' +
            '\\end{picture}%\n'
            '\\endgroup%\n')
        stream.write(s)

    def add_label(self, label):
        self.labels.append(label)


def parse_svg_transform(attribute):
    m = RX_TRANSFORM.match(attribute)
    if m is None:
        raise Exception('bad transform (' + attribute + ')')
    func = m.group(1)
    args = [float(x.strip()) for x in m.group(2).split(',')]
    xform = AffineTransform()
    if func == 'matrix':
        if len(args) != 6:
            raise Exception('bad matrix transform')
        xform.matrix(*args)
    elif func == 'translate':
        if len(args) < 1 or len(args) > 2:
            raise Exception('bad translate transform')
        tx = args[0]
        ty = args[1] if len(args) > 1 else 0.0
        xform.translate(tx, ty)
    elif func == 'scale':
        if len(args) < 1 or len(args) > 2:
            raise Exception('bad scale transform')
        sx = args[0]
        sy = args[1] if len(args) > 1 else sx
        xform.scale(sx, sy)
    else:
        raise Exception(
            'unsupported transform attribute ({a})'.format(
                a=attribute))
    return xform


def split_svg_style(style):
    parts = [x.strip() for x in style.split(';')]
    parts = [x.partition(':') for x in parts if x != '']
    st = {}
    for p in parts:
        st[p[0].strip()] = p[2].strip()
    return st


def parse_svg_color(col):
    if col[0] == '#':
        r = int(col[1:3], 16)
        g = int(col[3:5], 16)
        b = int(col[5:7], 16)
        return (r, g, b)
    else:
        raise Exception('only hash-code colors are supported!')


def compute_svg_transform(el):
    xform = AffineTransform()
    while el is not None:
        if 'transform' in el.attrib:
            t = parse_svg_transform(el.attrib['transform'])
            xform = t * xform
        el = el.getparent()
    return xform


def interpret_svg_text(textEl, labels):
    style = split_svg_style(
        textEl.attrib['style']) if 'style' in textEl.attrib else {}
    text_ids = set()
    name = textEl.attrib['id']
    text_ids.add(name)
    all_text = list()
    xys = list()
    for tspan in textEl.xpath(
        'svg:tspan', namespaces=INKSVG_NAMESPACES):
        span_style = style.copy()
        if 'style' in tspan.attrib:
            span_style.update(split_svg_style(tspan.attrib['style']))
        xform = compute_svg_transform(tspan)
        pos = (float(tspan.attrib['x']), float(tspan.attrib['y']))
        pos = xform.applyTo(pos)
        xys.append(pos)
        # name = tspan.attrib['id']
        # text_ids.add(name)
        angle = -round(xform.get_rotation(), 3)
        all_text.append(tspan.text)
        texLabel = TeXLabel(pos, '')
        texLabel.angle = angle
        if 'fill' in span_style:
            texLabel.color = parse_svg_color(span_style['fill'])
        if 'font-weight' in span_style:
            weight = span_style['font-weight']
            if weight == 'bold':
                texLabel.fontweight = WEIGHT_BOLD
            elif weight == 'normal':
                texLabel.fontweight = WEIGHT_NORMAL
            else:
                texLabel.fontweight = int(weight)
        if 'font-style' in span_style:
            fstyle = span_style['font-style']
            if fstyle == 'normal':
                texLabel.fontstyle = STYLE_NORMAL
            elif fstyle == 'italic':
                texLabel.fontstyle = STYLE_ITALIC
            elif fstyle == 'oblique':
                texLabel.fontstyle = STYLE_OBLIQUE
        if 'text-anchor' in span_style:
            anchor = span_style['text-anchor']
            if anchor == 'start':
                texLabel.align = ALIGN_LEFT
            elif anchor == 'end':
                texLabel.align = ALIGN_RIGHT
            elif anchor == 'middle':
                texLabel.align = ALIGN_CENTER
        if 'font-family' in span_style:
            ff = span_style['font-family']
            if ff in FONT_MAP:
                texLabel.fontfamily = FONT_MAP[ff]
            else:
                print('Could not match font-family', ff)
        if 'font-size' in span_style:
            fs = span_style['font-size']
            if fs in FONT_SIZE_MAP:
                texLabel.fontsize = FONT_SIZE_MAP[fs]
            else:
                print('Could not match font-size', fs)
    all_text = [s for s in all_text if s is not None]
    texLabel.text = ' '.join(all_text)
    texLabel.pos = xys[0]
    labels.append(texLabel)
    return text_ids


def interpret_svg_textext(textEl, labels):
    texcode = textEl.attrib[TEXTEXT_PREFIX + 'text'].encode(
        'utf-8').decode('unicode_escape')
    xform = compute_svg_transform(textEl)
    placedElements = textEl.xpath(
        r'.//svg:use', namespaces=INKSVG_NAMESPACES)
    if len(placedElements):
        minX = 1e20
        maxY = -1e20
        for el in placedElements:
            elPos = (float(el.attrib['x']), float(el.attrib['y']))
            elPos = xform.applyTo(elPos)
            x, y = elPos
            if x < minX:
                minX = x
            if y > maxY:
                maxY = y
        pos = (minX, maxY)
    else:
        pos = (0.0, 0.0)
    labels.append(RawTeXLabel(pos, texcode))


def svg_bounding_boxes(svgfile):
    """Parses the output from inkscape --query-all"""
    cmd = [
        'inkscape',
        '--without-gui',
        '--query-all',
        svgfile]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)
    lines = p.stdout.readlines()
    bboxes = dict()
    for line in lines:
        name, x, y, w, h = parse_line(line)
        bboxes[name] = dict(x=x, y=y, w=w, h=h)
    return bboxes


def parse_line(line):
    split = line.split(',')
    name = split[0]
    x, y, w, h = [float(x) for x in split[1:]]
    return name, x, y, w, h


def mm_to_svg_untis(x):
    if 'mm' in x:
        s = x[:-2]
        return float(s) / 25.4 * DPI
    else:
        return float(x)


def process_svg(inpath):
    doc = etree.parse(inpath)
    w = mm_to_svg_untis(doc.getroot().attrib['width'])
    h = mm_to_svg_untis(doc.getroot().attrib['height'])
    print('width = {w:0.2f} px, height = {h:0.2f} px'.format(
        w=w, h=h))
    w_inch = w / DPI
    h_inch = h / DPI
    print('width = {w:0.2f} in, height = {h:0.2f} in'.format(
        w=w_inch, h=h_inch))
    w_bp = w * SVG_UNITS_TO_BIG_POINTS
    h_bp = h * SVG_UNITS_TO_BIG_POINTS
    print('width = {w:0.2f} bp, height = {h:0.2f} bp'.format(
        w=w_bp, h=h_bp))
    text = doc.xpath(
        '//svg:text', namespaces=INKSVG_NAMESPACES)
    textext = doc.xpath(
        '//*[@textext:text]', namespaces=INKSVG_NAMESPACES)
    ignore_ids = set()
    for defs in doc.xpath(
            '//svg:defs', namespaces=INKSVG_NAMESPACES):
        for u in defs.xpath(
                '//svg:path', namespaces=INKSVG_NAMESPACES):
            name = u.attrib['id']
            ignore_ids.add(name)
    # extract text and remove it from svg
    text_ids = set()
    labels = list()
    for u in text:
        ids = interpret_svg_text(u, labels)
        text_ids.update(ids)
        parent = u.getparent()
        parent.remove(u)
    for u in textext:
        interpret_svg_textext(u, labels)
        parent = u.getparent()
        parent.remove(u)
    return doc, text_ids, ignore_ids, labels


def main(svg_fname):
    fname = os.path.splitext(svg_fname)[0]
    texpath = '{fname}.pdf_tex'.format(fname=fname)
    pdfpath = '{fname}.pdf'.format(fname=fname)
    # convert
    xml, text_ids, ignore_ids, labels = process_svg(svg_fname)
    pdf_bboxes = generate_pdf_from_svg_using_inkscape(xml, pdfpath)
    # get bounding boxes
    xs = set()
    ys = set()
    bboxes = svg_bounding_boxes(svg_fname)
    pprint.pprint(bboxes)
    print(text_ids)
    for name in text_ids:
        d = bboxes[name]
        if name in ignore_ids:
            continue
        x, _, y, _ = corners(d)
        xs.add(x)
        ys.add(y)
        if name not in text_ids:
            xs.add(x + w)
            ys.add(y + h)
    d = pdf_bboxes.get('svg2')
    xmin, xmax, ymin, ymax = corners(d)
    xs.add(xmin)
    xs.add(xmax)
    ys.add(ymin)
    ys.add(ymax)
    x_pdf = xmin
    y_pdf = ymax
    x_min = min(xs)
    x_max = max(xs)
    y_min = min(ys)
    y_max = max(ys)
    print(('x_min = {x_min}, x_max = {x_max}\n'
           'y_min = {y_min}, y_max = {y_max}\n').format(
               x_min=x_min, x_max=x_max,
               y_min=y_min, y_max=y_max))
    tex = TeXPicture(x_min, x_max, y_min, y_max, x_pdf, y_pdf)
    tex.labels = labels
    tex.backgroundGraphic = pdfpath
    with open(texpath, 'w', encoding='utf-8') as f:
        tex.emit_picture(f, xmax - xmin)


def generate_pdf_from_svg_using_cairo(svgData, pdfpath):
    with tempfile.NamedTemporaryFile(
            suffix='.svg', delete=True) as tmpsvg:
        svgData.write(tmpsvg, encoding='utf-8',
                      xml_declaration=True)
        tmpsvg.flush()
        bboxes = svg_bounding_boxes(tmpsvg.name)
        shutil.copyfile(tmpsvg.name, 'foo_bare.svg')
        cairosvg.svg2pdf(
            file_obj=tmpsvg,
            write_to=pdfpath)
    return bboxes


def generate_pdf_from_svg_using_inkscape(svgData, pdfpath):
    args = ['/usr/bin/inkscape',
            '--without-gui',
            '--export-area-drawing',
            '--export-ignore-filters',
            '--export-dpi={dpi}'.format(dpi=DPI),
            '--export-pdf={path}'.format(path=pdfpath)]
    with tempfile.NamedTemporaryFile(
            suffix='.svg', delete=True) as tmpsvg:
        svgData.write(tmpsvg, encoding='utf-8',
                      xml_declaration=True)
        tmpsvg.flush()
        bboxes = svg_bounding_boxes(tmpsvg.name)
        shutil.copyfile(tmpsvg.name, 'foo_bare.svg')
        args.append(tmpsvg.name)
        with subprocess.Popen(args) as proc:
            proc.wait()
            if proc.returncode != 0:
                sys.stderr.write('inkscape svg->pdf failed')
    return bboxes


def corners(d):
    x = d['x']
    y = d['y']
    w = d['w']
    h = d['h']
    xmax = x + w
    ymax = y + h
    return x, xmax, y, ymax


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('fname', type=str, help='svg file name')
    args = p.parse_args()
    return args


if __name__ == '__main__':
    args = parse_args()
    main(args.fname)
