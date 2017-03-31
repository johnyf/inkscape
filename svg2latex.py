#!/usr/bin/env python3
# vim: set ts=4 sw=4 noet ai:
#
# This file is from:
# https://github.com/johnbartholomew/svg2latex/blob/
# b77623b617b9b92c131a8eafe09ec1b1abed93f2/svg2latex.py
#
# BSD 3-Clause License
#
# Copyright (c) 2017, John Bartholomew All rights reserved.
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
import lxml.etree as etree
import subprocess
import re
import tempfile
import math
import io
import os
import sys

class AffineTransform:
	def __init__(s, t=None, m=None):
		s.t = (0.0, 0.0) if t is None else t
		s.m = (1.0,0.0, 0.0,1.0) if m is None else m

	def clone(s):
		nt = AffineTransform()
		nt.t = s.t
		nt.m = s.m
		return nt

	def translate(s, tx, ty):
		s.matrix(1.0,0.0, 0.0,1.0, tx,ty)

	def rotate_degrees(s, angle, cx=0.0, cy=0.0):
		angle = math.radians(angle)
		sin,cos = math.sin(angle), math.cos(angle)
		if cx != 0.0 or cy != 0.0:
			s.translate(cx,cy)
			s.matrix(cos,sin, -sin,cos, 0.0,0.0)
			s.translate(-cx,-cy)
		else:
			s.matrix(cos,sin, -sin,cos, 0.0,0.0)

	def scale(s, sx, sy=None):
		if sy is None:
			sy = sx
		s.matrix(sx,0.0, 0.0,sy)

	def matrix(s, a,b,c,d,e=0.0,f=0.0):
		sa,sb,sc,sd = s.m
		se,sf = s.t

		ma = sa*a + sc*b
		mb = sb*a + sd*b
		mc = sa*c + sc*d
		md = sb*c + sd*d
		me = sa*e + sc*f + se
		mf = sb*e + sd*f + sf
		s.m = (ma,mb, mc,md)
		s.t = (me,mf)

	def applyTo(s, x, y=None):
		if y is None:
			x,y = x
		xx = s.t[0] + s.m[0]*x+s.m[2]*y
		yy = s.t[1] + s.m[1]*x+s.m[3]*y
		return (xx,yy)

	def __str__(s):
		return '[{},{},{}  ;  {},{},{}]'.format(s.m[0],s.m[2],s.t[0],s.m[1],s.m[3],s.t[1])

	def __mul__(a, b):
		a11,a21,a12,a22 = a.m
		a13,a23 = a.t
		b11,b21,b12,b22 = b.m
		b13,b23 = b.t

		# cIJ = aI1*b1J + aI2*b2J + aI3*b3J
		c11 = a11*b11 + a12*b21
		c12 = a11*b12 + a12*b22
		c13 = a11*b13 + a12*b23 + a13
		c21 = a21*b11 + a22*b21
		c22 = a21*b12 + a22*b22
		c23 = a21*b13 + a22*b23 + a23
		return AffineTransform((c13,c23), (c11,c21,c12,c22))

	def get_rotation(s):
		m11,m21,m12,m22 = s.m
		len1 = math.sqrt(m11*m11 + m21*m21)
		len2 = math.sqrt(m12*m12 + m22*m22)
		# TODO check that len1 and len2 are close to 1
		# TODO check that the matrix is orthogonal
		# TODO do a real matrix decomposition here!
		return math.degrees(math.atan2(m21,m11))

SVG_UNITS_TO_BIG_POINTS = 72.0/90.0

PICTURE_PREAMBLE = r"""% Picture generated by svg2latex
\makeatletter
\providecommand\color[2][]{%
  \errmessage{(svg2latex) Color is used for the text in Inkscape, but the package 'color.sty' is not loaded}%
  \renewcommand\color[2][]{}%
}
\providecommand\transparent[1]{%
  \errmessage{(svg2latex) Transparency is used for the text in Inkscape, but the package 'transparent.sty' is not loaded}%
  \renewcommand\transparent[1]{}%
}
\makeatother
\setlength{\unitlength}{1bp}%
"""

