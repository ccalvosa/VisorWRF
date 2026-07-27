# Visor de superficie WRF

Sitio estático (sin servidor) para explorar campos de superficie de un wrfout
sobre la orografía del propio modelo, en 2D y 3D. Tres piezas independientes:

| Fichero | Qué hace |
|---|---|
| `pack_wrf_surface.py` | wrfout → `data/` (PNG 16-bit + `manifest.json`). Corre en Atos. |
| `index.html` | El visor. No sabe nada del caso, solo lee el manifest. |
| `make_demo_data.py` | Genera un `data/` **sintético** para probar la interfaz. |

## Probarlo ahora

```bash
python3 -m http.server 8000     # file:// no vale, fetch() lo bloquea
```

Y abrir <http://localhost:8000>. El `data/` incluido es sintético: relieve y
campos inventados, solo para ver si la interfaz sirve.

## Con datos reales

En Atos, donde estén los wrfout:

```bash
python3 pack_wrf_surface.py -o data \
    --title "d03 500 m — Sierra Oeste" \
    --note "Simulación experimental, no producto operativo" \
    /ruta/wrfout_d03_2026-07-2*
```

Campos por defecto: `wspd10 gust10 t2 rh2 vpd2 hdw_sfc pblh u10 v10`.
`--vars all` saca todos los del registro; `--vars` con lista para elegir.
`u10`/`v10` no aparecen en el selector: alimentan las flechas de viento.
Los campos cuyas variables fuente no estén en el fichero se omiten con aviso.

`--stride 2` reduce a la mitad la resolución si el peso se va de las manos.
Referencia: 501×501, 9 campos, 6 pasos ≈ 17 MB. A 37 pasos ≈ 105 MB. El
límite de GitHub Pages es 1 GB de sitio publicado y 100 GB/mes de tráfico.

## Añadir un campo nuevo

Una función y una línea en el registro `FIELDS` de `pack_wrf_surface.py`:

```python
def d_mi_campo(nc, t):
    return nc.variables["ALGO"][t] * 2.0

FIELDS["mi_campo"] = ("Etiqueta", "unidades", "thermal", d_mi_campo, ["ALGO"])
```

El visor lo recoge solo. Paletas disponibles: `wind thermal moist dry depth
diverge cyclic radar`.

## Controles

Rueda para zoom, arrastrar para orbitar (3D) o desplazar (2D). Espacio
reproduce, flechas cambian de paso, `D` alterna 2D/3D. El cursor sobre el
terreno da valor, lat/lon y cota.

## Decisiones que conviene conocer

- **Escala de color fija** en todos los pasos de tiempo (rango global del
  campo), para que la animación no engañe. Se calcula en una primera pasada.
- **Fila 0 del PNG = fila 0 del modelo (sur).** Se decodifica en el mismo
  orden y se usa `DataTexture`, así que no hay volteos implícitos en ningún
  punto de la cadena.
- **El sombreado del relieve se calcula con una exageración fija** (×3),
  independiente del deslizador. Por eso en 2D, con el terreno plano, se sigue
  viendo el relieve por debajo del campo.
- **La precisión de la sonda no depende de la textura.** La textura es de 8
  bit (solo para pintar); la lectura usa el `Float32Array` decodificado, con
  el paso real del uint16 (~1/65535 del rango).
- `hdw_sfc` **no es** el HDW de Srock et al. (2018): ese maximiza el producto
  VPD × viento en la capa de mezcla, esto es solo superficie. Sirve como
  proxy, no como el índice.
