#!/usr/bin/env python3
"""
pack_wrf_surface.py — empaqueta campos de superficie de wrfout a un formato
estatico (PNG 16-bit + manifest.json) consumible por un visor web.

Deliberadamente agnostico del caso: no sabe nada de incendios, dominios ni
proyectos. Cualquier wrfout con las variables de superficie habituales vale.

Uso
---
    python3 pack_wrf_surface.py -o salida/data wrfout_d03_2026-07-26_*
    python3 pack_wrf_surface.py -o salida/data --stride 2 --vars wspd10,gust10,t2 wrfout_d03_*

Codificacion
------------
Cada campo se guarda como PNG RGB donde R = byte alto y G = byte bajo de un
uint16 que cubre [vmin, vmax] globales del campo (constantes en todos los
pasos, para que la escala de color no baile en la animacion). B queda a 0.
No se usa canal alfa: evita el premultiplicado del navegador al decodificar.

Requisitos: numpy, Pillow, netCDF4.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Falta Pillow: pip install Pillow")


# --------------------------------------------------------------------------
# Codificacion / escritura
# --------------------------------------------------------------------------

UINT16_MAX = 65535


def encode_png16(path: str, arr: np.ndarray, vmin: float, vmax: float) -> None:
    """Escribe arr (2D, orden WRF: fila 0 = sur) como PNG RGB de 16 bit.

    La fila 0 del PNG es la fila 0 del array (sur). El visor lo decodifica en
    el mismo orden y usa DataTexture, asi que no hay volteos implicitos.
    """
    arr = np.asarray(arr, dtype=np.float64)
    if vmax <= vmin:
        vmax = vmin + 1.0
    q = (arr - vmin) / (vmax - vmin)
    q = np.clip(q, 0.0, 1.0)
    q = np.rint(q * UINT16_MAX).astype(np.uint16)

    ny, nx = q.shape
    rgb = np.zeros((ny, nx, 3), dtype=np.uint8)
    rgb[..., 0] = (q >> 8).astype(np.uint8)
    rgb[..., 1] = (q & 0xFF).astype(np.uint8)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(rgb, mode="RGB").save(path, format="PNG", compress_level=6)


def nice_range(vmin: float, vmax: float, pad: float = 0.02) -> tuple[float, float]:
    """Redondea un rango a algo legible en la leyenda."""
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        return 0.0, 1.0
    span = vmax - vmin
    if span <= 0:
        return float(vmin) - 0.5, float(vmin) + 0.5
    vmin -= span * pad
    vmax += span * pad
    span = vmax - vmin
    step = 10.0 ** np.floor(np.log10(span / 4.0))
    for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
        if span / (step * mult) <= 6:
            step *= mult
            break
    return float(np.floor(vmin / step) * step), float(np.ceil(vmax / step) * step)


# --------------------------------------------------------------------------
# Derivaciones meteorologicas (sin wrf-python: solo numpy)
# --------------------------------------------------------------------------

def _es_hpa(t_c: np.ndarray) -> np.ndarray:
    """Presion de vapor de saturacion (hPa), Bolton 1980."""
    return 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))


def _e_hpa(q: np.ndarray, p_pa: np.ndarray) -> np.ndarray:
    """Presion de vapor (hPa) a partir de razon de mezcla y presion."""
    q = np.maximum(q, 1e-12)
    return (q * p_pa / (0.622 + 0.378 * q)) / 100.0


def d_wspd10(nc, t):
    u = nc.variables["U10"][t]
    v = nc.variables["V10"][t]
    return np.hypot(u, v)


def d_wdir10(nc, t):
    u = nc.variables["U10"][t]
    v = nc.variables["V10"][t]
    return np.degrees(np.arctan2(-u, -v)) % 360.0


def d_u10(nc, t):
    return nc.variables["U10"][t]


def d_v10(nc, t):
    return nc.variables["V10"][t]


def d_gust10(nc, t):
    return nc.variables["WSPD10MAX"][t]


def d_t2(nc, t):
    return nc.variables["T2"][t] - 273.15


def d_rh2(nc, t):
    t2c = nc.variables["T2"][t] - 273.15
    q2 = nc.variables["Q2"][t]
    psfc = nc.variables["PSFC"][t]
    rh = 100.0 * _e_hpa(q2, psfc) / _es_hpa(t2c)
    return np.clip(rh, 0.0, 100.0)


def d_vpd2(nc, t):
    """Deficit de presion de vapor en superficie (hPa)."""
    t2c = nc.variables["T2"][t] - 273.15
    q2 = nc.variables["Q2"][t]
    psfc = nc.variables["PSFC"][t]
    return np.maximum(_es_hpa(t2c) - _e_hpa(q2, psfc), 0.0)


def d_hdw_sfc(nc, t):
    """Proxy de superficie del Hot-Dry-Windy: VPD(hPa/10) * viento 10 m.

    NO es el HDW de Srock et al. (2018), que maximiza el producto en la capa
    de mezcla. Es una aproximacion barata en superficie. Si quieres el indice
    real hay que recorrer niveles hasta PBLH; se hace en otro script.
    """
    vpd_kpa = d_vpd2(nc, t) / 10.0
    return vpd_kpa * d_wspd10(nc, t)


def d_pblh(nc, t):
    return nc.variables["PBLH"][t]


def d_hfx(nc, t):
    return nc.variables["HFX"][t]


def d_prec1h(nc, t):
    out = np.zeros_like(nc.variables["PREC_ACC_NC"][t])
    for name in ("PREC_ACC_NC", "PREC_ACC_C"):
        if name in nc.variables:
            out = out + nc.variables[name][t]
    return out


def d_refl1(nc, t):
    return nc.variables["REFL_10CM"][t, 0]


def d_tsk(nc, t):
    return nc.variables["TSK"][t] - 273.15


# key -> (etiqueta, unidades, paleta, funcion, variables requeridas)
FIELDS = {
    "wspd10":  ("Viento 10 m",            "m/s",   "wind",    d_wspd10,  ["U10", "V10"]),
    "gust10":  ("Racha max. 10 m",        "m/s",   "wind",    d_gust10,  ["WSPD10MAX"]),
    "wdir10":  ("Direccion 10 m",         "grados", "cyclic",  d_wdir10,  ["U10", "V10"]),
    "u10":     ("Componente u 10 m",      "m/s",   "diverge", d_u10,     ["U10"]),
    "v10":     ("Componente v 10 m",      "m/s",   "diverge", d_v10,     ["V10"]),
    "t2":      ("Temperatura 2 m",        "C",     "thermal", d_t2,      ["T2"]),
    "rh2":     ("Humedad relativa 2 m",   "%",     "moist",   d_rh2,     ["T2", "Q2", "PSFC"]),
    "vpd2":    ("Deficit de vapor 2 m",   "hPa",   "dry",     d_vpd2,    ["T2", "Q2", "PSFC"]),
    "hdw_sfc": ("HDW superficie (proxy)", "-",     "dry",     d_hdw_sfc, ["T2", "Q2", "PSFC", "U10", "V10"]),
    "pblh":    ("Altura capa limite",     "m",     "depth",   d_pblh,    ["PBLH"]),
    "hfx":     ("Flujo calor sensible",   "W/m2",  "thermal", d_hfx,     ["HFX"]),
    "tsk":     ("Temperatura de piel",    "C",     "thermal", d_tsk,     ["TSK"]),
    "prec1h":  ("Precipitacion acumulada", "mm",   "moist",   d_prec1h,  ["PREC_ACC_NC"]),
    "refl1":   ("Reflectividad nivel 1",  "dBZ",   "radar",   d_refl1,   ["REFL_10CM"]),
}

DEFAULT_VARS = ["wspd10", "gust10", "t2", "rh2", "vpd2", "hdw_sfc", "pblh", "u10", "v10"]

# Cubo transpuesto para los meteogramas: [tiempo, punto] por variable. Se
# guardan u10/v10 en vez de velocidad y direccion porque la direccion es una
# magnitud circular y no se puede interpolar ni cuantizar sin cuidado; el
# visor deriva ambas de las componentes.
METEO_VARS = ["t2", "rh2", "u10", "v10"]

# Rangos fijos donde una escala estable importa mas que ajustarse a los datos.
FIXED_RANGE = {
    "rh2": (0.0, 100.0),
    "wdir10": (0.0, 360.0),
}


# --------------------------------------------------------------------------
# Lectura de wrfout
# --------------------------------------------------------------------------

def parse_times(nc) -> list[str]:
    raw = nc.variables["Times"][:]
    out = []
    for row in raw:
        s = b"".join([bytes(c) if isinstance(c, bytes) else str(c).encode() for c in row])
        s = s.decode("ascii", "ignore").strip()
        out.append(s.replace("_", "T") + "Z")
    return out


def collect(paths: list[str], keys: list[str], stride: int):
    """Recorre los ficheros y devuelve (times, muestras por campo, estaticos, geo)."""
    from netCDF4 import Dataset

    times: list[str] = []
    index: list[tuple[str, int]] = []   # (fichero, indice de tiempo)
    static = {}
    geo = {}
    available = None

    for path in paths:
        with Dataset(path) as nc:
            if available is None:
                available = set(nc.variables.keys())
                geo = {
                    "nx": int(nc.dimensions["west_east"].size),
                    "ny": int(nc.dimensions["south_north"].size),
                    "dx": float(nc.DX),
                    "dy": float(nc.DY),
                    "grid_id": int(getattr(nc, "GRID_ID", 0)),
                    "cen_lat": float(getattr(nc, "CEN_LAT", 0.0)),
                    "cen_lon": float(getattr(nc, "CEN_LON", 0.0)),
                    "map_proj": str(getattr(nc, "MAP_PROJ_CHAR", "")),
                    "init": str(getattr(nc, "SIMULATION_START_DATE", "")).replace("_", "T") + "Z",
                    "model": str(getattr(nc, "TITLE", "")).strip(),
                }
                sl = (slice(None, None, stride), slice(None, None, stride))
                static["terrain"] = np.asarray(nc.variables["HGT"][0])[sl]
                static["lat"] = np.asarray(nc.variables["XLAT"][0])[sl]
                static["lon"] = np.asarray(nc.variables["XLONG"][0])[sl]
                if "LANDMASK" in nc.variables:
                    static["landmask"] = np.asarray(nc.variables["LANDMASK"][0])[sl]

            for it, tstr in enumerate(parse_times(nc)):
                times.append(tstr)
                index.append((path, it))

    # Descarta campos cuyas variables fuente no estan en el fichero.
    usable, skipped = [], []
    for k in keys:
        if k not in FIELDS:
            skipped.append((k, "clave desconocida"))
            continue
        need = FIELDS[k][4]
        missing = [v for v in need if v not in available]
        if missing:
            skipped.append((k, "faltan " + ",".join(missing)))
        else:
            usable.append(k)

    order = np.argsort(times)
    times = [times[i] for i in order]
    index = [index[i] for i in order]
    return times, index, usable, skipped, static, geo, available


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wrfout", nargs="+", help="ficheros wrfout (glob expandido por la shell)")
    ap.add_argument("-o", "--outdir", required=True, help="directorio de salida (data/)")
    ap.add_argument("--vars", default=",".join(DEFAULT_VARS),
                    help="campos separados por coma, o 'all'")
    ap.add_argument("--stride", type=int, default=1,
                    help="submuestreo espacial (2 = mitad de resolucion)")
    ap.add_argument("--title", default="", help="titulo mostrado en el visor")
    ap.add_argument("--note", default="", help="linea de aviso mostrada en la cabecera")
    ap.add_argument("--meteo-stride", type=int, default=4,
                    help="submuestreo espacial del cubo de meteogramas "
                         "(4 = un punto cada 4 de malla; 0 lo desactiva)")
    args = ap.parse_args()

    keys = list(FIELDS) if args.vars.strip() == "all" else [
        k.strip() for k in args.vars.split(",") if k.strip()
    ]

    paths = sorted(args.wrfout)
    print(f"[1/4] Leyendo {len(paths)} fichero(s)...")
    times, index, keys, skipped, static, geo, avail_vars = collect(
        paths, keys, args.stride)
    for k, why in skipped:
        print(f"      omitido {k}: {why}")
    if not keys:
        return sys.exit("Ningun campo utilizable.")

    ny, nx = static["terrain"].shape
    geo["nx"], geo["ny"] = nx, ny
    geo["dx"] *= args.stride
    geo["dy"] *= args.stride
    print(f"      malla {nx}x{ny}, dx={geo['dx']:.0f} m, {len(times)} pasos, "
          f"{len(keys)} campos")

    from netCDF4 import Dataset
    sl = (slice(None, None, args.stride), slice(None, None, args.stride))

    # Paso 1: rango global por campo. Requiere una pasada previa, pero es lo
    # que mantiene la escala de color estable a lo largo de la animacion.
    print("[2/4] Calculando rangos globales...")
    stats = {k: [np.inf, -np.inf] for k in keys}
    cache: dict[str, dict[int, np.ndarray]] = {k: {} for k in keys}

    ms = max(0, args.meteo_stride)
    mkeys = [k for k in METEO_VARS if k in FIELDS and
             all(v in avail_vars for v in FIELDS[k][4])] if ms else []
    if ms and mkeys:
        msl = (slice(None, None, ms), slice(None, None, ms))
        mny, mnx = static["terrain"][msl].shape
        cube = {k: np.zeros((len(index), mny*mnx), dtype=np.float32) for k in mkeys}
        print(f"      cubo de meteogramas: {mnx}x{mny} puntos "
              f"({geo['dx']*ms/1000:.1f} km), {len(mkeys)} variables")
    else:
        cube, mnx, mny = {}, 0, 0

    for n, (path, it) in enumerate(index):
        with Dataset(path) as nc:
            for k in keys:
                arr = np.asarray(FIELDS[k][3](nc, it), dtype=np.float32)[sl]
                arr = np.where(np.isfinite(arr), arr, 0.0)
                cache[k][n] = arr
                stats[k][0] = min(stats[k][0], float(arr.min()))
                stats[k][1] = max(stats[k][1], float(arr.max()))
            for k in mkeys:
                # se reaprovecha el campo ya calculado si esta empaquetado
                a = cache[k][n] if k in keys else np.asarray(
                    FIELDS[k][3](nc, it), dtype=np.float32)[sl]
                cube[k][n] = np.nan_to_num(a[msl]).ravel()

    ranges = {}
    for k in keys:
        if k in FIXED_RANGE:
            ranges[k] = FIXED_RANGE[k]
        elif FIELDS[k][2] == "diverge":
            m = max(abs(stats[k][0]), abs(stats[k][1]))
            lo, hi = nice_range(-m, m)
            m = max(abs(lo), abs(hi))
            ranges[k] = (-m, m)
        else:
            ranges[k] = nice_range(*stats[k])

    print("[3/4] Escribiendo campos...")
    manifest_fields = []
    for k in keys:
        label, units, cmap, _, _ = FIELDS[k]
        vmin, vmax = ranges[k]
        files = []
        for n in range(len(index)):
            rel = f"{k}/t{n:03d}.png"
            encode_png16(os.path.join(args.outdir, rel), cache[k][n], vmin, vmax)
            files.append(rel)
        manifest_fields.append({
            "key": k, "label": label, "units": units, "cmap": cmap,
            "vmin": vmin, "vmax": vmax, "files": files,
        })
        print(f"      {k:9s} [{vmin:g}, {vmax:g}] {units}")
        cache[k].clear()

    print("[4/4] Escribiendo estaticos y manifest...")
    statics = {}
    for name, arr in static.items():
        lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
        if hi <= lo:
            hi = lo + 1.0
        rel = f"static/{name}.png"
        encode_png16(os.path.join(args.outdir, rel), arr, lo, hi)
        statics[name] = {"file": rel, "vmin": lo, "vmax": hi}

    meteo = None
    if cube:
        mvars = []
        for k in cube:
            label, units, _, _, _ = FIELDS[k]
            lo, hi = float(cube[k].min()), float(cube[k].max())
            if hi <= lo:
                hi = lo + 1.0
            rel = f"meteo/{k}.png"
            # una fila por instante, una columna por punto: el meteograma es
            # entonces una columna, y PNG comprime bien porque las filas
            # contiguas se parecen mucho
            encode_png16(os.path.join(args.outdir, rel), cube[k], lo, hi)
            mvars.append({"key": k, "label": label, "units": units,
                          "vmin": lo, "vmax": hi, "file": rel})
        meteo = {"stride": args.meteo_stride, "nx": mnx, "ny": mny,
                 "vars": mvars}
        tot = sum(os.path.getsize(os.path.join(args.outdir, v["file"]))
                  for v in mvars)
        print(f"      cubo escrito: {tot/1e6:.1f} MB")

    manifest = {
        "format": "wrf-surface-pack/1",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": args.title or f"WRF d0{geo.get('grid_id', '')} superficie",
        "note": args.note,
        "grid": geo,
        "times": times,
        "static": statics,
        "fields": manifest_fields,
        "meteo": meteo,
    }
    with open(os.path.join(args.outdir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)

    total = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, fs in os.walk(args.outdir) for f in fs
    )
    print(f"Listo: {args.outdir}  ({total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