ALIGN_LEFT = 0
ALIGN_CENTER = 1
ALIGN_RIGHT = 2

WEIGHT_NORMAL = 500
WEIGHT_BOLD = 700

STYLE_NORMAL = 0
STYLE_ITALIC = 1
STYLE_OBLIQUE = 2

class RawTeXLabel:
	def __init__(s, pos, texcode):
		s.pos = pos
		s.code = texcode

	def texcode(s):
		return '\\scalebox{' + str(SVG_UNITS_TO_BIG_POINTS) + '}{\\makebox(0,0)[bl]{%\n' + s.code + '%\n}}'

class TeXLabel:
	def __init__(s, pos, text):
		s.text = text
		s.color = (0,0,0)
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

		r,g,b = s.color
		if (r != 0) or (g != 0) or (b != 0):
			color = '\\color[RGB]{{{},{},{}}}'.format(r,g,b)

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

		texcode = font + color + align + r'{\smash{' + s.text + '}}'

		if s.angle != 0.0:
			texcode = '\\rotatebox{{{}}}{{{}}}'.format(s.angle, texcode)

		return texcode

class TeXPicture:
	def __init__(s, width, height):
		s.width = width
		s.height = height
		s.backgroundGraphic = None
		s.labels = []

	def emit_picture(s, stream):
		stream.write('\\begingroup%\n')
		stream.write(PICTURE_PREAMBLE)
		stream.write('\\begin{{picture}}({},{})%\n'.format(s.width, s.height))
		if s.backgroundGraphic is not None:
			stream.write('\\put(0,0){{\\includegraphics{{{}}}}}%\n'.format(s.backgroundGraphic))
		for label in s.labels:
			x,y = label.pos
			stream.write('\\put({},{}){{{}}}%\n'.format(round(x,3),round(y,3), label.texcode()))
		stream.write('\\end{picture}%\n')
		stream.write('\\endgroup%\n')

	def add_label(s, label):
		s.labels.append(label)


TEXTEXT_NS = r"http://www.iki.fi/pav/software/textext/"
TEXTEXT_PREFIX = '{' + TEXTEXT_NS + '}'
INKSVG_NAMESPACES = {
   'dc': r"http://purl.org/dc/elements/1.1/",
   'cc': r"http://creativecommons.org/ns#",
   'rdf': r"http://www.w3.org/1999/02/22-rdf-syntax-ns#",
   'svg': r"http://www.w3.org/2000/svg",
   'textext': TEXTEXT_NS,
   'xlink': r"http://www.w3.org/1999/xlink",
   'sodipodi': r"http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd",
   'inkscape': r"http://www.inkscape.org/namespaces/inkscape",
}

RX_TRANSFORM = re.compile('^\s*(\w+)\(([0-9,\s\.-]*)\)\s*$')

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
		xform.translate(tx,ty)
	elif func == 'scale':
		if len(args) < 1 or len(args) > 2:
			raise Exception('bad scale transform')
		sx = args[0]
		sy = args[1] if len(args) > 1 else sx
		xform.scale(sx,sy)
	else:
		raise Exception('unsupported transform attribute (' + attribute + ')')
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
		return (r,g,b)
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

FONT_MAP = {
	'CMU Serif': 'rm',
	'CMU Sans Serif': 'sf',
	'CMU Typewriter Text': 'tt'
}

FONT_SIZE_MAP = {
	'9px': r'\scriptsize',
	'10px': r'\footnotesize',
	'11px': r'\small',
	'12px': r'\normalsize',
	'13px': r'\large'
}

