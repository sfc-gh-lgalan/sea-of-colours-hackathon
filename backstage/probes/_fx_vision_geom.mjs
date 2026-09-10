/**
 * Geometry check for the vision-border ring builder.
 *
 * Pulls the REAL `_visionRings` / `_collapseCollinear` / `_ringsToPath`
 * source straight out of `server/static/app.js` and runs it, so this
 * cannot drift from what ships. app.js is one big IIFE with no exports,
 * hence the brace-matched extraction rather than an import.
 *
 * Scratch harness, like the other `scripts/_*` files — not part of pytest.
 *
 *   node backstage/probes/_fx_vision_geom.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = readFileSync(join(root, "server/static/app.js"), "utf8");

/** Extract `function NAME(...) { ... }` by brace matching. */
function grab(name) {
  const at = src.indexOf(`function ${name}(`);
  if (at < 0) throw new Error(`could not find function ${name} in app.js`);
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    const ch = src[j];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(at, j + 1);
    }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}

// `window` is a parameter rather than a global so the footprint helpers can
// be handed a rules block per test (they read the engine's dials off it).
// eslint-disable-next-line no-new-func
const build = new Function(
  "window",
  `${grab("_visionRings")}\n${grab("_collapseCollinear")}\n${grab("_ringsToPath")}\n` +
  `${grab("_ringIsClosed")}\n${grab("_probeRadius")}\n${grab("_empRadius")}\n` +
  `${grab("_actionFootprint")}\n${grab("_actionHasArea")}\n` +
  `return { _visionRings, _collapseCollinear, _ringsToPath, _ringIsClosed,` +
  ` _probeRadius, _empRadius, _actionFootprint, _actionHasArea };`,
);
const ctx = build({});

let failures = 0;
function check(label, cond, detail) {
  if (cond) {
    console.log(`  ok   ${label}`);
  } else {
    failures++;
    console.log(`  FAIL ${label}${detail ? `  — ${detail}` : ""}`);
  }
}

/** Build an inSet predicate from a list of "x,y" strings. */
function setOf(list) {
  const s = new Set(list);
  return (x, y) => s.has(`${x},${y}`);
}

/** Count boundary edges the naive way, to prove the walker loses none. */
function edgeCount(inSet, w, h) {
  let n = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (!inSet(x, y)) continue;
      if (!inSet(x, y - 1)) n++;
      if (!inSet(x + 1, y)) n++;
      if (!inSet(x, y + 1)) n++;
      if (!inSet(x - 1, y)) n++;
    }
  }
  return n;
}

/** Total unit-length segments across all rings (before collapsing). */
function ringEdgeTotal(rings) {
  let n = 0;
  for (const r of rings) {
    for (let i = 0; i < r.length - 2; i += 2) {
      n += Math.abs(r[i + 2] - r[i]) + Math.abs(r[i + 3] - r[i + 1]);
    }
  }
  return n;
}

function ringsClosed(rings) {
  return rings.every((r) => r[0] === r[r.length - 2] && r[1] === r[r.length - 1]);
}

console.log("\nvision border geometry\n");

// 1 — a single cell is one square.
{
  const inSet = setOf(["3,3"]);
  const rings = ctx._visionRings(inSet, 8, 8);
  check("single cell -> 1 ring", rings.length === 1, `got ${rings.length}`);
  check("single cell ring is closed", ringsClosed(rings));
  const d = ctx._ringsToPath(rings);
  check("single cell -> 4 corners", (d.match(/[ML]/g) || []).length === 4, d);
}

// 2 — a solid block is one ring with four corners, not one ring per cell.
{
  const cells = [];
  for (let y = 2; y < 6; y++) for (let x = 2; x < 7; x++) cells.push(`${x},${y}`);
  const inSet = setOf(cells);
  const rings = ctx._visionRings(inSet, 10, 10);
  check("4x5 block -> 1 ring", rings.length === 1, `got ${rings.length}`);
  const d = ctx._ringsToPath(rings);
  check("4x5 block collapses to 4 corners", (d.match(/[ML]/g) || []).length === 4, d);
}

// 3 — disjoint regions each get their own ring.
{
  const inSet = setOf(["1,1", "8,8"]);
  const rings = ctx._visionRings(inSet, 12, 12);
  check("two disjoint cells -> 2 rings", rings.length === 2, `got ${rings.length}`);
}

// 4 — a hole must produce its own inner ring (the donut case).
{
  const cells = [];
  for (let y = 1; y < 6; y++) {
    for (let x = 1; x < 6; x++) {
      if (x === 3 && y === 3) continue; // punch the middle out
      cells.push(`${x},${y}`);
    }
  }
  const inSet = setOf(cells);
  const rings = ctx._visionRings(inSet, 8, 8);
  check("donut -> outer + inner ring", rings.length === 2, `got ${rings.length}`);
  check("donut rings closed", ringsClosed(rings));
}

