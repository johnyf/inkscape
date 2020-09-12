#!/usr/bin/env python
"""Export SVG to PDF + LaTeX."""
#
# Based on:
# https://github.com/johnbartholomew/svg2latex/blob/
# b77623b617b9b92c131a8eafe09ec1b1abed93f2/svg2latex.py
#
# BSD 3-Clause License
#
# Copyright 2017-2020 by California Institute of Technology
# Copyright (c) 2017, John Bartholomew
# Copyright 2017-2020 by Ioannis Filippidis
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
import argparse
import collections
import math
import os
import pprint
import re
import subprocess
import sys
import shutil
import tempfile

# import cairosvg
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
# 96 SVG "User Units" (96 px) per inch
# https://wiki.inkscape.org/wiki/index.php/Units_In_Inkscape
DPI = 96
SVG_UNITS_TO_BIG_POINTS = 72.0 / DPI
# initial fragment of `*.pdf_tex` file
PICTURE_PREAMBLE = r'''% Picture generated by svglatex
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
'''
# alignments
ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2
# weights
WEIGHT_NORMAL = 500
WEIGHT_BOLD = 700
# styles
STYLE_NORMAL = 0
STYLE_ITALIC = 1
STYLE_OBLIQUE = 2
# namespaces
INKSVG_NAMESPACES = {
    'dc': r'http://purl.org/dc/elements/1.1/',
    'cc': r'http://creativecommons.org/ns#',
    'rdf': r'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'svg': r'http://www.w3.org/2000/svg',
    'xlink': r'http://www.w3.org/1999/xlink',
    'sodipodi': (r'http://sodipodi.sourceforge.net/'
                 r'DTD/sodipodi-0.dtd'),
    'inkscape': r'http://www.inkscape.org/namespaces/inkscape'}
# transform re
RX_TRANSFORM = re.compile('^\s*(\w+)\(([0-9,\s\.-]*)\)\s*$')
# bounding box
BBox = collections.namedtuple('BBox', ['x', 'y', 'width', 'height'])


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('fname', type=str, help='svg file name')
    args = p.parse_args()
    return args


def convert(svg_fname):
    fname, ext = os.path.splitext(svg_fname)
    assert ext == '.svg', ext
    texpath = '{fname}.pdf_tex'.format(fname=fname)
    pdfpath = '{fname}.pdf'.format(fname=fname)
    # convert
    xml, text_ids, ignore_ids, labels = split_text_graphics(svg_fname)
    pdf_bboxes = generate_pdf_from_svg_using_inkscape(xml, pdfpath)
    pdf_bbox = pdf_bounding_box(pdf_bboxes)
    svg_bboxes = svg_bounding_boxes(svg_fname)
    svg_bbox = svg_bounding_box(
        svg_bboxes, text_ids, ignore_ids, pdf_bbox)
    tex = TeXPicture(svg_bbox, pdf_bbox, pdfpath, labels)
    pdf_tex_contents = tex.dumps()
    with open(texpath, 'w', encoding='utf-8') as f:
        f.write(pdf_tex_contents)


def split_text_graphics(svg_fname):
    doc = etree.parse(svg_fname)
    _print_svg_units(doc)
    text = doc.xpath(
        '//svg:text', namespaces=INKSVG_NAMESPACES)
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
    return doc, text_ids, ignore_ids, labels


def _print_svg_units(doc):
    w = mm_to_svg_units(doc.getroot().attrib['width'])
    h = mm_to_svg_units(doc.getroot().attrib['height'])
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


def mm_to_svg_units(x):
    if 'mm' in x:
        s = x[:-2]
        return float(s) / 25.4 * DPI
    else:
        return float(x)


def interpret_svg_text(textEl, labels):
    if 'style' in textEl.attrib:
        style = split_svg_style(
            textEl.attrib['style'])
    else:
        style = dict()
    text_ids = set()
    name = textEl.attrib['id']
    text_ids.add(name)
    all_text = list()
    xys = list()
    for tspan in textEl.xpath(
            'svg:tspan', namespaces=INKSVG_NAMESPACES):
        all_text.append(tspan.text)
        tex_label = _make_tex_label(tspan)
        xys.append(tex_label.pos)
        # name = tspan.attrib['id']
        # text_ids.add(name)
        # style
        span_style = _update_tspan_style(style, tspan)
        _set_fill(tex_label, span_style)
        _set_font_weight(tex_label, span_style)
        _set_font_style(tex_label, span_style)
        _set_text_anchor(tex_label, span_style)
        _set_font_family(tex_label, span_style)
        _set_font_size(tex_label, span_style)
    all_text = [s for s in all_text if s is not None]
    tex_label.text = ' '.join(all_text)
    tex_label.pos = xys[0]
    labels.append(tex_label)
    return text_ids


