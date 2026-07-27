#!/usr/bin/env python3
"""
fetch_firms.py — focos termicos de NASA FIRMS recortados al dominio del modelo.

Escribe data/fires.json, que el visor lee para dibujar los focos coloreados por
antiguedad. La descarga se hace aqui y no en el navegador por tres razones: la
clave quedaria publica en GitHub Pages, FIRMS no sirve CORS, y asi el fichero
publicado es reproducible y fechado.

    export FIRMS_MAP_KEY=...
    python3 fetch_firms.py --data data --hours 48

La clave nunca se escribe en el fichero de salida ni en el repositorio.

Deriva del codigo de Carlos para el visor HARMONIE: se conserva la
normalizacion de confianza entre VIIRS y MODIS, el descarte de nominales
aisladas y la agregacion en celdas de 1 km. Se ha quitado la dependencia del
recorte a tierra espanola, que aqui no aporta porque el dominio ya es interior.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

FIRMS_AREA_API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# Los tres VIIRS comparten plano orbital, asi que solo dan dos ventanas diarias
# sobre la peninsula. Terra y Aqua anaden pasadas a media manana y a ultima hora
# de la tarde a cambio de un pixel de 1 km.
SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT")
RESOLUTION_KM = {"VIIRS": 0.375, "MODIS": 1.0}
DEFAULT_RESOLUTION_KM = 1.0
# MODIS informa la confianza en porcentaje: 0-29 baja, 30-79 nominal, 80-100
# alta, equivalente a las letras l/n/h de VIIRS.
MODIS_NOMINAL, MODIS_HIGH = 30, 80
SUPPORT_RADIUS_KM = 1.2
SUPPORT_TIME = timedelta(hours=6)
CELL_KM = 1.0


# --------------------------------------------------------------------------
# Analisis del CSV
# --------------------------------------------------------------------------

def _acq(row):
    d = (row.get("acq_date") or "").strip()
    t = (row.get("acq_time") or "").strip().zfill(4)
    try:
        return datetime.strptime(d + t, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(row, key):
    try:
        return float(row.get(key, ""))
    except (TypeError, ValueError):
        return None


def _confidence(raw):
    v = (raw or "").strip().lower()
    if v in {"h", "high"}:
        return "high"
    if v in {"n", "nominal"}:
        return "nominal"
    try:
        pct = float(v)
    except ValueError:
        return None
    if pct >= MODIS_HIGH:
        return "high"
    if pct >= MODIS_NOMINAL:
        return "nominal"
    return None


def _instrument(row, source):
    name = (row.get("instrument") or "").strip().upper()
    if name.startswith("MODIS") or (not name and source.startswith("MODIS")):
        return "MODIS"
    return name or "VIIRS"


def parse_csv(payload, source, now, hours, bbox):
    """Filtra por confianza, ventana temporal y recuadro del dominio."""
    west, south, east, north = bbox
    cutoff = now - timedelta(hours=hours)
    reader = csv.DictReader(io.StringIO(payload.lstrip("\ufeff")))
    if not reader.fieldnames or not {"latitude", "longitude"}.issubset(reader.fieldnames):
        raise RuntimeError(f"Respuesta FIRMS no valida para {source}")

    out = []
    for row in reader:
        conf = _confidence(row.get("confidence"))
        if conf is None:
            continue
        at = _acq(row)
        if at is None or at < cutoff or at > now + timedelta(minutes=5):
            continue
        lat, lon = _num(row, "latitude"), _num(row, "longitude")
        if lat is None or lon is None:
            continue
        if not (west <= lon <= east and south <= lat <= north):
            continue
        inst = _instrument(row, source)
        bright = _num(row, "bright_ti4")
        if bright is None:
            bright = _num(row, "brightness")
        out.append({
            "lat": lat, "lon": lon,
            "at": at,
            "age_h": max(0.0, (now - at).total_seconds()/3600.0),
            "satellite": (row.get("satellite") or source.removesuffix("_NRT")).strip(),
            "instrument": inst,
            "resolution_km": RESOLUTION_KM.get(inst, DEFAULT_RESOLUTION_KM),
            "confidence": conf,
            "frp": _num(row, "frp"),
            "brightness_k": bright,
            "daynight": (row.get("daynight") or "").strip(),
            "source": source,
        })
    return out


# --------------------------------------------------------------------------
# Filtrado y agregacion
# --------------------------------------------------------------------------

def drop_isolated_nominal(recs):
    """Retira nominales sin respaldo; las de confianza alta se conservan siempre.

    Se considera respaldada una deteccion si hay otra a 1,2 km o menos y con
    menos de seis horas de diferencia. El indice de celdas evita comparar cada
    punto contra la coleccion entera.
    """
    if not recs:
        return []
    cosref = math.cos(math.radians(40.0))
    idx, buckets = [], {}
    for n, r in enumerate(recs):
        x = r["lon"]*cosref*111.32
        y = r["lat"]*110.57
        cell = (math.floor(x/SUPPORT_RADIUS_KM), math.floor(y/SUPPORT_RADIUS_KM))
        idx.append((x, y, r["at"], cell))
        buckets.setdefault(cell, []).append(n)

    kept, r2 = [], SUPPORT_RADIUS_KM**2
    for n, r in enumerate(recs):
        x, y, at, (cx, cy) = idx[n]
        support = 1
        for i in range(cx-1, cx+2):
            for j in range(cy-1, cy+2):
                for m in buckets.get((i, j), ()):
                    if m == n:
                        continue
                    ox, oy, oat, _ = idx[m]
                    if abs(oat - at) > SUPPORT_TIME:
                        continue
                    if (ox-x)**2 + (oy-y)**2 <= r2:
                        support += 1
        r["support"] = support
        if support > 1 or r["confidence"] == "high":
            kept.append(r)
    return kept


def aggregate(recs):
    """Consolida observaciones proximas en celdas de 1 km."""
    if not recs:
        return []
    latref = sum(r["lat"] for r in recs)/len(recs)
    cosref = math.cos(math.radians(latref))
    cells = {}
    for r in recs:
        cell = (round(r["lon"]*cosref*111.32/CELL_KM),
                round(r["lat"]*110.57/CELL_KM))
        e = cells.get(cell)
        if e is None:
            cells[cell] = {"latest": r, "first": r["at"], "n": 1,
                           "sats": {r["satellite"]}, "inst": {r["instrument"]},
                           "res": r["resolution_km"],
                           "frp_max": r["frp"], "frp_sum": r["frp"] or 0.0}
            continue
        e["n"] += 1
        e["sats"].add(r["satellite"])
        e["inst"].add(r["instrument"])
        e["res"] = min(e["res"], r["resolution_km"])
        if r["frp"] is not None:
            e["frp_sum"] += r["frp"]
            if e["frp_max"] is None or r["frp"] > e["frp_max"]:
                e["frp_max"] = r["frp"]
        if r["at"] < e["first"]:
            e["first"] = r["at"]
        if r["at"] > e["latest"]["at"]:
            e["latest"] = r
    out = []
    for e in cells.values():
        r = e["latest"]
        out.append({
            "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
            "at": r["at"].isoformat().replace("+00:00", "Z"),
            "first_at": e["first"].isoformat().replace("+00:00", "Z"),
            "age_h": round(r["age_h"], 2),
            "n": e["n"],
            "sats": sorted(e["sats"]),
            "inst": sorted(e["inst"]),
            "res_km": e["res"],
            "conf": r["confidence"],
            "frp": round(e["frp_max"], 2) if e["frp_max"] is not None else None,
            "daynight": r["daynight"],
        })
    return sorted(out, key=lambda x: x["at"], reverse=True)


# --------------------------------------------------------------------------

def domain_bbox(datadir, pad=0.05):
    man = json.load(open(os.path.join(datadir, "manifest.json")))
    def dec(name):
        st = man["static"][name]
        px = np.asarray(Image.open(os.path.join(datadir, st["file"])).convert("RGB"))
        q = (px[..., 0].astype(np.uint32) << 8) | px[..., 1].astype(np.uint32)
        return st["vmin"] + q*(st["vmax"] - st["vmin"])/65535.0
    lat, lon = dec("lat"), dec("lon")
    return (float(lon.min())-pad, float(lat.min())-pad,
            float(lon.max())+pad, float(lat.max())+pad)


def download(source, key, bbox, days):
    west, south, east, north = bbox
    url = (f"{FIRMS_AREA_API}/{key}/{source}/"
           f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}/{days}")
    req = urllib.request.Request(url, headers={"User-Agent": "wrf-visor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--hours", type=int, default=48,
                    help="ventana temporal a conservar")
    ap.add_argument("--key", default=os.environ.get("FIRMS_MAP_KEY", ""),
                    help="clave FIRMS; mejor por la variable FIRMS_MAP_KEY")
    args = ap.parse_args()

    if not args.key:
        return sys.exit("Falta la clave. export FIRMS_MAP_KEY=... "
                        "No la pongas en la linea de comandos si compartes el "
                        "historial de la shell.")

    bbox = domain_bbox(args.data)
    print(f"dominio: lon {bbox[0]:.3f} a {bbox[2]:.3f}, "
          f"lat {bbox[1]:.3f} a {bbox[3]:.3f}")
    now = datetime.now(timezone.utc)
    days = max(1, min(10, args.hours//24 + 1))

    recs, ok, errs = [], [], []
    for src in SOURCES:
        try:
            txt = download(src, args.key, bbox, days)
            got = parse_csv(txt, src, now, args.hours, bbox)
            recs.extend(got)
            ok.append(src)
            print(f"  {src:18s} {len(got):5d} detecciones")
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
            errs.append(f"{src}: {type(e).__name__}")
            print(f"  {src:18s} error: {e}")

    if not ok:
        return sys.exit("Ninguna fuente de FIRMS respondio.")

    # deduplica la misma deteccion vista por varias fuentes
    seen, uniq = set(), []
    for r in recs:
        k = (r["source"], r["at"], round(r["lon"], 5), round(r["lat"], 5))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    supported = drop_isolated_nominal(uniq)
    points = aggregate(supported)

    out = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window_hours": args.hours,
        "sources": ok,
        "errors": errs,
        "observations": len(supported),
        "discarded_isolated": len(uniq) - len(supported),
        "cell_km": CELL_KM,
        "attribution": "Focos termicos: NASA FIRMS (VIIRS/MODIS), NRT. "
                       "Detecciones de confianza nominal y alta.",
        "points": points,
    }
    path = os.path.join(args.data, "fires.json")
    json.dump(out, open(path, "w"), separators=(",", ":"))
    print(f"{path}: {len(points)} celdas de {len(supported)} observaciones, "
          f"{os.path.getsize(path)/1000:.0f} KB")
    if points:
        print(f"  mas reciente: {points[0]['at']} "
              f"({points[0]['age_h']:.1f} h) FRP {points[0]['frp']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
