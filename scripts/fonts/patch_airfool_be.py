"""Add Belarusian і/І and ў/Ў to Airfool.

і/І reuse the Latin i/I glyphs (identical shape).
ў/Ў = у glyph + the breve taken from й (contours above the и height), centred over у.
Outputs airfool-be.ttf and airfool-be.woff2.
Usage: python patch_airfool_be.py static/fonts/airfool.ttf out_dir
"""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen

src, out = Path(sys.argv[1]), Path(sys.argv[2])
f = TTFont(src)
gs, glyf, hmtx = f.getGlyphSet(), f["glyf"], f["hmtx"]
cmap = f.getBestCmap()
name = lambda ch: cmap[ord(ch)]

def contours(gname):
    rec = DecomposingRecordingPen(gs)
    gs[gname].draw(rec)
    parts, cur = [], []
    for op, args in rec.value:
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            parts.append(cur); cur = []
    return parts

def bounds(ops):
    bp = BoundsPen(gs)
    for op, args in ops:
        getattr(bp, op)(*args)
    return bp.bounds

def replay(pen, ops):
    for op, args in ops:
        getattr(pen, op)(*args)

# breve = contours of й reaching above the top of и
i_top = bounds([o for c in contours(name("и")) for o in c])[3]
breve = [c for c in contours(name("й")) if bounds(c)[3] > i_top + 50]
assert breve, "breve not found"
bx0, _, bx1, _ = bounds([o for c in breve for o in c])

for base_ch, new_cp in (("у", 0x045E), ("У", 0x040E)):
    base = name(base_ch)
    ux0, _, ux1, _ = bounds([o for c in contours(base) for o in c])
    dx = (ux0 + ux1) / 2 - (bx0 + bx1) / 2
    pen = TTGlyphPen(gs)
    for c in contours(base):
        replay(pen, c)
    tp = TransformPen(pen, (1, 0, 0, 1, dx, 0))
    for c in breve:
        replay(tp, c)
    gname = f"uni{new_cp:04X}"
    glyf[gname] = pen.glyph()
    hmtx[gname] = hmtx[base]
    f.setGlyphOrder(f.getGlyphOrder() + [gname]) if gname not in f.getGlyphOrder() else None
    for t in f["cmap"].tables:
        if t.isUnicode():
            t.cmap[new_cp] = gname

for latin, cp in (("i", 0x0456), ("I", 0x0406)):
    for t in f["cmap"].tables:
        if t.isUnicode():
            t.cmap[cp] = name(latin)

f["maxp"].numGlyphs = len(f.getGlyphOrder())
out.mkdir(parents=True, exist_ok=True)
f.save(out / "airfool-be.ttf")
f.flavor = "woff2"
f.save(out / "airfool-be.woff2")
print("ok:", out / "airfool-be.ttf", out / "airfool-be.woff2")