def _make_tex_label(tspan):
    # position and angle
    pos, angle = _get_tspan_pos_angle(tspan)
    tex_label = TeXLabel(pos, '')
    tex_label.angle = angle
    return tex_label


def _get_tspan_pos_angle(tspan):
    xform = compute_svg_transform(tspan)
    pos = (float(tspan.attrib['x']), float(tspan.attrib['y']))
    pos = xform.applyTo(pos)
    angle = - round(xform.get_rotation(), 3)
    return pos, angle


def _update_tspan_style(style, tspan):
    span_style = style.copy()
    if 'style' in tspan.attrib:
        st = split_svg_style(tspan.attrib['style'])
        span_style.update(st)
    return span_style


def _set_fill(tex_label, span_style):
    if 'fill' not in span_style:
        return
    tex_label.color = parse_svg_color(span_style['fill'])


def _set_font_weight(tex_label, span_style):
    if 'font-weight' not in span_style:
        return
    weight = span_style['font-weight']
    if weight == 'bold':
        tex_label.fontweight = WEIGHT_BOLD
    elif weight == 'normal':
        tex_label.fontweight = WEIGHT_NORMAL
    else:
        tex_label.fontweight = int(weight)


def _set_font_style(tex_label, span_style):
    if 'font-style' not in span_style:
        return
    fstyle = span_style['font-style']
    if fstyle == 'normal':
        tex_label.fontstyle = STYLE_NORMAL
    elif fstyle == 'italic':
        tex_label.fontstyle = STYLE_ITALIC
    elif fstyle == 'oblique':
        tex_label.fontstyle = STYLE_OBLIQUE


def _set_text_anchor(tex_label, span_style):
    if 'text-anchor' not in span_style:
        return
    anchor = span_style['text-anchor']
    if anchor == 'start':
        tex_label.align = ALIGN_LEFT
    elif anchor == 'end':
        tex_label.align = ALIGN_RIGHT
    elif anchor == 'middle':
        tex_label.align = ALIGN_CENTER


def _set_font_family(tex_label, span_style):
    if 'font-family' not in span_style:
        return
    ff = span_style['font-family']
    if ff in FONT_MAP:
        tex_label.fontfamily = FONT_MAP[ff]
    else:
        print('Could not match font-family', ff)


def _set_font_size(tex_label, span_style):
    if 'font-size' not in span_style:
        return
    fs = span_style['font-size']
    if fs in FONT_SIZE_MAP:
        tex_label.fontsize = FONT_SIZE_MAP[fs]
    else:
        print('Could not match font-size', fs)


def split_svg_style(style):
    parts = [x.strip() for x in style.split(';')]
    parts = [x.partition(':') for x in parts if x != '']
    st = dict()
    for p in parts:
        st[p[0].strip()] = p[2].strip()
    return st


def compute_svg_transform(el):
    xform = AffineTransform()
    while el is not None:
        if 'transform' in el.attrib:
            t = parse_svg_transform(el.attrib['transform'])
            xform = t * xform
        el = el.getparent()
    return xform


def parse_svg_transform(attribute):
    m = RX_TRANSFORM.match(attribute)
    assert m is not None, 'bad transform (' + attribute + ')'
    func = m.group(1)
    args = [float(x.strip()) for x in m.group(2).split(',')]
    if func == 'matrix':
        return _make_matrix_transform(args)
    elif func == 'translate':
        return _make_translation_transform(args)
    elif func == 'scale':
        return _make_scaling_transform(args)
    elif func == 'rotate':
        return _make_rotation_transform(args)
    else:
        raise Exception(
            'unsupported transform attribute ({a})'.format(
                a=attribute))


