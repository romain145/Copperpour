#!/usr/bin/env python3
"""Read-only visual audit of a saved KiCad board (.kicad_pcb).

Reports what KiCad's DRC does not, or only reports when two severities that are
often set to "ignore" are enabled:

  edge       silkscreen text cut by the board edge or a cut-out
  hidden     silkscreen text lying under a component body (invisible once assembled)
  overlap    two silkscreen texts printed over each other
  model      footprint whose 3D model file does not exist, is hidden, or is absent

The board file is only read, never written. Text boxes are estimated from the
glyph size (KiCad's stroke font), so treat a finding within about 0.2 mm of a
limit as "look at the render".

Usage:
  python board_visual_check.py BOARD.kicad_pcb [--json] [--edge-margin 0.15]
                               [--kicad-share "C:/Program Files/KiCad/10.0/share/kicad"]
Exit code: 0 no finding, 1 findings, 2 could not run.
"""
import argparse
import glob
import json
import math
import os
import re
import sys

TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+')
CHAR_ADVANCE = 0.87   # stroke-font advance per character, in glyph widths
LINE_PITCH = 1.62     # line spacing, in glyph heights


# ---------------------------------------------------------------- s-expressions
def parse(text):
    stack, cur = [], []
    for tok in TOKEN.findall(text):
        if tok == '(':
            stack.append(cur)
            cur = []
        elif tok == ')':
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif tok[0] == '"':
            cur.append(tok[1:-1].replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n'))
        else:
            cur.append(tok)
    return cur[0]


def kids(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def kid(node, name):
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def nums(node, n=2):
    out = []
    for v in (node or [])[1:]:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out + [0.0] * (n - len(out))


def flag(node, name):
    """True for a bare `name` atom or `(name yes)`."""
    if name in [c for c in node if isinstance(c, str)][1:]:
        return True
    k = kid(node, name)
    return bool(k) and (len(k) == 1 or k[1] == 'yes')


# --------------------------------------------------------------------- geometry
def rot(lx, ly, deg):
    """KiCad rotation: positive = counter-clockwise on screen, Y axis down."""
    a = math.radians(deg)
    return lx * math.cos(a) + ly * math.sin(a), -lx * math.sin(a) + ly * math.cos(a)


def area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2 if len(poly) > 2 else 0.0


def ccw(poly):
    s = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
            for i in range(len(poly)))
    return poly if s >= 0 else poly[::-1]


def clip(subject, clipper):
    """Sutherland-Hodgman: intersection of two convex polygons."""
    out = ccw(subject)
    cl = ccw(clipper)
    for i in range(len(cl)):
        a, b = cl[i], cl[(i + 1) % len(cl)]
        inp, out = out, []
        if not inp:
            break

        def side(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        for j in range(len(inp)):
            p, q = inp[j], inp[(j + 1) % len(inp)]
            sp, sq = side(p), side(q)
            if sp >= 0:
                out.append(p)
            if (sp > 0 > sq) or (sp < 0 < sq):
                t = sp / (sp - sq)
                out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def seg_point(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = dx * dx + dy * dy
    t = 0 if d == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def seg_seg(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0:
        return 0.0
    return min(seg_point(a, b, c), seg_point(a, b, d), seg_point(c, d, a), seg_point(c, d, b))


def inside(p, loop):
    n, r = len(loop), False
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            r = not r
    return r


def arc_points(s, m, e, step=5.0):
    ax, ay, bx, by, cx, cy = *s, *m, *e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return [s, e]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, a1, a2 = (math.atan2(p[1] - uy, p[0] - ux) for p in (s, m, e))
    sweep = (a2 - a0) % (2 * math.pi)
    if (a1 - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    n = max(2, int(abs(math.degrees(sweep)) / step) + 1)
    return [(ux + r * math.cos(a0 + sweep * i / n), uy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


def shape_points(node, xf):
    """World-space polyline of one gr_*/fp_* graphic; closed says whether it is a loop."""
    kind = node[0].split('_', 1)[1]
    if kind == 'line':
        return [xf(*nums(kid(node, 'start'))[:2]), xf(*nums(kid(node, 'end'))[:2])], False
    if kind == 'rect':
        (x1, y1), (x2, y2) = nums(kid(node, 'start'))[:2], nums(kid(node, 'end'))[:2]
        return [xf(x1, y1), xf(x2, y1), xf(x2, y2), xf(x1, y2)], True
    if kind == 'circle':
        (cx, cy), (ex, ey) = nums(kid(node, 'center'))[:2], nums(kid(node, 'end'))[:2]
        r = math.hypot(ex - cx, ey - cy)
        return [xf(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
                for a in range(0, 360, 10)], True
    if kind == 'arc' and kid(node, 'mid'):
        return arc_points(*(xf(*nums(kid(node, k))[:2]) for k in ('start', 'mid', 'end'))), False
    if kind == 'poly':
        return [xf(*nums(p)[:2]) for p in kids(kid(node, 'pts') or [], 'xy')], True
    return [], False


def layer_of(node):
    lay = kid(node, 'layer')
    return lay[1] if lay and len(lay) > 1 else ''


# ------------------------------------------------------------------------- text
def text_quad(string, anchor, angle, effects, pad=0.0):
    font = kid(effects or [], 'font') or []
    sh, sw = (nums(kid(font, 'size')) if kid(font, 'size') else [1.0, 1.0])[:2]
    th = nums(kid(font, 'thickness'), 1)[0] if kid(font, 'thickness') else 0.15 * min(sw, sh)
    lines = string.split('\n')
    w = max(len(s) for s in lines) * sw * CHAR_ADVANCE + th + 2 * pad
    h = sh * (1 + LINE_PITCH * (len(lines) - 1)) + th + 2 * pad
    just = [c for c in (kid(effects or [], 'justify') or [])[1:] if isinstance(c, str)]
    mirror = 'mirror' in just
    ox = (w / 2 if 'left' in just else -w / 2 if 'right' in just else 0.0) * (-1 if mirror else 1)
    oy = h / 2 if 'top' in just else -h / 2 if 'bottom' in just else 0.0
    quad = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        rx, ry = rot(ox + dx, oy + dy, angle)
        quad.append((anchor[0] + rx, anchor[1] + ry))
    return quad


def centre(poly):
    return (round(sum(p[0] for p in poly) / len(poly), 2), round(sum(p[1] for p in poly) / len(poly), 2))


# ------------------------------------------------------------------------ board
def load(board_path):
    with open(board_path, encoding='utf-8') as fh:
        root = parse(fh.read())
    edge_shapes, texts, bodies, footprints = [], [], [], []

    def ident(x, y):
        return (x, y)

    for node in root:
        if not isinstance(node, list) or not node:
            continue
        head = node[0]
        if head.startswith('gr_') and layer_of(node) == 'Edge.Cuts':
            edge_shapes.append(shape_points(node, ident))
        elif head == 'gr_text' and layer_of(node).endswith('.SilkS') and not flag(node, 'hide'):
            at = nums(kid(node, 'at'), 3)
            texts.append({'owner': 'board', 'what': 'text', 'string': node[1], 'layer': layer_of(node),
                          'quad': text_quad(node[1], at[:2], at[2], kid(node, 'effects'))})
        elif head == 'footprint':
            at = nums(kid(node, 'at'), 3)
            fx, fy, frot = at[0], at[1], at[2]
            side = layer_of(node)[0]  # F or B

            def xf(lx, ly, fx=fx, fy=fy, frot=frot):
                rx, ry = rot(lx, ly, frot)
                return (fx + rx, fy + ry)

            props = {p[1]: p for p in kids(node, 'property')}
            ref = props['Reference'][2] if 'Reference' in props else '?'
            attr = [c for c in (kid(node, 'attr') or [])[1:] if isinstance(c, str)]
            dnp = 'dnp' in attr
            # printed text: fields plus fp_text items
            items = [(n, p[2], p) for n, p in props.items()]
            items += [('text', t[2].replace('${REFERENCE}', ref), t) for t in kids(node, 'fp_text')]
            for name, string, t in items:
                if not layer_of(t).endswith('.SilkS') or not string:
                    continue
                if flag(t, 'hide') or flag(kid(t, 'effects') or [], 'hide'):
                    continue
                tat = nums(kid(t, 'at'), 3)
                texts.append({'owner': ref, 'what': name.lower(), 'string': string, 'layer': layer_of(t),
                              'quad': text_quad(string, xf(tat[0], tat[1]), tat[2], kid(t, 'effects'))})
            # body outline: Fab graphics, else courtyard, else pads
            local = {'Fab': [], 'CrtYd': [], 'pads': []}
            for g in node:
                if not isinstance(g, list) or not g or not isinstance(g[0], str):
                    continue
                if g[0].startswith('fp_') and g[0] != 'fp_text':
                    lay = layer_of(g)
                    pts, _ = shape_points(g, ident)
                    if lay == 'Edge.Cuts':
                        edge_shapes.append(shape_points(g, xf))
                    for key in ('Fab', 'CrtYd'):
                        if lay == side + '.' + key:
                            local[key] += pts
                elif g[0] == 'pad':
                    pat, size = nums(kid(g, 'at'), 3), nums(kid(g, 'size'))
                    r = math.hypot(size[0], size[1]) / 2 if pat[2] % 90 else 0
                    hw, hh = (r, r) if r else ((size[0] / 2, size[1] / 2) if pat[2] % 180 == 0 else (size[1] / 2, size[0] / 2))
                    local['pads'] += [(pat[0] - hw, pat[1] - hh), (pat[0] + hw, pat[1] + hh)]
            source = next((k for k in ('Fab', 'CrtYd', 'pads') if local[k]), None)
            if source:
                # fp_* coordinates are stored unrotated; pads too
                xs, ys = [p[0] for p in local[source]], [p[1] for p in local[source]]
                body = [xf(min(xs), min(ys)), xf(max(xs), min(ys)), xf(max(xs), max(ys)), xf(min(xs), max(ys))]
                bodies.append({'ref': ref, 'side': side, 'poly': body, 'source': source, 'dnp': dnp})
            models = [{'path': m[1], 'hidden': flag(m, 'hide')} for m in kids(node, 'model')]
            footprints.append({'ref': ref, 'side': side, 'attr': attr, 'models': models,
                               'pads': len(kids(node, 'pad')), 'lib': node[1]})
    return edge_shapes, texts, bodies, footprints


def build_loops(shapes, tol=0.02):
    loops, opens = [], []
    for pts, closed in shapes:
        if len(pts) < 2:
            continue
        (loops if closed else opens).append(list(pts))
    while opens:
        chain = opens.pop()
        grown = True
        while grown:
            grown = False
            for i, seg in enumerate(opens):
                for cand in (seg, seg[::-1]):
                    if math.dist(chain[-1], cand[0]) <= tol:
                        chain += cand[1:]
                        opens.pop(i)
                        grown = True
                        break
                if grown:
                    break
        if math.dist(chain[0], chain[-1]) <= tol:
            chain.pop()
        loops.append(chain)
    loops.sort(key=area, reverse=True)
    return loops


def kicad_vars():
    """Path variables the user configured in KiCad (Preferences > Configure Paths)."""
    out = {}
    roots = [os.path.join(os.environ.get('APPDATA', ''), 'kicad'), os.path.expanduser('~/.config/kicad'),
             os.path.expanduser('~/Library/Preferences/kicad')]
    for cfg in sorted(f for r in roots for f in glob.glob(os.path.join(r, '*', 'kicad_common.json'))):
        try:
            with open(cfg, encoding='utf-8') as fh:
                out.update((json.load(fh).get('environment') or {}).get('vars') or {})
        except (OSError, ValueError):
            pass
    return out


KICAD_VARS = kicad_vars()


def resolve_model(path, board_dir, share):
    def sub(m):
        var = m.group(1)
        if var == 'KIPRJMOD':
            return board_dir
        if os.environ.get(var):
            return os.environ[var]
        if KICAD_VARS.get(var):
            return KICAD_VARS[var]
        if re.fullmatch(r'KI(CAD\d*|SYS)_?3DMODEL_DIR|KISYS3DMOD', var) and share:
            return os.path.join(share, '3dmodels')
        return m.group(0)
    full = re.sub(r'\$\{([^}]+)\}|\$\(([^)]+)\)', lambda m: sub(m) if m.group(1) else m.group(0), path)
    return os.path.normpath(full if os.path.isabs(full) or '${' in full else os.path.join(board_dir, full))


def default_share():
    hits = sorted(glob.glob(r'C:\Program Files\KiCad\*\share\kicad') + glob.glob('/usr/share/kicad')
                  + glob.glob('/Applications/KiCad/KiCad.app/Contents/SharedSupport'))
    return hits[-1] if hits else None


NO_BODY = re.compile(r'^(TP|NT|JP|H|MH|FID|LOGO|G|REF\*\*)\d*$')


def audit(board_path, edge_margin, share, min_frac):
    board_dir = os.path.dirname(os.path.abspath(board_path))
    edge_shapes, texts, bodies, footprints = load(board_path)
    loops = build_loops(edge_shapes)
    findings = []
    if not loops:
        findings.append({'check': 'edge', 'severity': 'error', 'item': 'board',
                         'detail': 'no closed Edge.Cuts outline found; edge check not run'})
    for t in texts:
        q = t['quad']
        label = f"{t['owner']} {t['what']} \"{t['string']}\"".replace('\n', ' / ')
        t['label'] = label
        if loops:
            outer, holes = loops[0], loops[1:]
            out = [p for p in q if not inside(p, outer) or any(inside(p, h) for h in holes)]
            dist = min(seg_seg(q[i], q[(i + 1) % 4], lp[j], lp[(j + 1) % len(lp)])
                       for lp in loops for j in range(len(lp)) for i in range(4))
            if out or dist < edge_margin:
                findings.append({'check': 'edge', 'severity': 'error', 'item': label, 'layer': t['layer'],
                                 'at': centre(q),
                                 'detail': ('crosses the board edge' if out or dist == 0 else
                                            f'{dist:.2f} mm from the board edge (< {edge_margin} mm)')})
        ta = area(q)
        for b in bodies:
            if b['side'] != t['layer'][0]:
                continue
            frac = area(clip(q, b['poly'])) / ta if ta else 0
            if frac >= min_frac:
                own = ' (its own part)' if b['ref'] == t['owner'] else ''
                findings.append({'check': 'hidden', 'severity': 'info' if b['dnp'] else 'error', 'item': label,
                                 'layer': t['layer'], 'at': centre(q),
                                 'detail': f"{frac:.0%} under the body of {b['ref']}{own}"
                                           + (' - part is DNP' if b['dnp'] else '')})
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            if a['layer'] != b['layer']:
                continue
            ov = area(clip(a['quad'], b['quad']))
            if ov > 0.03:
                findings.append({'check': 'overlap', 'severity': 'error', 'item': a['label'], 'layer': a['layer'],
                                 'at': centre(a['quad']), 'detail': f"overlaps {b['label']} by {ov:.2f} mm2"})
    for f in footprints:
        shown = [m for m in f['models'] if not m['hidden']]
        if not f['models']:
            quiet = NO_BODY.match(f['ref']) or 'board_only' in f['attr'] or f['pads'] == 0
            findings.append({'check': 'model', 'severity': 'info' if quiet else 'warning', 'item': f['ref'],
                             'detail': f"no 3D model assigned ({f['lib']})"})
            continue
        if not shown:
            findings.append({'check': 'model', 'severity': 'warning', 'item': f['ref'],
                             'detail': 'every 3D model entry is hidden'})
        for m in shown:
            full = resolve_model(m['path'], board_dir, share)
            if '${' in full:
                findings.append({'check': 'model', 'severity': 'warning', 'item': f['ref'],
                                 'detail': f"path variable not resolved, file not checked: {m['path']}"})
            elif not os.path.isfile(full):
                stem = os.path.splitext(full)[0]
                alt = [e for e in ('.step', '.stp', '.wrl', '.STEP') if os.path.isfile(stem + e)]
                findings.append({'check': 'model', 'severity': 'error', 'item': f['ref'],
                                 'detail': f"3D model file not found: {m['path']}"
                                           + (f" (only {', '.join(alt)} exists)" if alt else '')})
    stats = {'silk_texts': len(texts), 'footprints': len(footprints), 'outline_loops': len(loops)}
    return findings, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('board')
    ap.add_argument('--json', action='store_true', help='print findings as JSON')
    ap.add_argument('--edge-margin', type=float, default=0.15, help='minimum text-to-edge distance, mm')
    ap.add_argument('--min-hidden', type=float, default=0.05, help='smallest covered fraction of a text to report')
    ap.add_argument('--kicad-share', default=None, help="KiCad share folder holding '3dmodels'")
    args = ap.parse_args()
    if not os.path.isfile(args.board):
        print(f'board not found: {args.board}', file=sys.stderr)
        return 2
    findings, stats = audit(args.board, args.edge_margin, args.kicad_share or default_share(), args.min_hidden)
    if args.json:
        print(json.dumps({'board': args.board, 'stats': stats, 'findings': findings}, indent=1))
    else:
        print(f"{os.path.basename(args.board)}: {stats['silk_texts']} visible silkscreen texts, "
              f"{stats['footprints']} footprints, {stats['outline_loops']} outline loop(s)")
        for check in ('edge', 'hidden', 'overlap', 'model'):
            rows = [f for f in findings if f['check'] == check]
            print(f"\n[{check}] {len(rows)} finding(s)")
            for f in rows:
                where = f" @ {f['at'][0]}/{f['at'][1]} {f['layer']}" if 'at' in f else ''
                print(f"  {f['severity']:<7} {f['item']}{where}: {f['detail']}")
    return 1 if any(f['severity'] != 'info' for f in findings) else 0


if __name__ == '__main__':
    sys.exit(main())
