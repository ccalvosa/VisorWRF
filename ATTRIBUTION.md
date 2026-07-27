# Procedencia de los datos y de las dependencias

## Límites administrativos y topónimos

`data/admin.geojson` y `data/places.json` derivan de las **Líneas Límite
Municipales** del Instituto Geográfico Nacional.

> Líneas límite municipales © Instituto Geográfico Nacional, CC BY 4.0
> https://creativecommons.org/licenses/by/4.0/

Intermediario: [es-atlas](https://github.com/martgnz/es-atlas) v0.6.0 (MIT),
que convierte los shapefiles del IGN a TopoJSON. El recorte al dominio y el
adelgazado de vértices los hace `clip_admin.py` de este repositorio.

La licencia CC BY **obliga a mantener este crédito visible** en cualquier
publicación o captura que use estas capas. Condiciones de uso del IGN:
http://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf

## Dependencias

- [three.js](https://threejs.org) r169 (MIT), incluido en `vendor/`.

## Datos meteorológicos

Salidas del modelo WRF-ARW generadas por el autor. Si el `data/` publicado
es el sintético de `make_demo_data.py`, los campos son **inventados** y el
visor lo indica en la cabecera.