def _make_matrix_transform(args):
    assert len(args) == 6, args
    xform = AffineTransform()
    xform.matrix(*args)
    return xform


def _make_translation_transform(args):
    assert len(args) in (1, 2), args
    tx = args[0]
    ty = args[1] if len(args) > 1 else 0.0
    xform = AffineTransform()
    xform.translate(tx, ty)
    return xform


def _make_scaling_transform(args):
    assert len(args) in (1, 2), args
    sx = args[0]
    sy = args[1] if len(args) > 1 else sx
    xform = AffineTransform()
    xform.scale(sx, sy)
    return xform


def _make_rotation_transform(args):
    assert len(args) in (1, 3), args
    if len(args) == 1:
        args = args + [0, 0]  # cx, cy
    xform = AffineTransform()
    xform.rotate_degrees(*args)
    print('WARNING: text rotation (not tested)')
    return xform


def parse_svg_color(col):
    if col[0] == '#':
        r = int(col[1:3], 16)
        g = int(col[3:5], 16)
        b = int(col[5:7], 16)
        return (r, g, b)
    else:
        raise Exception('only hash-code colors are supported!')


def generate_pdf_from_svg_using_inkscape(svg_data, pdfpath):
    inkscape = which_inkscape()
    path = os.path.realpath(pdfpath)
    args = [inkscape,
            '--without-gui',
            '--export-area-drawing',
            '--export-ignore-filters',
            '--export-dpi={dpi}'.format(dpi=DPI),
            '--export-pdf={path}'.format(path=path)]
    with tempfile.NamedTemporaryFile(
            suffix='.svg', delete=True) as tmpsvg:
        svg_data.write(tmpsvg, encoding='utf-8',
                      xml_declaration=True)
        tmpsvg.flush()
        bboxes = svg_bounding_boxes(tmpsvg.name)
        # shutil.copyfile(tmpsvg.name, 'foo_bare.svg')
        tmp_path = os.path.realpath(tmpsvg.name)
        args.append('--file={s}'.format(s=tmp_path))
        with subprocess.Popen(args) as proc:
            proc.wait()
            if proc.returncode != 0:
                raise Exception((
                    '`{inkscape}` conversion of SVG '
                    'to PDF failed with return code '
                    '{rcode}'
                    ).format(
                        inkscape=inkscape,
                        rcode=proc.returncode))
    return bboxes


def generate_pdf_from_svg_using_cairo(svg_data, pdfpath):
    with tempfile.NamedTemporaryFile(
            suffix='.svg', delete=True) as tmpsvg:
        svg_data.write(tmpsvg, encoding='utf-8',
                      xml_declaration=True)
        tmpsvg.flush()
        bboxes = svg_bounding_boxes(tmpsvg.name)
        # shutil.copyfile(tmpsvg.name, 'foo_bare.svg')
        cairosvg.svg2pdf(
            file_obj=tmpsvg,
            write_to=pdfpath)
    return bboxes


def pdf_bounding_box(pdf_bboxes):
    """Return PDF bounding box."""
    # Drawing area coordinates within SVG
    for k, d in pdf_bboxes.items():
        if k.startswith('svg'):
            break
    xmin, xmax, ymin, ymax = corners(d)
    pdf_bbox = BBox(
        x=xmin,
        y=ymin,
        width=xmax - xmin,
        height=ymax - ymin)
    return pdf_bbox


def svg_bounding_box(
        svg_bboxes, text_ids, ignore_ids, pdf_bbox):
    """Return initial SVG bounding box."""
    xs = set()
    ys = set()
    # pprint.pprint(svg_bboxes)
    for name in text_ids:
        d = svg_bboxes.get(name)
        if name in ignore_ids or d is None:
            continue
        x, _, y, _ = corners(d)
        xs.add(x)
        ys.add(y)
    # overall bounding box
    xmin = pdf_bbox.x
    ymin = pdf_bbox.y
    xmax = xmin + pdf_bbox.width
    ymax = ymin + pdf_bbox.height
    xs.add(xmin)
    xs.add(xmax)
    ys.add(ymin)
    ys.add(ymax)
    x_min = min(xs)
    x_max = max(xs)
    y_min = min(ys)
    y_max = max(ys)
    svg_bbox = BBox(
        x=x_min,
        y=y_min,
        width=x_max - x_min,
        height=y_max - y_min)
    return svg_bbox