def interpret_svg_text(textEl, texDoc):
	style = split_svg_style(textEl.attrib['style']) if 'style' in textEl.attrib else {}
	for tspan in textEl.xpath('svg:tspan', namespaces=INKSVG_NAMESPACES):
		span_style = style.copy()
		if 'style' in tspan.attrib:
			span_style.update(split_svg_style(tspan.attrib['style']))
		xform = compute_svg_transform(tspan)
		pos = (float(tspan.attrib['x']), float(tspan.attrib['y']))
		pos = xform.applyTo(pos)
		pos = (SVG_UNITS_TO_BIG_POINTS * pos[0], texDoc.height - SVG_UNITS_TO_BIG_POINTS * pos[1])

		angle = -round(xform.get_rotation(),3)
		texLabel = TeXLabel(pos, tspan.text)
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

		texDoc.add_label(texLabel)

def interpret_svg_textext(textEl, texDoc):
	texcode = textEl.attrib[TEXTEXT_PREFIX+'text'].encode('utf-8').decode('unicode_escape')
	xform = compute_svg_transform(textEl)

	placedElements = textEl.xpath(r'.//svg:use', namespaces=INKSVG_NAMESPACES)
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
		pos = (SVG_UNITS_TO_BIG_POINTS * pos[0], texDoc.height - SVG_UNITS_TO_BIG_POINTS * pos[1])
	else:
		pos = (0.0,0.0)

	texDoc.add_label(RawTeXLabel(pos, texcode))

def process_svg(inpath):
	doc = etree.parse(inpath)
	normalTextElements = doc.xpath('//svg:text', namespaces=INKSVG_NAMESPACES)
	texTextElements = doc.xpath('//*[@textext:text]', namespaces=INKSVG_NAMESPACES)
	# 72 big-points (PostScript points) per inch, 90 SVG "User Units" per inch
	width = float(doc.getroot().attrib['width']) * SVG_UNITS_TO_BIG_POINTS
	height = float(doc.getroot().attrib['height']) * SVG_UNITS_TO_BIG_POINTS
	texDoc = TeXPicture(width, height)
	for textEl in normalTextElements:
		interpret_svg_text(textEl, texDoc)
		parent = textEl.getparent()
		parent.remove(textEl)
	for textEl in texTextElements:
		interpret_svg_textext(textEl, texDoc)
		parent = textEl.getparent()
		parent.remove(textEl)
	return doc, texDoc

def generate_pdf_from_svg(svgData, pdfpath):
	args = ['/usr/bin/inkscape',
				'--without-gui',
				'--export-area-page',
				'--export-ignore-filters',
				'--export-dpi=90',
				'--export-pdf={}'.format(pdfpath)]
	with tempfile.NamedTemporaryFile(suffix='.svg', delete=True) as tmpsvg:
		svgData.write(tmpsvg, encoding='utf-8', xml_declaration=True)
		tmpsvg.flush()
		args.append(tmpsvg.name)
		with subprocess.Popen(args) as proc:
			proc.wait()
			if proc.returncode != 0:
				sys.stderr.write('inkscape svg->pdf failed')

def svgDataToPdfInkscape(xmldata, outpath):
	fl = tempfile.NamedTemporaryFile(suffix='.svg',delete=True)
	fl.write(xmldata)
	fl.flush()
	inkscapeProcess = subprocess.Popen(['/usr/bin/inkscape',
		'--export-area-page','--export-ignore-filters','--export-dpi='+str(PIXELS_PER_INCH),
		'--export-pdf=' + outpath, fl.name],stdin=subprocess.PIPE)
	inkscapeProcess.communicate(xmldata)
	if inkscapeProcess.returncode != 0:
		sys.stderr.write('inkscape returned an error code (' + str(inkscapeProcess.returncode) + ')\n')
	fl.close()

def main():
	xmlData, texDoc = process_svg('test-figure.svg')
	basename, ext = 'test-figure', '.svg'
	texpath = basename + '.tex'
	pdfpath = basename + '.pdf'

	texDoc.backgroundGraphic = pdfpath

	with open(texpath, 'w', encoding='utf-8') as fl:
		texDoc.emit_picture(fl)
	generate_pdf_from_svg(xmlData, pdfpath)

if __name__ == '__main__':
	main()
