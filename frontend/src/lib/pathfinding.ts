export type Cell = { r: number; c: number };
export type Algorithm = "astar" | "dijkstra" | "bidirectional" | "highway";

export type StepEvent =
  | { type: "visit"; cell: Cell }
  | { type: "frontier"; cell: Cell };

export type SearchResult = {
  steps: StepEvent[];
  path: Cell[];
  found: boolean;
};

const key = (c: Cell) => `${c.r},${c.c}`;

function neighbors(c: Cell, rows: number, cols: number, walls: Set<string>): Cell[] {
  const out: Cell[] = [];
  const dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  for (const [dr, dc] of dirs) {
    const nr = c.r + dr;
    const nc = c.c + dc;
    if (nr < 0 || nc < 0 || nr >= rows || nc >= cols) continue;
    if (walls.has(`${nr},${nc}`)) continue;
    out.push({ r: nr, c: nc });
  }
  return out;
}

// "Highways" are evenly spaced rows/cols that are much cheaper to traverse,
// simulating arterial roads in a road network.
function isHighway(cell: Cell, rows: number, cols: number) {
  const hwRow = Math.floor(rows / 2);
  const hwCol = Math.floor(cols / 2);
  return (
    cell.r === hwRow ||
    cell.c === hwCol ||
    cell.r === Math.floor(rows / 4) ||
    cell.r === Math.floor((rows * 3) / 4) ||
    cell.c === Math.floor(cols / 4) ||
    cell.c === Math.floor((cols * 3) / 4)
  );
}

function edgeCost(from: Cell, to: Cell, rows: number, cols: number, useHighway: boolean) {
  if (!useHighway) return 1;
  // Cheaper if both endpoints are on a highway, slightly cheaper if one is.
  const a = isHighway(from, rows, cols);
  const b = isHighway(to, rows, cols);
  if (a && b) return 0.25;
  if (a || b) return 0.6;
  return 1;
}

function manhattan(a: Cell, b: Cell) {
  return Math.abs(a.r - b.r) + Math.abs(a.c - b.c);
}

// Simple binary-heap priority queue
class PQ<T> {
  private h: { p: number; v: T }[] = [];
  push(p: number, v: T) {
    this.h.push({ p, v });
    let i = this.h.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (this.h[parent].p <= this.h[i].p) break;
      [this.h[parent], this.h[i]] = [this.h[i], this.h[parent]];
      i = parent;
    }
  }
  pop(): T | undefined {
    if (!this.h.length) return undefined;
    const top = this.h[0].v;
    const last = this.h.pop()!;
    if (this.h.length) {
      this.h[0] = last;
      let i = 0;
      const n = this.h.length;
      while (true) {
        const l = 2 * i + 1;
        const r = 2 * i + 2;
        let s = i;
        if (l < n && this.h[l].p < this.h[s].p) s = l;
        if (r < n && this.h[r].p < this.h[s].p) s = r;
        if (s === i) break;
        [this.h[s], this.h[i]] = [this.h[i], this.h[s]];
        i = s;
      }
    }
    return top;
  }
  get size() { return this.h.length; }
}

export function runSearch(
  algorithm: Algorithm,
  start: Cell,
  goal: Cell,
  rows: number,
  cols: number,
  walls: Set<string>,
): SearchResult {
  if (algorithm === "bidirectional") {
    return runBidirectional(start, goal, rows, cols, walls);
  }

  const steps: StepEvent[] = [];
  const cameFrom = new Map<string, string>();
  const gScore = new Map<string, number>();
  const visited = new Set<string>();
  gScore.set(key(start), 0);
  const useHighway = algorithm === "highway";

  const pq = new PQ<Cell>();
  pq.push(0, start);
  while (pq.size) {
    const cur = pq.pop()!;
    const ck = key(cur);
    if (visited.has(ck)) continue;
    visited.add(ck);
    steps.push({ type: "visit", cell: cur });
    if (cur.r === goal.r && cur.c === goal.c) break;
    const curG = gScore.get(ck) ?? Infinity;
    for (const n of neighbors(cur, rows, cols, walls)) {
      const nk = key(n);
      const tentative = curG + edgeCost(cur, n, rows, cols, useHighway);
      if (tentative < (gScore.get(nk) ?? Infinity)) {
        gScore.set(nk, tentative);
        cameFrom.set(nk, ck);
        const h = algorithm === "astar" ? manhattan(n, goal) : 0;
        pq.push(tentative + h, n);
        steps.push({ type: "frontier", cell: n });
      }
    }
  }

  const path: Cell[] = [];
  let curK: string | undefined = key(goal);
  const found = cameFrom.has(curK) || (start.r === goal.r && start.c === goal.c);
  if (found) {
    while (curK) {
      const [r, c] = curK.split(",").map(Number);
      path.unshift({ r, c });
      if (curK === key(start)) break;
      curK = cameFrom.get(curK);
    }
  }
  return { steps, path, found };
}

function runBidirectional(
  start: Cell,
  goal: Cell,
  rows: number,
  cols: number,
  walls: Set<string>,
): SearchResult {
  const steps: StepEvent[] = [];
  const sCame = new Map<string, string>();
  const gCame = new Map<string, string>();
  const sVisited = new Set<string>([key(start)]);
  const gVisited = new Set<string>([key(goal)]);
  const sQ: Cell[] = [start];
  const gQ: Cell[] = [goal];
  let meet: string | null = null;

  while (sQ.length && gQ.length && !meet) {
    // expand one layer from start
    const sNext: Cell[] = [];
    for (const cur of sQ) {
      steps.push({ type: "visit", cell: cur });
      if (gVisited.has(key(cur))) { meet = key(cur); break; }
      for (const n of neighbors(cur, rows, cols, walls)) {
        const k = key(n);
        if (sVisited.has(k)) continue;
        sVisited.add(k);
        sCame.set(k, key(cur));
        steps.push({ type: "frontier", cell: n });
        sNext.push(n);
        if (gVisited.has(k)) { meet = k; break; }
      }
      if (meet) break;
    }
    if (meet) break;
    sQ.length = 0; sQ.push(...sNext);

    // expand one layer from goal
    const gNext: Cell[] = [];
    for (const cur of gQ) {
      steps.push({ type: "visit", cell: cur });
      if (sVisited.has(key(cur))) { meet = key(cur); break; }
      for (const n of neighbors(cur, rows, cols, walls)) {
        const k = key(n);
        if (gVisited.has(k)) continue;
        gVisited.add(k);
        gCame.set(k, key(cur));
        steps.push({ type: "frontier", cell: n });
        gNext.push(n);
        if (sVisited.has(k)) { meet = k; break; }
      }
      if (meet) break;
    }
    gQ.length = 0; gQ.push(...gNext);
  }

  const path: Cell[] = [];
  let found = false;
  if (meet) {
    found = true;
    // start -> meet
    const left: Cell[] = [];
    let k: string | undefined = meet;
    while (k) {
      const [r, c] = k.split(",").map(Number);
      left.unshift({ r, c });
      if (k === key(start)) break;
      k = sCame.get(k);
    }
    // meet -> goal
    const right: Cell[] = [];
    k = gCame.get(meet);
    while (k) {
      const [r, c] = k.split(",").map(Number);
      right.push({ r, c });
      if (k === key(goal)) break;
      k = gCame.get(k);
    }
    path.push(...left, ...right);
  }
  return { steps, path, found };
}