# Viewport-Resolution Wind Particles — Implementation Plan

## Problem
Wind U/V texture is a fixed 512x512 canvas (4 tiles at zoom 3) covering all CONUS.
When zoomed in past z7, the viewport covers only 3-5 data pixels → all particles
move identically → "christmas tree" effect.

## Solution
Dynamically load higher-zoom wind U/V tiles matching the current viewport, stitch
into a GPU texture. Particles always have hundreds of data points across the screen.

## Verified: Wind tiles exist at all zoom levels
- Zoom 2-6 confirmed on R2 (tested `wind-u/4/`, `wind-u/5/`, `wind-u/6/`)
- Tile format: `${TILE_BASE}/wind-u/{z}/{x}/{y}.png` (Slippy Map XYZ)

## Tile Counts per Zoom (full CONUS)
| Zoom | Tiles | Canvas Size | Notes |
|------|-------|-------------|-------|
| 2 | 2 | 512px | Minimal |
| 3 | 4 | 512px | Current setup |
| 4 | 8 | 1024px | Fast stitch |
| 5 | 28 | 1792px | Good detail |
| 6 | 84 | 3072px | Max available, heavy |

**But we don't load all CONUS** — only the viewport's tiles. At any zoom level,
the viewport covers ~4-16 tiles, so canvas size stays manageable (512-1024px).

## Tile Zoom Selection
```
Map Zoom → Tile Zoom
  0-3    →    3  (current behavior)
  4      →    4  (2x resolution bump)
  5      →    5  (4x resolution)
  6+     →    6  (max available, 16x resolution vs current)
```
Formula: `tileZoom = clamp(floor(mapZoom), 3, 6)`

## Implementation Phases

### Phase 1: Generalize loadWind() — ~2 hours
**Currently:** Hardcoded to load 4 tiles at zoom 3, stitch into 512x512 canvas.
**Change:** Accept tileZoom param, calculate which tiles cover viewport, load + stitch.

Key functions to write:
1. `getTileCoverage(bounds, tileZoom)` → [{x, y, z}, ...]
2. `stitchTiles(tiles, layer)` → Promise<{imageData, bounds}>
3. Refactor `loadWind(gl)` → `loadWind(gl, tileZoom)`

Bounds calculation:
```js
const gridSize = Math.pow(2, tileZoom);
const toX = lng => (lng + 180) / 360;
const toY = lat => {
  const r = lat * Math.PI / 180;
  return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2;
};
// viewport bounds → tile range
const tileX0 = Math.floor(toX(west) * gridSize);
const tileY0 = Math.floor(toY(north) * gridSize);
// etc.
```

### Phase 2: Dynamic bounds in shader — ~1 hour
**Currently:** `WB = { x0: 1/8, y0: 2/8, x1: 3/8, y1: 4/8 }` (hardcoded zoom 3 bounds)
**Change:** `windTextureBounds` variable updated every time wind tiles reload.

Shader already has `u_wind_min` / `u_wind_max` uniforms — just pass the new bounds.
Particle respawn bounds (`u_view_min/max`) already use `getViewBounds()` — no change.

### Phase 3: Trigger reload on zoom/pan — ~2 hours
- On `moveend`, check if tile coverage changed
- Debounce: 200ms delay (avoid reload during animations)
- Only reload when tileZoom changes OR viewport pans past current texture bounds
- Clear trails + redistribute particles after reload (already exists)

### Phase 4: Remove fade-out hack — ~15 min
- Remove the z6→z8 opacity fade since particles will work at all zooms
- May want to keep a subtle fade at z2-3 (too zoomed out, particles too tiny)

### Phase 5: Performance + caching — ~2 hours
- Cache stitched textures keyed by tile coverage hash
- Reuse texture if viewport shifts within current texture bounds
- Use `Promise.allSettled()` so one 404 doesn't kill the batch
- Measure: canvas stitch time, GPU upload time, frame rate impact

## Key Architecture Decisions

### Why not load tiles directly as MapLibre sources?
MapLibre can load raster tiles, but the particle system needs raw pixel data
(ImageData) on the GPU as a texture. MapLibre's raster layers are rendered
as map layers, not exposed as textures. We must load/stitch ourselves.

### Why canvas stitch instead of multi-texture lookup?
Could pass 4-16 separate textures to the shader and sample the right one.
But WebGL 1 has limited texture units (8-16), and the shader math to select
the right texture per pixel is messy. Single stitched canvas is simpler.

### Why not use WebGL 2 texture arrays?
Would be cleaner but drops Safari < 15 support. Canvas stitch works everywhere.

## Files to Modify
- `docs/index.html` (and mirror to `frontend/index.html`)
  - `loadWind()` function (~line 2051)
  - `WB` constant → `windTextureBounds` variable
  - `render()` function — uniform updates
  - Map event handlers — zoom/pan detection
  - Remove opacity fade-out hack

## Testing Checklist
- [ ] Zoom 3-4: identical to current (regression check)
- [ ] Zoom 5-6: visibly more particle variation
- [ ] Zoom 7-8: particles show local wind patterns (the goal)
- [ ] Zoom 9+: still reasonable (z6 data, 64x better than z3)
- [ ] Pan at same zoom: particles don't flicker during reload
- [ ] Mobile: canvas stitch doesn't lock UI thread
- [ ] Tile 404: graceful fallback, no crash

## Status
- [x] Plan written (2026-03-09)
- [x] Quick fix deployed: fade particles out past z7 (temporary)
- [x] Phase 1: Generalize loadWind() (2026-03-09)
- [x] Phase 2: Dynamic shader bounds (2026-03-09)
- [x] Phase 3: Zoom/pan trigger (2026-03-09)
- [x] Phase 4: Remove fade hack (2026-03-09)
- [ ] Phase 5: Caching + performance (deferred — test first)
