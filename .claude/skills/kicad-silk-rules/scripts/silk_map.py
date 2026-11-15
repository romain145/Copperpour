#!/usr/bin/env python3
"""Draw a millimetre-gridded map of one board side for hand-placing silkscreen text.

Shows what a text must avoid (component bodies grey, pads gold, footprint silkscreen
white) and every printed text as a box with its string (red when the audit flags it).
Grid lines every 1 mm, labelled every 2 mm in board coordinates. Read-only.

Usage:
  python silk_map.py BOARD.kicad_pcb OUT.png [--side F|B] [--window x1,y1,x2,y2] [--scale 40]
                     [--plan plan.json]   (planned boxes from plan_silk_moves.py, drawn cyan)
Needs Pillow (KiCad's bundled python has it).
"""
import argparse
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_visual_check as bvc  # noqa: E402
import plan_silk_moves as psm  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board')
    ap.add_argument('out')
    ap.add_argument('--side', default='F', choices=['F', 'B'])
    ap.add_argument('--window', default=None)
    ap.add_argument('--scale', type=float, default=40)
    ap.add_argument('--plan', default=None)
    a = ap.parse_args()

    obst, texts, _ = psm.collect(a.board)
    loops = bvc.build_loops(bvc.load(a.board)[0])
    findings, _ = bvc.audit(a.board, 0.15, bvc.default_share(), 0.05)
    flagged = {f['item'].split(' "')[0] for f in findings if f['check'] != 'model'}
    if a.window:
        x1, y1, x2, y2 = (float(v) for v in a.window.split(','))
    else:
        x1, y1, x2, y2 = psm.grow(psm.aabb(loops[0]), 1.0)
    s = a.scale
    img = Image.new('RGB', (int((x2 - x1) * s), int((y2 - y1) * s)), (16, 48, 32))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('arial.ttf', max(10, int(s * 0.38)))
    except OSError:
        font = ImageFont.load_default()

    def px(p):
        return ((p[0] - x1) * s, (p[1] - y1) * s)

    def rect(box, **kw):
        d.rectangle([px((box[0], box[1])), px((box[2], box[3]))], **kw)

    for box, kind, owner in obst[a.side]:
        if kind == 'body':
            rect(box, fill=(70, 70, 70), outline=(120, 120, 120))
    for box, kind, owner in obst[a.side]:
        if kind == 'pad':
            rect(box, fill=(200, 160, 40))
        elif kind == 'silk':
            rect(box, fill=(235, 235, 235))
        elif kind == 'text' and not any(owner == psm.label(t) for t in texts):
            rect(box, outline=(235, 235, 235))
    gx = int(x1) + 1
    while gx < x2:
        d.line([px((gx, y1)), px((gx, y2))], fill=(60, 110, 80) if gx % 2 else (90, 160, 110), width=1)
        if gx % 2 == 0:
            d.text((px((gx, y1))[0] + 2, 2), str(gx), fill=(255, 255, 0), font=font)
        gx += 1
    gy = int(y1) + 1
    while gy < y2:
        d.line([px((x1, gy)), px((x2, gy))], fill=(60, 110, 80) if gy % 2 else (90, 160, 110), width=1)
        if gy % 2 == 0:
            d.text((2, px((x1, gy))[1] + 2), str(gy), fill=(255, 255, 0), font=font)
        gy += 1
    for lp in loops:
        d.line([px(p) for p in lp + [lp[0]]], fill=(255, 0, 255), width=3)
    for t in texts:
        if t['side'] != a.side:
            continue
        box = psm.aabb(t['quad'])
        bad = psm.label(t) in flagged
        rect(box, outline=(255, 60, 60) if bad else (80, 255, 80), width=2)
        d.text(px((box[0], box[1])), t['string'].split('\n')[0], fill=(255, 140, 140) if bad else (160, 255, 160),
               font=font)
    if a.plan:
        with open(a.plan, encoding='utf-8-sig') as fh:
            for m in json.load(fh)['moves']:
                if m['side'] != a.side:
                    continue
                size = m.get('size_y', 1.0)
                w = len(m['string'].split('\n')[0]) * m.get('size_x', size) * bvc.CHAR_ADVANCE
                bw, bh = (size, w) if m['rotation'] == 90 else (w, size)
                box = (m['centre_x'] - bw / 2, m['centre_y'] - bh / 2, m['centre_x'] + bw / 2, m['centre_y'] + bh / 2)
                rect(box, outline=(0, 220, 255), width=2)
                d.text(px((box[0], box[3])), m['string'].split('\n')[0], fill=(0, 220, 255), font=font)
    img.save(a.out)
    print(a.out, img.size)


if __name__ == '__main__':
    main()
