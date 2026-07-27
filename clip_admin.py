#!/usr/bin/env python3
"""
clip_admin.py — recorta limites administrativos al dominio del modelo.

Entrada: el TopoJSON de es-atlas (datos de lineas limite del IGN) y el
directorio data/ generado por pack_wrf_surface.py, del que se lee la extension
real del dominio a partir de los XLAT/XLONG empaquetados.

Salida, dentro de data/:
  admin.geojson   contornos municipales y provinciales recortados
  places.json     nombres de municipio en su centroide, formato del visor

    python3 clip_admin.py --topo municipalities.json --data data

El TopoJSON de es-atlas se decodifica a mano: son arcos delta-codificados mas
una transformacion afin, no hace falta ninguna dependencia.
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
from PIL import Image


def decode_arcs(topo):
    """Deshace la cuantizacion y el delta-encoding de los arcos."""
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x*sx + tx, y*sy + ty))
        out.append(np.asarray(pts))
    return out


def rings(geom, arcs):
    """Devuelve la lista de anillos (arrays Nx2) de un Polygon o MultiPolygon."""
    def ring(idx_list):
        parts = []
        for i in idx_list:
            a = arcs[~i][::-1] if i < 0 else arcs[i]
            parts.append(a if not parts else a[1:])
        return np.concatenate(parts) if parts else np.empty((0, 2))

    t = geom.get("type")
    if t == "Polygon":
        return [ring(r) for r in geom["arcs"]]
    if t == "MultiPolygon":
        return [ring(r) for poly in geom["arcs"] for r in poly]
    if t == "LineString":
        return [ring(geom["arcs"])]
    if t == "MultiLineString":
        return [ring(r) for r in geom["arcs"]]
    return []


def domain_bbox(datadir, pad=0.06):
    man = json.load(open(os.path.join(datadir, "manifest.json")))
    def dec(name):
        st = man["static"][name]
        px = np.asarray(Image.open(os.path.join(datadir, st["file"])).convert("RGB"))
        q = (px[..., 0].astype(np.uint32) << 8) | px[..., 1].astype(np.uint32)
        return st["vmin"] + q*(st["vmax"] - st["vmin"])/65535.0
    lat, lon = dec("lat"), dec("lon")
    return (float(lon.min())-pad, float(lat.min())-pad,
            float(lon.max())+pad, float(lat.max())+pad), man


def clip_ring(ring, bb):
    """Parte un anillo en tramos contenidos en la bbox. Sin recorte exacto:
    basta con tirar los vertices de fuera y cortar el tramo, porque esto es
    para pintar, no para calcular areas."""
    x0, y0, x1, y1 = bb
    inside = (ring[:, 0] >= x0) & (ring[:, 0] <= x1) & \
             (ring[:, 1] >= y0) & (ring[:, 1] <= y1)
    if not inside.any():
        return []
    segs, cur = [], []
    for k, ok in enumerate(inside):
        if ok:
            cur.append(ring[k])
        else:
            if len(cur) > 1:
                cur.append(ring[k])          # un vertice fuera para cerrar el corte
                segs.append(np.asarray(cur))
            cur = []
    if len(cur) > 1:
        segs.append(np.asarray(cur))
    return segs


def simplify(pts, tol):
    """Adelgazado por distancia acumulada. Suficiente para lineas de dibujo."""
    if len(pts) < 3:
        return pts
    keep = [0]
    for i in range(1, len(pts)-1):
        if np.hypot(*(pts[i] - pts[keep[-1]])) > tol:
            keep.append(i)
    keep.append(len(pts)-1)
    return pts[keep]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topo", required=True, help="municipalities.json de es-atlas")
    ap.add_argument("--data", required=True, help="directorio data/ del visor")
    ap.add_argument("--tol", type=float, default=0.0012,
                    help="tolerancia de adelgazado en grados (~130 m)")
    args = ap.parse_args()

    bb, man = domain_bbox(args.data)
    print(f"dominio: lon {bb[0]:.3f} a {bb[2]:.3f}, lat {bb[1]:.3f} a {bb[3]:.3f}")

    topo = json.load(open(args.topo))
    arcs = decode_arcs(topo)

    features, places = [], []
    for objname, layer in (("municipalities", "municipio"),
                           ("provinces", "provincia")):
        obj = topo["objects"].get(objname)
        if not obj:
            continue
        kept = 0
        for geom in obj["geometries"]:
            name = (geom.get("properties") or {}).get("name", "")
            lines = []
            allpts = []
            for r in rings(geom, arcs):
                if not len(r):
                    continue
                allpts.append(r)
                for seg in clip_ring(r, bb):
                    seg = simplify(seg, args.tol)
                    if len(seg) > 1:
                        lines.append([[round(float(x), 5), round(float(y), 5)]
                                      for x, y in seg])
            if not lines:
                continue
            kept += 1
            features.append({
                "type": "Feature",
                "properties": {"name": name, "layer": layer},
                "geometry": {"type": "MultiLineString", "coordinates": lines},
            })
            if layer == "municipio" and allpts:
                p = np.concatenate(allpts)
                # Centroide del contorno recortado a la bbox: el geometrico del
                # municipio completo puede caer fuera del dominio.
                m = (p[:, 0] >= bb[0]) & (p[:, 0] <= bb[2]) & \
                    (p[:, 1] >= bb[1]) & (p[:, 1] <= bb[3])
                if m.sum() > 2:
                    places.append({"name": name,
                                   "lat": round(float(p[m, 1].mean()), 5),
                                   "lon": round(float(p[m, 0].mean()), 5)})
        print(f"  {objname}: {kept} entidades tocan el dominio")

    gj = {"type": "FeatureCollection",
          "attribution": "Lineas limite municipales (c) Instituto Geografico "
                         "Nacional, CC BY 4.0. TopoJSON: es-atlas (MIT).",
          "features": features}
    out_gj = os.path.join(args.data, "admin.geojson")
    out_pl = os.path.join(args.data, "places.json")
    json.dump(gj, open(out_gj, "w"), separators=(",", ":"))
    json.dump(sorted(places, key=lambda p: p["name"]),
              open(out_pl, "w"), ensure_ascii=False, indent=1)

    nv = sum(len(l) for f in features for l in f["geometry"]["coordinates"])
    print(f"{out_gj}: {os.path.getsize(out_gj)/1000:.0f} KB, {nv} vertices")
    print(f"{out_pl}: {len(places)} municipios rotulados")


if __name__ == "__main__":
    main()