def svg_bounding_boxes(svgfile):
    """Parses the output from inkscape `--query-all`."""
    inkscape = which_inkscape()
    path = os.path.realpath(svgfile)
    args = [
        inkscape,
        '--without-gui',
        '--query-all',
        '--file={s}'.format(s=path)]
    with subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            universal_newlines=True) as proc:
        lines = proc.stdout.readlines()
        proc.wait()
        if proc.returncode != 0:
            raise Exception((
                '`{inkscape}` exited with '
                'return code {rcode}'
                ).format(
                    inkscape=inkscape,
                    rcode=proc.returncode))
    bboxes = dict()
    for line in lines:
        name, x, y, w, h = parse_bbox_string(line)
        bboxes[name] = dict(x=x, y=y, w=w, h=h)
    return bboxes


def which_inkscape():
    """Return absolute path to `inkscape`.

    Assume that `inkscape` is in the `$PATH`.
    Useful on OS X, where calling `inkscape` from the command line does not
    work properly, unless an absolute path is used.

    In the future, using another approach for conversion (e.g., a future
    version of `cairosvg`) will make this function obsolete.
    """
    s = shutil.which('inkscape')
    inkscape_abspath = os.path.realpath(s)
    return inkscape_abspath


def parse_bbox_string(line):
    """Return `x, y, w, h` from bounding box string."""
    name, *rest = line.split(',')
    x, y, w, h = [float(x) for x in rest]
    return name, x, y, w, h


def corners(d):
    """Return corner coordinates.

    @param d: `dict` with keys
        `'x', 'y', 'w', 'h'`
    """
    x = d['x']
    y = d['y']
    w = d['w']
    h = d['h']
    xmax = x + w
    ymax = y + h
    return x, xmax, y, ymax


class AffineTransform(object):

    def __init__(self, t=None, m=None):
        self.t = (0.0, 0.0) if t is None else t
        self.m = (1.0, 0.0, 0.0, 1.0) if m is None else m

    def clone(self):
        nt = AffineTransform()
        nt.t = self.t
        nt.m = self.m
        return nt

    def translate(self, tx, ty):
        self.matrix(1.0, 0.0, 0.0, 1.0, tx, ty)

    def rotate_degrees(self, angle, cx=0.0, cy=0.0):
        angle = math.radians(angle)
        sin, cos = math.sin(angle), math.cos(angle)
        if cx != 0.0 or cy != 0.0:
            self.translate(cx, cy)
            self.matrix(cos, sin, -sin, cos, 0.0, 0.0)
            self.translate(-cx, -cy)
        else:
            self.matrix(cos, sin, -sin, cos, 0.0, 0.0)

    def scale(self, sx, sy=None):
        if sy is None:
            sy = sx
        self.matrix(sx, 0.0, 0.0, sy)

    def matrix(self, a, b, c, d, e=0.0, f=0.0):
        sa, sb, sc, sd = self.m
        se, sf = self.t

        ma = sa * a + sc * b
        mb = sb * a + sd * b
        mc = sa * c + sc * d
        md = sb * c + sd * d
        me = sa * e + sc * f + se
        mf = sb * e + sd * f + sf
        self.m = (ma, mb, mc, md)
        self.t = (me, mf)

    def applyTo(self, x, y=None):
        if y is None:
            x, y = x
        xx = self.t[0] + self.m[0] * x + self.m[2] * y
        yy = self.t[1] + self.m[1] * x + self.m[3] * y
        return (xx, yy)

    def __str__(self):
        return '[{},{},{}  ;  {},{},{}]'.format(
            self.m[0], self.m[2], self.t[0],
            self.m[1], self.m[3], self.t[1])

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

    def get_rotation(self):
        m11, m21, m12, m22 = self.m
        len1 = math.sqrt(m11 * m11 + m21 * m21)
        len2 = math.sqrt(m12 * m12 + m22 * m22)
        # TODO check that len1 and len2 are close to 1
        # TODO check that the matrix is orthogonal
        # TODO do a real matrix decomposition here!
        return math.degrees(math.atan2(m21, m11))