// 5 — nothing is lost or drawn twice, on a shape with a hole AND a pinch.
{
  const cells = [];
  for (let y = 1; y < 6; y++) {
    for (let x = 1; x < 6; x++) {
      if (x === 3 && y === 3) continue;
      cells.push(`${x},${y}`);
    }
  }
  cells.push("6,6"); // diagonal touch at a corner = the pinch case
  const inSet = setOf(cells);
  const rings = ctx._visionRings(inSet, 10, 10);
  const want = edgeCount(inSet, 10, 10);
  const got = ringEdgeTotal(rings);
  check("every boundary edge is drawn exactly once", want === got, `want ${want}, got ${got}`);
  check("pinched shape rings all closed", ringsClosed(rings));
}

// 6 — the real shape this draws: a probe's Euclidean disk.
{
  const r = 4;
  const cells = [];
  for (let y = 0; y < 20; y++) {
    for (let x = 0; x < 20; x++) {
      const dx = x - 10;
      const dy = y - 10;
      if (dx * dx + dy * dy <= r * r) cells.push(`${x},${y}`);
    }
  }
  const inSet = setOf(cells);
  const rings = ctx._visionRings(inSet, 20, 20);
  check("probe disk -> 1 ring", rings.length === 1, `got ${rings.length}`);
  check("probe disk ring closed", ringsClosed(rings));
  const want = edgeCount(inSet, 20, 20);
  check("probe disk edges all drawn", want === ringEdgeTotal(rings));
  console.log(`       (disk is ${cells.length} cells, ${want} boundary edges)`);
}

// 7 — two overlapping disks (two probes) still make one clean region.
{
  const inDisk = (x, y, cx, cy) => (x - cx) ** 2 + (y - cy) ** 2 <= 16;
  const cells = [];
  for (let y = 0; y < 24; y++) {
    for (let x = 0; x < 24; x++) {
      if (inDisk(x, y, 8, 10) || inDisk(x, y, 13, 10)) cells.push(`${x},${y}`);
    }
  }
  const inSet = setOf(cells);
  const rings = ctx._visionRings(inSet, 24, 24);
  check("two overlapping disks -> 1 ring", rings.length === 1, `got ${rings.length}`);
  check("union edges all drawn", edgeCount(inSet, 24, 24) === ringEdgeTotal(rings));
}

// 8 — a region touching the board edge must still close (no open path).
{
  const cells = [];
  for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) cells.push(`${x},${y}`);
  const inSet = setOf(cells);
  const rings = ctx._visionRings(inSet, 6, 6);
  check("corner-of-board region -> 1 closed ring", rings.length === 1 && ringsClosed(rings));
  const d = ctx._ringsToPath(rings);
  check("board-edge region has 4 corners", (d.match(/[ML]/g) || []).length === 4, d);
}

// 9 — collinear collapse is lossless: same shape, fewer vertices.
{
  const cells = [];
  for (let x = 1; x < 11; x++) cells.push(`${x},4`);
  const rings = ctx._visionRings(setOf(cells), 14, 8);
  const before = rings[0].length / 2 - 1;
  const d = ctx._ringsToPath(rings);
  const after = (d.match(/[ML]/g) || []).length;
  check("1x10 strip collapses 22 vertices -> 4", before === 22 && after === 4,
    `before ${before}, after ${after}`);
}

// 10 — empty input draws nothing at all.
{
  const rings = ctx._visionRings(() => false, 8, 8);
  check("empty set -> no rings", rings.length === 0);
  check("empty set -> empty path", ctx._ringsToPath(rings) === "");
}

