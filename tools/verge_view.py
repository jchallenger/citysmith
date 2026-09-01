"""Two gabled wings side by side from the SHIPPED `_lay_gabled_wing`:
one with the verge piece, one without, small enough for one frame.

**This exists because a town chunk cannot be framed from its own anchor.**
Every chunk carries the whole map's registration box, so it anchors on the
map's centre and its own geometry lands wherever it sits in the map -- up to a
hundred tiles from the cursor, against a camera that caps at a ~40-tile frame.
Pasted on a fresh board it leaves bare ground under the cursor and reads
exactly like a Ctrl+V that did nothing, which is how it was diagnosed for four
pastes before a small slab at the same cursor proved the machinery fine.

`--crop` is not the answer either: it re-runs the import, and quarters are
derived from the whole settlement, so cropping a gabled district turns every
wing back into a hip and the feature vanishes from the frame chosen to show
it. So this builds the shape the generator actually builds, at the origin, and
calls `_lay_gabled_wing` itself rather than a copy of it -- a probe that
reimplements what it probes can only tell you about the probe.

The west wing gets no verge and the east one does; a bar of 1 and 2 cells on
the ground says which is which, because a hip is judged in plan and a tally
that only reads at an oblique is a tally that lies.

    python tools/verge_view.py > out/vergeview.slab.txt
    tools\review.ps1 360 -Slab out\vergeview.slab.txt -Name vergeview
"""
import sys

sys.path.insert(0, ".")
from citysmith.build import (_lay_gabled_wing, _normalized_whole_tiles,
                             place_tile, roof_offsets)
from citysmith.catalog import load_or_build
from citysmith.palette import MEDIEVAL, Palette, gable_verge
from citysmith.slab import Slab, encode

cat = load_or_build()
byname = {a.name: a for a in cat.assets}
byid = {a.id: a for a in cat.assets}
side, cap = byname["Thatched Roof 01"], byname["Thatched roof flat 01"]
wall, floor = byname["Rural Wall 01"], byname["Rural Floor 01"]
verge = gable_verge(Palette(cat, MEDIEVAL), side)
assert verge is not None, "no verge resolved for the Rural roof"
print(f"# verge = {verge.name} {verge.size_x}x{verge.size_y}x{verge.size_z}",
      file=sys.stderr)

EDGE = roof_offsets(side)[0]
W, D, GAP = 9, 5, 3
COURSES = 2                       # two courses of wall under the roof


class Out:
    def __init__(self): self.placements = []
    def add(self, p): self.placements.append(p)


out = Out()
for i, v in enumerate((None, verge)):        # west bare, east with the verge
    ox = i * (W + GAP)
    cells = {(ox + x, z) for x in range(W) for z in range(D)}
    for x, z in sorted(cells):
        out.add(place_tile(floor, x, z, -floor.size_y))
    for lvl in range(COURSES):
        for x, z in sorted(cells):
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if (x + dx, z + dz) not in cells:
                    out.add(place_tile(wall, x, z, lvl * wall.size_y))
    _lay_gabled_wing(out, cells, "flush", COURSES * wall.size_y, side.size_y,
                     side, cap, EDGE, None, None, None, None, verge=v)
    # A bar of i+1 cells running east, so the pair cannot be misread.
    for k in range(i + 1):
        out.add(place_tile(floor, ox + k, D + 1, -floor.size_y))

print(encode(_normalized_whole_tiles(Slab(out.placements), byid)))
print(f"# {len(out.placements)} placements", file=sys.stderr)
