#!/usr/bin/env python3
"""Propose new places for silkscreen texts that board_visual_check.py (and KiCad's DRC) flag.

Read-only: prints a JSON plan; apply it through the PCB tool's own text-edit
commands (Konnect: edit_footprint_field / edit_board_text), never by editing the file.

A place is free when the text box clears, on its board side: component bodies,
pads (through-hole pads on both sides), untented vias, footprint silkscreen
artwork, every other printed text, and the board edge.

Usage:
  python plan_silk_moves.py BOARD.kicad_pcb [--drc drc.json] [--also R28,D2]
                            [--skip BAT1] [--min-size 0.8] [--edge-margin 0.3]
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_visual_check as bvc  # noqa: E402
from board_visual_check import kid, kids, nums, flag, rot, layer_of  # noqa: E402


def aabb(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def grow(box, d):
    return box[0] - d, box[1] - d, box[2] + d, box[3] + d


def hit(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def box_dist(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return math.hypot(dx, dy)


def text_dims(string, effects, scale=1.0):
    font = kid(effects or [], 'font') or []
    sh, sw = (nums(kid(font, 'size')) if kid(font, 'size') else [1.0, 1.0])[:2]
    th = nums(kid(font, 'thickness'), 1)[0] if kid(font, 'thickness') else 0.15 * min(sw, sh)
    sh, sw, th = sh * scale, sw * scale, th * scale
    lines = string.split('\n')
    return (max(len(s) for s in lines) * sw * bvc.CHAR_ADVANCE + th,
            sh * (1 + bvc.LINE_PITCH * (len(lines) - 1)) + th, sw, sh, th)


def collect(board_path):
    """Obstacles per side as axis-aligned boxes, plus the movable texts."""
    with open(board_path, encoding='utf-8') as fh:
        root = bvc.parse(fh.read())
    obst = {'F': [], 'B': []}          # (box, kind, owner)
    texts, own_box = [], {}
    setup = kid(root, 'setup') or []
    tent = kid(setup, 'tenting') or []
    # KiCad 9+: (tenting (front yes) (back yes)); older: (tenting front back)
    tented = [c for c in tent[1:] if isinstance(c, str)] + [w for w in ('front', 'back') if flag(tent, w)]

    def ident(x, y):
        return (x, y)

    def add_graphic(g, xf, side, owner):
        pts, closed = bvc.shape_points(g, xf)
        if closed and pts:
            pts = pts + [pts[0]]
        w = nums(kid(kid(g, 'stroke') or [], 'width'), 1)[0] or 0.12
        for a, b in zip(pts, pts[1:]):
            obst[side].append((grow(aabb([a, b]), w / 2), 'silk', owner))

    for node in root:
        if not isinstance(node, list) or not node or not isinstance(node[0], str):
            continue
        head = node[0]
        if head == 'via':
            x, y = nums(kid(node, 'at'))[:2]
            r = nums(kid(node, 'size'), 1)[0] / 2
            for side, word in (('F', 'front'), ('B', 'back')):
                if word not in tented:
                    obst[side].append(((x - r, y - r, x + r, y + r), 'via', ''))
        elif head.startswith('gr_') and layer_of(node).endswith('.SilkS'):
            side = layer_of(node)[0]
            if head == 'gr_text':
                if flag(node, 'hide'):
                    continue
                at = nums(kid(node, 'at'), 3)
                texts.append({'owner': 'board', 'what': 'text', 'string': node[1], 'side': side,
                              'uuid': (kid(node, 'uuid') or [None, None])[1], 'angle': at[2],
                              'effects': kid(node, 'effects'),
                              'quad': bvc.text_quad(node[1], at[:2], at[2], kid(node, 'effects'))})
            else:
                add_graphic(node, ident, side, 'board')
        elif head == 'footprint':
            at = nums(kid(node, 'at'), 3)
            fx, fy, frot = at
            side = layer_of(node)[0]

            def xf(lx, ly, fx=fx, fy=fy, frot=frot):
                rx, ry = rot(lx, ly, frot)
                return (fx + rx, fy + ry)

            props = {p[1]: p for p in kids(node, 'property')}
            ref = props['Reference'][2] if 'Reference' in props else '?'
            for name in ('Reference', 'Value'):
                p = props.get(name)
                if not p or not layer_of(p).endswith('.SilkS') or flag(p, 'hide') \
                        or flag(kid(p, 'effects') or [], 'hide'):
                    continue
                tat = nums(kid(p, 'at'), 3)
                texts.append({'owner': ref, 'what': name.lower(), 'string': p[2], 'side': layer_of(p)[0],
                              'angle': tat[2], 'effects': kid(p, 'effects'),
                              'quad': bvc.text_quad(p[2], xf(tat[0], tat[1]), tat[2], kid(p, 'effects'))})
            fab, crt, padpts = [], [], []
            for g in node:
                if not isinstance(g, list) or not g or not isinstance(g[0], str):
                    continue
                if g[0] == 'fp_text':
                    if layer_of(g).endswith('.SilkS') and not flag(g, 'hide'):
                        tat = nums(kid(g, 'at'), 3)
                        q = bvc.text_quad(g[2].replace('${REFERENCE}', ref), xf(tat[0], tat[1]), tat[2],
                                          kid(g, 'effects'))
                        obst[layer_of(g)[0]].append((aabb(q), 'text', ref))
                elif g[0].startswith('fp_'):
                    lay = layer_of(g)
                    if lay.endswith('.SilkS'):
                        add_graphic(g, xf, lay[0], ref)
                    elif lay == side + '.Fab':
                        fab += bvc.shape_points(g, xf)[0]
                    elif lay == side + '.CrtYd':
                        crt += bvc.shape_points(g, xf)[0]
                elif g[0] == 'pad':
                    pat, size = nums(kid(g, 'at'), 3), nums(kid(g, 'size'))
                    c = xf(pat[0], pat[1])
                    quad = [(c[0] + r[0], c[1] + r[1]) for r in
                            (rot(sx * size[0] / 2, sy * size[1] / 2, pat[2]) for sx in (-1, 1) for sy in (-1, 1))]
                    box = aabb(quad)
                    padpts += quad
                    lays = [c for c in (kid(g, 'layers') or [])[1:] if isinstance(c, str)]
                    for s in 'FB':
                        if any(x.startswith(s + '.') or x.startswith('*.') for x in lays) or g[2] in ('thru_hole', 'np_thru_hole'):
                            obst[s].append((box, 'pad', ref))
            body = fab or crt or padpts
            if body:
                obst[side].append((aabb(body), 'body', ref))
                own_box[ref] = aabb(body + padpts)
    return obst, texts, own_box


def label(t):
    return f"{t['owner']} {t['what']}"


def solve(board_path, drc_path, also, skip, min_size, edge_margin, clear, force_size=None):
    findings, _ = bvc.audit(board_path, 0.15, bvc.default_share(), 0.05)
    edge_shapes = bvc.load(board_path)[0]
    loops = bvc.build_loops(edge_shapes)
    obst, texts, own_box = collect(board_path)

    wanted = set(also)
    for f in findings:
        if f['check'] in ('edge', 'hidden', 'overlap'):
            m = re.match(r'(\S+) (reference|value|text) ', f['item'])
            if m and m.group(2) != 'text' or (m and m.group(1) == 'board'):
                wanted.add(m.group(1) if m.group(1) != 'board' else 'board:' + f['item'].split('"')[1].split(' / ')[0])
            # for an overlap, the second party of a ref/ref pair stays where it is
    if drc_path:
        with open(drc_path, encoding='utf-8') as fh:
            for v in json.load(fh).get('violations', []):
                if v.get('type', '').startswith('silk'):
                    for it in v.get('items', []):
                        m = re.search(r'Reference field of (\S+)', it.get('description', ''))
                        if m:
                            wanted.add(m.group(1))
    wanted -= set(skip)

    def is_wanted(t):
        if t['owner'] == 'board':
            return ('board:' + t['string'].split('\n')[0]) in wanted
        return t['what'] == 'reference' and t['owner'] in wanted

    moving = [t for t in texts if is_wanted(t)]
    fixed = [t for t in texts if not is_wanted(t)]
    for t in fixed:
        obst[t['side']].append((aabb(t['quad']), 'text', label(t)))

    def ok(box, side, owner_ref):
        for corner in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
            if not loops or not bvc.inside(corner, loops[0]) or any(bvc.inside(corner, h) for h in loops[1:]):
                return False
        q = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
        for lp in loops:
            for j in range(len(lp)):
                for i in range(4):
                    if bvc.seg_seg(q[i], q[(i + 1) % 4], lp[j], lp[(j + 1) % len(lp)]) < edge_margin:
                        return False
        g = grow(box, clear)
        return not any(hit(g, o[0]) for o in obst[side])

    moving.sort(key=lambda t: -bvc.area(t['quad']))
    plan, failed = [], []
    for t in moving:
        side = t['side']
        cur = aabb(t['quad'])
        cur_c = ((cur[0] + cur[2]) / 2, (cur[1] + cur[3]) / 2)
        cur_vert = abs(t['angle']) % 180 == 90
        best = None
        _, _, _, base_h, _ = text_dims(t['string'], t['effects'])
        scales = [1.0] + [s for s in (0.8 / base_h, 0.7 / base_h, min_size / base_h)
                          if s < 0.999 and s * base_h >= min_size - 1e-6]
        if force_size and t['owner'] != 'board':
            scales = [force_size / base_h]
        for scale in dict.fromkeys(scales):
            w, h, sw, sh, th = text_dims(t['string'], t['effects'], scale)
            for vert in (cur_vert, not cur_vert):
                bw, bh = (h, w) if vert else (w, h)
                cands = []
                if t['owner'] == 'board':
                    # anywhere on this side, nearest to where it is now
                    bx = aabb(loops[0])
                    x = bx[0]
                    while x <= bx[2]:
                        y = bx[1]
                        while y <= bx[3]:
                            cands.append((x, y, math.hypot(x - cur_c[0], y - cur_c[1])))
                            y += 0.5
                        x += 0.5
                else:
                    ob = own_box[t['owner']]
                    for gap in (0.3, 0.5, 0.8, 1.2, 1.8, 2.5, 3.5, 5.0):
                        n = int(((ob[2] - ob[0]) + bw) / 0.25) + 1
                        for i in range(-n, n + 1):
                            x = (ob[0] + ob[2]) / 2 + i * 0.25
                            if abs(x - (ob[0] + ob[2]) / 2) <= (ob[2] - ob[0]) / 2 + bw / 2:
                                cands.append((x, ob[1] - gap - bh / 2, None))
                                cands.append((x, ob[3] + gap + bh / 2, None))
                        n = int(((ob[3] - ob[1]) + bh) / 0.25) + 1
                        for i in range(-n, n + 1):
                            y = (ob[1] + ob[3]) / 2 + i * 0.25
                            if abs(y - (ob[1] + ob[3]) / 2) <= (ob[3] - ob[1]) / 2 + bh / 2:
                                cands.append((ob[0] - gap - bw / 2, y, None))
                                cands.append((ob[2] + gap + bw / 2, y, None))
                for x, y, c in cands:
                    box = (x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)
                    if c is None:
                        ob = own_box[t['owner']]
                        c = box_dist(box, ob)
                        c += 0.15 * math.hypot(x - (ob[0] + ob[2]) / 2, y - (ob[1] + ob[3]) / 2)
                        near = min((box_dist(box, o[0]) for o in obst[side]
                                    if o[1] == 'body' and o[2] != t['owner']), default=9)
                        if near < box_dist(box, ob):
                            c += 1.0          # reads as another part's label
                    c += (0.4 if vert != cur_vert else 0) + (1 - scale) * 4
                    if (best is None or c < best[0]) and ok(box, side, t['owner']):
                        best = (c, x, y, vert, scale, sw, sh, th, box)
        if not best and t['owner'] == 'board':
            # no room on its own side: nearest free place on the other side
            other = 'B' if side == 'F' else 'F'
            w, h, sw, sh, th = text_dims(t['string'], t['effects'])
            bx = aabb(loops[0])
            x = bx[0]
            while x <= bx[2]:
                y = bx[1]
                while y <= bx[3]:
                    box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
                    c = math.hypot(x - cur_c[0], y - cur_c[1])
                    if (best is None or c < best[0]) and ok(box, other, 'board'):
                        best = (c, x, y, False, 1.0, sw, sh, th, box)
                    y += 0.5
                x += 0.5
            if best:
                side = other
        if not best:
            failed.append(label(t) + f" \"{t['string']}\"")
            obst[side].append((cur, 'text', label(t)))
            continue
        _, x, y, vert, scale, sw, sh, th, box = best
        obst[side].append((box, 'text', label(t)))
        if side != t['side']:
            t = dict(t, new_layer=side + '.SilkS')
        step = {'target': label(t), 'string': t['string'], 'side': side,
                **({'layer': t['new_layer']} if 'new_layer' in t else {}),
                'centre_x': round(x, 3), 'centre_y': round(y, 3), 'rotation': 90 if vert else 0,
                'from': [round(cur_c[0], 2), round(cur_c[1], 2)]}
        if t['owner'] == 'board':
            step['uuid'] = t.get('uuid')
        else:
            step['reference'] = t['owner']
        if abs(scale - 1) > 0.001:
            step.update(size_x=round(sw, 3), size_y=round(sh, 3), thickness=round(th, 3))
        plan.append(step)
    return {'moves': plan, 'no_free_place': failed}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board')
    ap.add_argument('--drc', help='kicad-cli DRC report (JSON); its silk findings are added to the work list')
    ap.add_argument('--also', default='', help='extra references to re-place, comma separated')
    ap.add_argument('--skip', default='', help='references to leave alone, comma separated')
    ap.add_argument('--min-size', type=float, default=0.8, help='smallest glyph height to shrink to, mm')
    ap.add_argument('--edge-margin', type=float, default=0.3)
    ap.add_argument('--size', type=float, default=None, help='give every re-placed reference this glyph height, mm')
    ap.add_argument('--clearance', type=float, default=0.12, help='gap kept around the text box, mm')
    a = ap.parse_args()
    split = lambda s: [x for x in s.split(',') if x]  # noqa: E731
    print(json.dumps(solve(a.board, a.drc, split(a.also), split(a.skip), a.min_size, a.edge_margin, a.clearance, a.size),
                     indent=1))


if __name__ == '__main__':
    main()
