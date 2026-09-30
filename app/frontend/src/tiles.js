// Imagery tiles are grouped into 8 x 8-tile WebP atlases per survey (processing/make_tiles.py).
// The "fw" protocol serves fw://<survey>/<layer>/<z>/<x>/<y> by cropping the tile out of its
// atlas. Encoded atlases stay cached; decoded bitmaps (16 MB each) are kept in a small LRU.
import * as maplibregl from "maplibre-gl";
import { surveyById } from "./store.js";

const blobCache = new Map();
const bitmapCache = new Map();
const BITMAP_LRU = 10;
let emptyTile = null;

async function empty() {
  if (!emptyTile) emptyTile = await createImageBitmap(new ImageData(1, 1));
  return emptyTile;
}

function loadBlob(url) {
  if (!blobCache.has(url)) {
    blobCache.set(url, fetch(url).then((r) => {
      if (!r.ok) throw new Error(`tile atlas ${url}: ${r.status}`);
      return r.blob();
    }));
  }
  return blobCache.get(url);
}

function loadAtlas(url) {
  if (bitmapCache.has(url)) {
    const p = bitmapCache.get(url);
    bitmapCache.delete(url);
    bitmapCache.set(url, p);
    return p;
  }
  const p = loadBlob(url).then((b) => createImageBitmap(b));
  bitmapCache.set(url, p);
  while (bitmapCache.size > BITMAP_LRU) {
    const [oldest, op] = bitmapCache.entries().next().value;
    bitmapCache.delete(oldest);
    op.then((bm) => bm.close?.()).catch(() => {});
  }
  return p;
}

export function tileUrl(surveyId, layer) {
  return `fw://${encodeURIComponent(surveyId)}/${layer}/{z}/{x}/{y}`;
}

maplibregl.addProtocol("fw", async (params) => {
  const [sid, layer, z, x, y] = params.url.replace("fw://", "").split("/");
  const s = surveyById(decodeURIComponent(sid));
  const L = s?.tiles?.layers?.[layer];
  const hit = L?.tiles[`${z}/${x}/${y}`];
  if (!hit) return { data: await empty() };
  const size = s.tiles.tile_size || 256;
  const atlas = await loadAtlas(`${s.base}tiles/${L.files[hit[0]]}`);
  return { data: await createImageBitmap(atlas, hit[1] * size, hit[2] * size, size, size) };
});
