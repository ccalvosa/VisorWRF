#!/usr/bin/env python3
"""
make_meteo_cube.py — construye el cubo de meteogramas a partir de un data/ ya
empaquetado, sin necesitar los wrfout originales.

El cubo es una transposicion: los PNG del visor estan troceados por instante
(un fichero por campo y hora, con los 251.001 puntos del dominio dentro), y un
meteograma necesita lo contrario, todos los instantes de un punto. Aqui se
reordena a [tiempo, punto] y se submuestrea en el espacio.

    python3 make_meteo_cube.py --data data --stride 4

Sobrescribe la entrada "meteo" de data/manifest.json. Es idempotente: puedes
volver a lanzarlo con otro stride cuantas veces quieras.
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
from PIL import Image

from pack_wrf_surface import METEO_VARS, encode_png16


def decode(path: str, vmin: float, vmax: float) -> np.ndarray:
    px = np.asarray(Image.open(path).convert("RGB"))
    q = (px[..., 0].astype(np.uint32) << 8) | px[..., 1].astype(np.uint32)
    return (vmin + q*(vmax - vmin)/65535.0).astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="directorio data/ del visor")
    ap.add_argument("--stride", type=int, default=4,
                    help="submuestreo espacial (4 = un punto cada 4 de malla)")
    args = ap.parse_args()

    mpath = os.path.join(args.data, "manifest.json")
    man = json.load(open(mpath))
    bykey = {f["key"]: f for f in man["fields"]}

    missing = [k for k in METEO_VARS if k not in bykey]
    if missing:
        return SystemExit(
            f"Faltan campos en el paquete: {', '.join(missing)}.\n"
            "El cubo necesita t2, rh2, u10 y v10. Vuelve a empaquetar "
            "incluyendolos, o quita del cubo los que no tengas.")

    st = max(1, args.stride)
    nt = len(man["times"])
    mvars = []

    for key in METEO_VARS:
        f = bykey[key]
        rows = []
        for rel in f["files"]:
            a = decode(os.path.join(args.data, rel), f["vmin"], f["vmax"])
            rows.append(a[::st, ::st].ravel())
        cube = np.asarray(rows, dtype=np.float32)     # [tiempo, punto]
        mny, mnx = a[::st, ::st].shape

        lo, hi = float(cube.min()), float(cube.max())
        if hi <= lo:
            hi = lo + 1.0
        rel = f"meteo/{key}.png"
        encode_png16(os.path.join(args.data, rel), cube, lo, hi)
        mvars.append({"key": key, "label": f["label"], "units": f["units"],
                      "vmin": lo, "vmax": hi, "file": rel})
        kb = os.path.getsize(os.path.join(args.data, rel))/1000
        print(f"  {key:5s} {cube.shape[0]}x{cube.shape[1]}  {kb:6.0f} KB")

    man["meteo"] = {"stride": st, "nx": mnx, "ny": mny, "vars": mvars}
    json.dump(man, open(mpath, "w"), indent=1)

    tot = sum(os.path.getsize(os.path.join(args.data, v["file"])) for v in mvars)
    grid_km = man["grid"]["dx"]*st/1000
    print(f"cubo: {mnx}x{mny} puntos cada {grid_km:.1f} km, {nt} instantes, "
          f"{tot/1e6:.2f} MB")
    print(f"manifest actualizado: {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
