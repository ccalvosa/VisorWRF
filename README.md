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

## Referencia geográfica

El visor no lleva cartografía embarcada. Se georreferencia de tres formas:

1. **Retícula lat/lon dibujada sobre el terreno**, a partir de los propios
   `XLAT`/`XLONG` del modelo. Es exacta y no depende de ningún dato externo.
2. **Sonda con UTM.** Además de lat/lon y cota, el cursor da huso, banda y
   coordenadas UTM (30T para el centro peninsular). Implementación por la
   serie clásica de Snyder; validada por ida y vuelta con error máximo de
   0,14 mm en el dominio. Ojo: es WGS84, y la cartografía del IGN va en
   ETRS89, que difiere en el orden del metro. No lo uses para nada
   topográfico.
3. **Topónimos opcionales.** Si existe `data/places.json` se rotulan:

```json
[
 {"name": "Villa del Prado", "lat": 40.2778, "lon": -4.3111},
 {"name": "Puesto de mando", "lat": 40.35,   "lon": -4.40}
]
```

Las coordenadas del ejemplo son ilustrativas: cámbialas por las reales
del Nomenclátor Geográfico del IGN o de donde las saques. Doble clic sobre
el terreno marca un punto y pide nombre; la tecla `P` copia todos los puntos
al portapapeles ya en el formato de `places.json`.

La flecha de norte se gira según la **convergencia real de la rejilla**,
medida sobre los XLAT/XLONG del centro del dominio: en una Lambert el norte
de la malla solo coincide con el geográfico en el meridiano central. La barra
de escala solo aparece en 2D, porque en perspectiva una escala única miente.

## three.js va dentro del repositorio

`vendor/three.module.js` y `vendor/controls/OrbitControls.js` son la revisión
169 tal cual sale del paquete npm. Están aquí a propósito, no por comodidad:

- Un fallo de red con el CDN dejaba la página **completamente en blanco y
  muda**, porque el manejador de errores vive dentro del módulo que no llega
  a cargarse. Sin dependencia externa ese modo de fallo desaparece.
- En un puesto de mando o en una red aislada tiene que funcionar sin salida
  a internet.

Son 1,3 MB. Para volver al CDN, cambia las dos rutas del `importmap`; están
comentadas en el propio `index.html`.

## Diagnóstico cuando algo no sale

El `index.html` lleva un vigilante de arranque en un script clásico, que se
ejecuta aunque el módulo falle. Si algo va mal verás un recuadro arriba a la
izquierda con las etapas alcanzadas. Si no reporta ninguna etapa, el módulo
no ha arrancado: casi siempre es `file://` en vez de `http://`, o falta la
carpeta `vendor/`.

Para depurar sin navegador, `harness.mjs` ejecuta el módulo en Node con un DOM
y un three.js simulados, y recorre todos los controles:

```bash
node harness.mjs .
```

No dibuja nada, así que no detecta errores de compilación de shaders, pero sí
cualquier fallo de lógica, referencia o flujo de arranque.

## Focos térmicos de NASA FIRMS

`fetch_firms.py` descarga las detecciones de VIIRS y MODIS, las recorta al
dominio y escribe `data/fires.json`. La descarga **no** la hace el navegador:

- La clave de FIRMS quedaría pública en GitHub Pages y las cuotas son por
  usuario.
- FIRMS no sirve CORS, así que un `fetch` desde el navegador fallaría.
- Así el fichero publicado queda fechado y es reproducible.

```bash
export FIRMS_MAP_KEY=...            # nunca en el repositorio
python3 fetch_firms.py --data data --hours 48
```

Filtros heredados del pipeline de HARMONIE: se descartan las detecciones de
baja confianza, se normaliza la confianza de MODIS (porcentaje) contra las
letras de VIIRS, se retiran las nominales sin otra observación a 1,2 km y 6 h,
y se consolidan las observaciones próximas en celdas de 1 km quedándose el FRP
máximo y la mejor resolución de las que respaldan la celda.

En el visor, el tamaño del punto va con el FRP y el color con la antigüedad,
de amarillo a rojo oscuro, con la opacidad bajando. **La antigüedad se
recalcula en el navegador contra la hora actual**, no se usa la del fichero:
si el paquete lleva horas publicado esa cifra ya no vale.

Añade al `.gitignore` cualquier fichero donde guardes la clave. Y si la clave
se ha expuesto en algún momento, pide otra en el portal de FIRMS: son
gratuitas y revocables.