class TeXLabel(object):

    def __init__(self, pos, text):
        self.text = text
        self.color = (0, 0, 0)
        self.pos = pos
        self.angle = 0.0
        self.align = ALIGN_LEFT
        self.fontsize = None
        self.fontfamily = 'rm'
        self.fontweight = WEIGHT_NORMAL
        self.fontstyle = STYLE_NORMAL
        self.scale = 1.0

    def texcode(self):
        color = self._color_tex()
        font = '\\' + self.fontfamily + 'family'
        font += self._font_weight_tex()
        font += self._font_style_tex()
        font += self._font_size_tex()
        align = self._alignment_tex()
        text = self._text()
        texcode = (
            font + color + align +
            r'{\smash{' + text + '}}')
        if self.angle != 0.0:
            texcode = (
                '\\rotatebox{{{angle}}}{{{texcode}}}'
                ).format(
                    angle=self.angle,
                    texcode=texcode)
        return texcode

    def _color_tex(self):
        r, g, b = self.color
        if r != 0 or g != 0 or b != 0:
            color = '\\color[RGB]{{{r},{g},{b}}}'.format(
                r=r, g=g, b=b)
        else:
            color = ''
        return color

    def _font_weight_tex(self):
        if self.fontweight >= WEIGHT_BOLD:
            return r'\bfseries'
        else:
            return ''

    def _font_style_tex(self):
        if self.fontstyle == STYLE_ITALIC:
            return r'\itshape'
        elif self.fontstyle == STYLE_OBLIQUE:
            return r'\slshape'
        else:
            return ''

    def _font_size_tex(self):
        if self.fontsize is not None:
            return self.fontsize
        else:
            return ''

    def _alignment_tex(self):
        if self.align == ALIGN_LEFT:
            return r'\makebox(0,0)[bl]'
        elif self.align == ALIGN_CENTER:
            return r'\makebox(0,0)[b]'
        elif self.align == ALIGN_RIGHT:
            return r'\makebox(0,0)[br]'
        else:
            raise ValueError(align)

    def _text(self):
        if self.text is None:
            return ''
        else:
            return self.text


class TeXPicture(object):

    def __init__(
            self, svg_bbox, pdf_bbox,
            fname=None, labels=None):
        self.svg_bbox = svg_bbox
        self.pdf_bbox = pdf_bbox
        self.background_graphics = fname
        if labels is None:
            labels = list()
        self.labels = labels

    def dumps(self):
        unit = self.svg_bbox.width
        xmin = self.svg_bbox.x
        ymin = self.svg_bbox.y
        w = self.svg_bbox.width
        h = self.svg_bbox.height
        c = list()
        if self.background_graphics is not None:
            x = self.pdf_bbox.x - xmin
            # the SVG coordinate system origin is at the top left corner
            # whereas the `picture` origin is at the lower left corner
            y = (h + ymin) - (self.pdf_bbox.height + self.pdf_bbox.y)
            x, y = _round(x, y, unit=unit)
            scale = self.pdf_bbox.width / unit
            s = (
                '\\put({x}, {y}){{'
                '\\includegraphics[width={scale}\\unitlength]{{{img}}}'
                '}}%').format(
                    scale=scale,
                    x=x, y=y,
                    img=self.background_graphics)
            c.append(s)
        for label in self.labels:
            x, y = label.pos
            # y=0 top in SVG, bottom in `\picture`
            x = x - xmin
            y = (h + ymin) - y
            x, y = _round(x, y, unit=unit)
            s = '\\put({x}, {y}){{{text}}}%'.format(
                x=x, y=y,
                text=label.texcode())
            c.append(s)
        width, height = _round(w, h, unit=unit)
        assert width == 1, width
        s = (
            '\\begingroup%\n' +
            PICTURE_PREAMBLE +
            ('\\begin{{picture}}'
             '({width}, {height})%\n').format(
                width=width,
                height=height) +
            '\n'.join(c) + '\n' +
            '\\end{picture}%\n'
            '\\endgroup%\n')
        return s

    def add_label(self, label):
        self.labels.append(label)


def _round(*args, unit=1):
    return tuple(round(x / unit, 3) for x in args)


if __name__ == '__main__':
    args = parse_args()
    convert(args.fname)