// 11 — v1.22 `skipEdge`: the clipped-rival case. A rival band always sits
// inside your own live set, so the stretches of its outline that coincide
// with YOUR outline are dropped; what is left is an open chain marking where
// their sight stops on ground you can see.
{
  const W = 12;
  const H = 8;
  // Your live set: columns 2..8. Their clipped band: columns 2..5, i.e. it
  // runs off your left edge but ends inside your view on the right.
  const own = (x, y) => x >= 2 && x <= 8 && y >= 2 && y <= 5;
  const band = (x, y) => own(x, y) && x <= 5;
  const skip = (x, y, dir) => {
    const nx = dir === "e" ? x + 1 : dir === "w" ? x - 1 : x;
    const ny = dir === "s" ? y + 1 : dir === "n" ? y - 1 : y;
    if (nx < 0 || ny < 0 || nx >= W || ny >= H) return true;
    return !own(nx, ny);
  };

  const full = ctx._visionRings(band, W, H);
  check("clipped band unskipped -> 1 closed ring",
    full.length === 1 && ringsClosed(full));

  const open = ctx._visionRings(band, W, H, skip);
  const total = open.reduce((a, r) => a + r.length / 2 - 1, 0);
  check("skipEdge leaves only the interior boundary",
    open.length >= 1 && open.every((r) => !ctx._ringIsClosed(r)),
    `rings=${open.length} closed=${open.map((r) => ctx._ringIsClosed(r))}`);
  // Their edge inside your view is the 4-cell column x=5 -> 4 unit edges.
  check("interior chain is the 4 rows of their east edge", total === 4,
    `got ${total} segments`);

  const d = ctx._ringsToPath(open);
  check("open chain emits no Z", !d.includes("Z"), d);
  check("open chain collapses to one segment",
    (d.match(/[ML]/g) || []).length === 2, d);

  // And the degenerate case the fix is FOR: when their disk covers
  // everything you can see, there is no interior edge left to draw.
  const covers = ctx._visionRings(own, W, H, skip);
  check("band covering all your sight draws nothing", covers.length === 0,
    `got ${covers.length} rings`);
  check("...and therefore an empty path", ctx._ringsToPath(covers) === "");
}

// 12 — v1.23 order footprints. The shapes are the client's copy of three
// engine helpers, so the counts are pinned here and the exact cell sets are
// diffed against the engine itself by backstage/probes/_fx_aoe.py.
{
  const W = 40;
  const H = 28;
  const size = (a, x, y) => ctx._actionFootprint(a, x, y, W, H).length;

  check("probe is a 49-cell r4 euclidean disk", size("probe", 20, 14) === 49,
    `got ${size("probe", 20, 14)}`);
  check("EMP is a 13-cell r2 manhattan diamond", size("emp_launch", 20, 14) === 13,
    `got ${size("emp_launch", 20, 14)}`);
  // The disk really is round, not a square: r4 Chebyshev would be 81.
  const disk = ctx._actionFootprint("probe", 20, 14, W, H);
  check("disk is round, not a chebyshev square",
    !disk.some(([x, y]) => Math.abs(x - 20) === 4 && Math.abs(y - 14) === 4));

  // Clipping at the rim is the case that decides whether the tooltip's
  // "wasted" figure means anything. A corner keeps a quarter-ish of the disk.
  // 5 + 4 + 4 + 3 + 1 along the rows the quadrant keeps.
  check("corner probe clips to 17 cells", size("probe", 0, 0) === 17,
    `got ${size("probe", 0, 0)}`);
  // The EMP diamond loses three of its four points in a corner.
  check("corner EMP clips to 6 cells", size("emp_launch", 0, 0) === 6,
    `got ${size("emp_launch", 0, 0)}`);
  check("nothing lands off the board",
    ctx._actionFootprint("probe", 0, 0, W, H)
      .every(([x, y]) => x >= 0 && y >= 0 && x < W && y < H));

  // Retuning a dial must move the drawn shape — that is the whole reason
  // these read `window` instead of a literal.
  const tuned = build({ __SOC_PROBE_RADIUS__: 2, __SOC_EMP_RADIUS__: 3 });
  check("probe radius tracks meta.rules",
    tuned._actionFootprint("probe", 20, 14, W, H).length === 13,
    `got ${tuned._actionFootprint("probe", 20, 14, W, H).length}`);
  check("EMP radius tracks weapon_specs",
    tuned._actionFootprint("emp_launch", 20, 14, W, H).length === 25,
    `got ${tuned._actionFootprint("emp_launch", 20, 14, W, H).length}`);
  // Only the area orders get an outline; a step/drop already carries a
  // path badge on its single cell. v1.31 — mine_lay was the third, and
  // must now claim nothing: a retired order that still drew a footprint
  // would promise the player an effect the engine refuses.
  check("only probe/EMP claim an area",
    ["probe", "emp_launch"].every(ctx._actionHasArea)
    && !["step", "drop", "pickup", "wait", "chaff_flare", "mine_lay"]
      .some(ctx._actionHasArea));

  // And the footprint feeds the same ring builder the borders use.
  const mask = new Set(disk.map(([x, y]) => `${x},${y}`));
  const rings = ctx._visionRings((x, y) => mask.has(`${x},${y}`), W, H);
  check("disk outlines as one closed ring",
    rings.length === 1 && ringsClosed(rings), `rings=${rings.length}`);
}

console.log(
  failures ? `\n${failures} FAILURE(S)\n` : "\nall geometry checks passed\n",
);
process.exit(failures ? 1 : 0);
