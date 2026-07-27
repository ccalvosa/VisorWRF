#!/bin/bash
# publish_visor.sh — se ejecuta por cron en un NODO DE LOGIN (con salida a
# internet). Detecta el stage listo con el ciclo más reciente y lo publica en
# GitHub Pages. El guard por ciclo evita que un slot lento (ciclo viejo) pise
# el sitio con datos rancios. Idempotente y reintentable: si el push falla, no
# marca el ciclo como publicado y lo reintenta en la siguiente pasada.
set -uo pipefail

REPO=/perm/ecme2143/work_VisorWRF
CTRL=/perm/ecme2143/visor_ctrl

# Deploy key sin passphrase para push no interactivo desde cron. Si tu clave por
# defecto (~/.ssh/id_ed25519) es la de VisorWRF, puedes dejar esta línea comentada.
# export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_ed25519_visorwrf -o BatchMode=yes"

cd "$REPO" || exit 1
mkdir -p "$CTRL"

shopt -s nullglob
readies=("$CTRL"/publish_ready.*)
[ ${#readies[@]} -eq 0 ] && exit 0

last=$(cat "$CTRL/published_cycle" 2>/dev/null || echo 0)

# Elegir el sentinel con ciclo máximo.
best_cycle=0; best_stage=""
for f in "${readies[@]}"; do
    read -r cyc tag stage < "$f" || continue
    [[ "$cyc" =~ ^[0-9]+$ ]] || continue
    if [ "$cyc" -gt "$best_cycle" ]; then best_cycle=$cyc; best_stage=$stage; fi
done

# Nada más nuevo que lo ya publicado → limpiar sentinels superados y salir.
if [ "$best_cycle" -le "$last" ]; then
    for f in "${readies[@]}"; do
        read -r cyc _ _ < "$f" || continue
        [[ "$cyc" =~ ^[0-9]+$ ]] && [ "$cyc" -le "$last" ] && rm -f "$f"
    done
    exit 0
fi

[ -d "$best_stage/data" ] || { echo "stage '$best_stage' sin data/, abort." >&2; exit 1; }

# Volcar el stage ganador al data/ del repo.
rsync -a --delete "$best_stage/data/" "$REPO/data/"
git add -A data

# Si no hay cambios reales, marcar publicado y salir (evita commits vacíos).
if git diff --cached --quiet; then
    echo "Ciclo $best_cycle sin cambios respecto a lo publicado."
    echo "$best_cycle" > "$CTRL/published_cycle"
    for f in "${readies[@]}"; do
        read -r cyc _ _ < "$f" || continue
        [[ "$cyc" =~ ^[0-9]+$ ]] && [ "$cyc" -le "$best_cycle" ] && rm -f "$f"
    done
    exit 0
fi

# Commit automático rodante: si el tip ya es del bot, se amenda (historial plano,
# repo pequeño bajo el límite de Pages); si no, commit nuevo sobre el inicial.
if git log -1 --format=%s 2>/dev/null | grep -q '^visor-auto:'; then
    git commit --amend -m "visor-auto: ciclo $best_cycle" --reset-author -q
else
    git commit -m "visor-auto: ciclo $best_cycle" -q
fi

if git push --force-with-lease origin main; then
    echo "$best_cycle" > "$CTRL/published_cycle"
    for f in "${readies[@]}"; do
        read -r cyc _ _ < "$f" || continue
        [[ "$cyc" =~ ^[0-9]+$ ]] && [ "$cyc" -le "$best_cycle" ] && rm -f "$f"
    done
    echo "Publicado ciclo $best_cycle → https://ccalvosa.github.io/VisorWRF/"
else
    echo "push falló; se reintenta en la próxima pasada." >&2
    exit 1
fi
