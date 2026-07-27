#!/usr/bin/env python3
"""
make_demo_data.py — genera un dataset SINTETICO con la misma estructura que
pack_wrf_surface.py, para poder abrir y probar el visor sin tener el wrfout
delante. Los campos son inventados: relieve plausible y campos idealizados.

No usar para nada que no sea probar la interfaz.

    python3 make_demo_data.py -o data
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np

from pack_wrf_surface import FIELDS, encode_png16, nice_range

NX = NY = 501
DX = DY = 500.0
CEN_LAT, CEN_LON = 40.40698, -4.05835


def fbm(shape, seed, octaves=5, persistence=0.55):
    """Ruido fractal simple por suma de rejillas interpoladas."""
    rng = np.random.default_rng(seed)
    ny, nx = shape
    out = np.zeros(shape)
    amp = 1.0
    for o in range(octaves):
        n = 2 ** (o + 2)
        coarse = rng.standard_normal((n + 1, n + 1))
        yi = np.linspace(0, n, ny)
        xi = np.linspace(0, n, nx)
        y0 = np.clip(np.floor(yi).astype(int), 0, n - 1)
        x0 = np.clip(np.floor(xi).astype(int), 0, n - 1)
        fy = (yi - y0)[:, None]
        fx = (xi - x0)[None, :]
        fy = fy * fy * (3 - 2 * fy)
        fx = fx * fx * (3 - 2 * fx)
        c00 = coarse[np.ix_(y0, x0)]
        c10 = coarse[np.ix_(y0 + 1, x0)]
        c01 = coarse[np.ix_(y0, x0 + 1)]
        c11 = coarse[np.ix_(y0 + 1, x0 + 1)]
        out += amp * ((c00 * (1 - fx) + c01 * fx) * (1 - fy)
                      + (c10 * (1 - fx) + c11 * fx) * fy)
        amp *= persistence
    out -= out.min()
    return out / max(out.max(), 1e-9)


def build_terrain():
    """Relieve inventado: sierra orientada WSW-ENE, meseta al norte, valle al sur."""
    j, i = np.mgrid[0:NY, 0:NX]
    x = (i - (NX - 1) / 2) * DX / 1000.0     # km, +E
    y = (j - (NY - 1) / 2) * DY / 1000.0     # km, +N

    ang = np.radians(62.0)
    across = -x * np.sin(ang) + y * np.cos(ang)   # distancia perpendicular al eje
    along = x * np.cos(ang) + y * np.sin(ang)

    ridge = 1750.0 * np.exp(-(across / 17.0) ** 2)
    ridge *= 0.75 + 0.25 * np.cos(along / 26.0)
    plateau = 700.0 / (1.0 + np.exp(-(across - 40.0) / 14.0))
    valley = -220.0 * np.exp(-((across + 34.0) / 15.0) ** 2)

    rough = fbm((NY, NX), seed=11) * 320.0 * (0.35 + 0.65 * ridge / 1750.0)
    z = 420.0 + ridge + plateau + valley + rough
    return np.maximum(z, 0.0).astype(np.float32)


def build_fields(z):
    """Seis pasos horarios de campos idealizados coherentes con el relieve."""
    zn = (z - z.min()) / max(z.max() - z.min(), 1e-9)
    gy, gx = np.gradient(z, DY, DX)
    slope = np.hypot(gx, gy)
    exposure = np.clip((gx * 0.7 + gy * 0.7) / (slope.max() + 1e-9) * 3.0, -1, 1)

    times, out = [], {k: [] for k in
                      ("wspd10", "gust10", "t2", "rh2", "vpd2", "hdw_sfc",
                       "pblh", "u10", "v10")}
    t0 = datetime(2026, 7, 26, 18, tzinfo=timezone.utc)

    for h in range(6):
        tt = t0 + timedelta(hours=h)
        times.append(tt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        night = h / 5.0

        speed = (7.5 + 5.5 * zn - 2.5 * night) * (1.0 + 0.35 * exposure)
        speed += fbm((NY, NX), seed=100 + h) * 3.2 - 1.2
        speed = np.clip(speed, 0.2, None)

        drift = np.radians(232.0 + 14.0 * night + 10.0 * exposure)
        u = -speed * np.sin(drift)
        v = -speed * np.cos(drift)

        t2 = 35.0 - 6.5 * z / 1000.0 - 5.2 * night + fbm((NY, NX), seed=200 + h) * 1.4
        rh = np.clip(13.0 + 26.0 * night + (t2.max() - t2) * 1.6, 5.0, 96.0)

        es = 6.112 * np.exp(17.67 * t2 / (t2 + 243.5))
        vpd = np.maximum(es * (1.0 - rh / 100.0), 0.0)
        hdw = vpd / 10.0 * speed
        pblh = np.clip(2600.0 * (1.0 - night) ** 1.4 + 180.0 + 260.0 * zn, 120.0, None)

        out["wspd10"].append(speed)
        out["gust10"].append(speed * (1.55 + 0.25 * zn))
        out["t2"].append(t2)
        out["rh2"].append(rh)
        out["vpd2"].append(vpd)
        out["hdw_sfc"].append(hdw)
        out["pblh"].append(pblh)
        out["u10"].append(u)
        out["v10"].append(v)

    return times, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--outdir", default="data")
    args = ap.parse_args()

    z = build_terrain()
    times, fields = build_fields(z)

    lat = CEN_LAT + (np.arange(NY) - (NY - 1) / 2)[:, None] * (DY / 111320.0) * np.ones(NX)
    lon = CEN_LON + (np.arange(NX) - (NX - 1) / 2)[None, :] * (
        DX / (111320.0 * np.cos(np.radians(CEN_LAT)))) * np.ones((NY, 1))

    manifest_fields = []
    for key, samples in fields.items():
        label, units, cmap, _, _ = FIELDS[key]
        lo = min(float(s.min()) for s in samples)
        hi = max(float(s.max()) for s in samples)
        if key == "rh2":
            lo, hi = 0.0, 100.0
        elif cmap == "diverge":
            m = max(abs(lo), abs(hi))
            lo, hi = -m, m
        else:
            lo, hi = nice_range(lo, hi)
        files = []
        for n, arr in enumerate(samples):
            rel = f"{key}/t{n:03d}.png"
            encode_png16(os.path.join(args.outdir, rel), arr, lo, hi)
            files.append(rel)
        manifest_fields.append({"key": key, "label": label, "units": units,
                                "cmap": cmap, "vmin": lo, "vmax": hi, "files": files})
        print(f"  {key:9s} [{lo:g}, {hi:g}] {units}")

    statics = {}
    for name, arr in (("terrain", z), ("lat", lat), ("lon", lon)):
        lo, hi = float(arr.min()), float(arr.max())
        rel = f"static/{name}.png"
        encode_png16(os.path.join(args.outdir, rel), arr, lo, hi)
        statics[name] = {"file": rel, "vmin": lo, "vmax": hi}

    manifest = {
        "format": "wrf-surface-pack/1",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "DATOS SINTETICOS — prueba de interfaz",
        "note": "Campos inventados. No es una simulacion.",
        "grid": {"nx": NX, "ny": NY, "dx": DX, "dy": DY, "grid_id": 3,
                 "cen_lat": CEN_LAT, "cen_lon": CEN_LON,
                 "map_proj": "Lambert Conformal",
                 "init": "2026-07-26T12:00:00Z", "model": "SINTETICO"},
        "times": times,
        "static": statics,
        "fields": manifest_fields,
    }
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(args.outdir) for f in fs)
    print(f"{args.outdir}: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
