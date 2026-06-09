#!/usr/bin/env bash
# =============================================================================
#  deploy.sh — Script de despliegue de SharePoint Connector
#
#  Dispatcher modular con comandos posicionales. Todas las rutas son absolutas.
#
#  Uso:
#    ./deploy.sh <comando> [opciones]
#
#  Ejecuta './deploy.sh help' para ver todos los comandos disponibles.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Rutas absolutas
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"
IMAGE_NAME="sharepoint-connector:latest"
APP_SERVICE="sharepoint-connector"
CONTAINER_NAME="sp-connector"
HEALTH_TIMEOUT=60

# ---------------------------------------------------------------------------
# Visual: colores y helpers semánticos
# Solo se activan colores si el terminal los soporta.
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    _RED='\033[0;31m'; _GREEN='\033[0;32m'; _YELLOW='\033[1;33m'
    _CYAN='\033[0;36m'; _BOLD='\033[1m'; _RESET='\033[0m'
else
    _RED=''; _GREEN=''; _YELLOW=''; _CYAN=''; _BOLD=''; _RESET=''
fi

log()  { echo -e "${_CYAN}[INFO]${_RESET}  $*"; }
ok()   { echo -e "${_GREEN}[OK]${_RESET}    $*"; }
warn() { echo -e "${_YELLOW}[WARN]${_RESET}  $*"; }
err()  { echo -e "${_RED}[ERROR]${_RESET} $*" >&2; }
bold() { echo -e "${_BOLD}$*${_RESET}"; }

header() {
    echo ""
    echo -e "${_BOLD}══════════════════════════════════════════════════${_RESET}"
    echo -e "${_BOLD}  $*${_RESET}"
    echo -e "${_BOLD}══════════════════════════════════════════════════${_RESET}"
}

# ---------------------------------------------------------------------------
# get_env_var KEY [DEFAULT]
# Extrae el valor de una variable del .env sin hacer 'source' global.
# ---------------------------------------------------------------------------
get_env_var() {
    local key="$1"
    local default="${2:-}"
    local val
    val=$(grep -E "^${key}[[:space:]]*=" "$ENV_FILE" 2>/dev/null \
        | head -1 \
        | sed 's/^[^=]*=[[:space:]]*//' \
        | tr -d "\"'") || true
    echo "${val:-$default}"
}

# ---------------------------------------------------------------------------
# confirm MSG
# Pide confirmación interactiva: el usuario debe escribir exactamente "si".
# ---------------------------------------------------------------------------
confirm() {
    local msg="${1:-¿Estás seguro?}"
    warn "$msg"
    warn "Esta acción es DESTRUCTIVA e IRREVERSIBLE."
    echo ""
    printf "  Escribe %bsi%b para confirmar: " "${_BOLD}" "${_RESET}"
    local answer
    read -r answer
    if [[ "$answer" != "si" ]]; then
        log "Operación cancelada."
        exit 0
    fi
}

# ---------------------------------------------------------------------------
# check_requirements
# Verifica docker, docker compose y existencia del .env.
# Establece COMPOSE_CMD.
# ---------------------------------------------------------------------------
check_requirements() {
    COMPOSE_CMD=""

    if ! command -v docker &>/dev/null; then
        err "Docker no está instalado o no está en el PATH."
        err "Instalar: https://docs.docker.com/engine/install/"
        exit 1
    fi

    local docker_version
    docker_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "desconocida")
    ok "Docker encontrado (versión: $docker_version)"

    if docker compose version &>/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
        ok "Docker Compose plugin v2 encontrado"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
        warn "Usando docker-compose v1 (se recomienda el plugin 'docker compose' v2)"
    else
        err "Docker Compose no está disponible."
        err "Instalar: https://docs.docker.com/compose/install/"
        exit 1
    fi

    if ! docker info &>/dev/null; then
        err "El daemon de Docker no está corriendo. Inicia Docker primero."
        exit 1
    fi
    ok "Docker daemon activo"

    if [[ ! -f "$ENV_FILE" ]]; then
        err "Archivo .env no encontrado: $ENV_FILE"
        if [[ -f "$ENV_EXAMPLE" ]]; then
            log "Crea el archivo ejecutando:"
            log "  cp $ENV_EXAMPLE $ENV_FILE"
            log "Luego edita .env con los valores reales."
        fi
        exit 1
    fi
    ok "Archivo .env encontrado"
}

# ---------------------------------------------------------------------------
# do_git_pull
# Hace pull usando credenciales de GIT_USER / GIT_TOKEN sin embeber en URL.
# Crea un helper temporal que no deja rastro de contraseñas en el proceso.
# ---------------------------------------------------------------------------
do_git_pull() {
    header "Git Pull"

    local git_user="${GIT_USER:-$(get_env_var GIT_USER)}"
    local git_token="${GIT_TOKEN:-$(get_env_var GIT_TOKEN)}"

    if [[ -z "$git_user" || -z "$git_token" ]]; then
        err "GIT_USER y GIT_TOKEN son requeridos para --pull."
        err "Defínelos en .env o como variables de entorno antes de ejecutar."
        exit 1
    fi

    local helper
    helper="$(mktemp)"
    chmod 700 "$helper"
    cat > "$helper" <<HELPER_EOF
#!/usr/bin/env bash
echo "username=${git_user}"
echo "password=${git_token}"
HELPER_EOF

    trap "rm -f '$helper'" EXIT

    log "Actualizando repositorio desde remoto..."
    git -C "$PROJECT_ROOT" -c "credential.helper=$helper" pull

    rm -f "$helper"
    trap - EXIT
    ok "Repositorio actualizado"
}

# ---------------------------------------------------------------------------
# build_image
# Construye la imagen Docker desde el Dockerfile del proyecto.
# ---------------------------------------------------------------------------
build_image() {
    header "Construyendo imagen Docker"
    log "Contexto: $PROJECT_ROOT"
    log "Imagen:   $IMAGE_NAME"
    echo ""

    cd "$PROJECT_ROOT"
    docker build \
        --progress=plain \
        --file "$SCRIPT_DIR/Dockerfile" \
        --tag "$IMAGE_NAME" \
        --label "build.date=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        .

    ok "Imagen construida: $IMAGE_NAME"
}

# ---------------------------------------------------------------------------
# wait_for_health
# Espera hasta que el endpoint /health responda correctamente.
# ---------------------------------------------------------------------------
wait_for_health() {
    header "Verificando salud del servicio"

    local sp_port
    sp_port="$(get_env_var SP_PORT 8003)"
    local url="http://localhost:${sp_port}/health"
    local elapsed=0
    local interval=3

    log "Esperando respuesta en: $url (timeout: ${HEALTH_TIMEOUT}s)"
    echo ""

    while [[ "$elapsed" -lt "$HEALTH_TIMEOUT" ]]; do
        if curl -sf "$url" -o /dev/null 2>/dev/null; then
            local response
            response=$(curl -sf "$url" 2>/dev/null || echo '{}')
            ok "Servicio disponible — $response"
            return 0
        fi
        printf "  Esperando... (%ds/%ds)\r" "$elapsed" "$HEALTH_TIMEOUT"
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    echo ""
    err "El servicio no respondió en ${HEALTH_TIMEOUT}s."
    err "Revisa los logs con: ./deploy.sh logs"
    exit 1
}

# ---------------------------------------------------------------------------
# show_summary
# Muestra el resumen de acceso tras un despliegue exitoso.
# ---------------------------------------------------------------------------
show_summary() {
    local sp_port
    sp_port="$(get_env_var SP_PORT 8003)"
    local host_ip
    host_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "TU_IP")

    echo ""
    echo -e "${_GREEN}${_BOLD}╔══════════════════════════════════════════════════════╗${_RESET}"
    echo -e "${_GREEN}${_BOLD}║  SharePoint Connector — DESPLEGADO                  ║${_RESET}"
    echo -e "${_GREEN}${_BOLD}╚══════════════════════════════════════════════════════╝${_RESET}"
    echo ""
    echo -e "  ${_BOLD}Local:${_RESET}         http://localhost:${sp_port}"
    echo -e "  ${_BOLD}Red local:${_RESET}     http://${host_ip}:${sp_port}"
    echo -e "  ${_BOLD}API Docs:${_RESET}      http://localhost:${sp_port}/docs"
    echo -e "  ${_BOLD}Health:${_RESET}        http://localhost:${sp_port}/health"
    echo ""
    echo -e "  ${_BOLD}Gestión rápida:${_RESET}"
    echo -e "    ${_CYAN}./deploy.sh logs -f${_RESET}      — Logs en tiempo real"
    echo -e "    ${_CYAN}./deploy.sh status${_RESET}        — Estado + health check"
    echo -e "    ${_CYAN}./deploy.sh restart${_RESET}       — Reinicio ligero"
    echo -e "    ${_CYAN}./deploy.sh stop${_RESET}          — Detener el stack"
    echo -e "    ${_CYAN}./deploy.sh update${_RESET}        — Rebuild + recrear"
    echo ""
}

# ===========================================================================
# COMANDOS DEL DISPATCHER
# ===========================================================================

# ---------------------------------------------------------------------------
# cmd_start [--pull] [--no-build]
# Despliegue completo. --no-build usa imagen existente. --pull hace git pull.
# ---------------------------------------------------------------------------
cmd_start() {
    local do_pull=false
    local do_build=true

    for arg in "$@"; do
        case "$arg" in
            --pull)     do_pull=true ;;
            --no-build) do_build=false ;;
        esac
    done

    header "Iniciando SharePoint Connector"

    [[ "$do_pull" == true ]] && do_git_pull

    if [[ "$do_build" == true ]]; then
        build_image
    else
        if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
            err "Imagen '$IMAGE_NAME' no encontrada. Ejecuta sin --no-build para construirla."
            exit 1
        fi
        log "Usando imagen existente: $IMAGE_NAME (--no-build)"
    fi

    cd "$SCRIPT_DIR"
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$" 2>/dev/null; then
        log "Deteniendo contenedor anterior ($CONTAINER_NAME)..."
        $COMPOSE_CMD -f "$COMPOSE_FILE" down --remove-orphans
    fi

    log "Arrancando el stack..."
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d

    wait_for_health
    show_summary
}

# ---------------------------------------------------------------------------
# cmd_stop
# Detiene el stack.
# ---------------------------------------------------------------------------
cmd_stop() {
    header "Deteniendo stack"
    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" down
    ok "Stack detenido."
}

# ---------------------------------------------------------------------------
# cmd_restart
# Reinicio ligero sin destruir el contenedor.
# ---------------------------------------------------------------------------
cmd_restart() {
    header "Reinicio ligero"
    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" restart
    ok "Stack reiniciado"
}

# ---------------------------------------------------------------------------
# cmd_update [--pull]
# Rebuild imagen + recrear contenedor.
# ---------------------------------------------------------------------------
cmd_update() {
    local do_pull=false
    for arg in "$@"; do
        [[ "$arg" == "--pull" ]] && do_pull=true
    done

    header "Actualizando SharePoint Connector"
    warn "Se reconstruirá la imagen y se recreará el contenedor."

    [[ "$do_pull" == true ]] && do_git_pull

    build_image

    header "Recreando contenedor con la nueva imagen"
    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d --force-recreate

    wait_for_health
    show_summary
    ok "Actualización completada"
}

# ---------------------------------------------------------------------------
# cmd_logs [-f] [-n N]
# Muestra logs del servicio principal.
# ---------------------------------------------------------------------------
cmd_logs() {
    local follow_flag=""
    local tail_flag="--tail=100"

    local i=1
    while [[ $i -le $# ]]; do
        arg="${!i}"
        case "$arg" in
            -f|--follow) follow_flag="--follow" ;;
            -n)
                i=$((i + 1))
                tail_flag="--tail=${!i}" ;;
            --tail=*) tail_flag="$arg" ;;
        esac
        i=$((i + 1))
    done

    cd "$SCRIPT_DIR"
    # shellcheck disable=SC2086
    $COMPOSE_CMD -f "$COMPOSE_FILE" logs $tail_flag $follow_flag "$APP_SERVICE"
}

# ---------------------------------------------------------------------------
# cmd_status
# Estado del stack + healthcheck HTTP.
# ---------------------------------------------------------------------------
cmd_status() {
    local sp_port
    sp_port="$(get_env_var SP_PORT 8003)"

    header "Estado del stack"
    echo ""
    cd "$SCRIPT_DIR"
    $COMPOSE_CMD -f "$COMPOSE_FILE" ps 2>/dev/null || true
    echo ""

    local url="http://localhost:${sp_port}/health"
    log "Verificando endpoint: $url"
    if curl -sf "$url" -o /tmp/_sp_health.json 2>/dev/null; then
        ok "Servicio respondiendo — $(cat /tmp/_sp_health.json)"
        rm -f /tmp/_sp_health.json
    else
        warn "El servicio no responde en $url"
        warn "Revisa los logs con: ./deploy.sh logs"
    fi
}

# ---------------------------------------------------------------------------
# cmd_clean
# Borrado completo: contenedor, imagen y volúmenes. Requiere confirmar "si".
# ---------------------------------------------------------------------------
cmd_clean() {
    header "Limpieza completa del entorno"
    echo ""
    warn "Se eliminarán:"
    warn "  · El stack Docker (contenedor + red)"
    warn "  · La imagen Docker: $IMAGE_NAME"
    warn "  · Los volúmenes virtuales de Docker Compose"
    echo ""

    confirm "¿Confirmas la limpieza total del entorno?"

    header "Ejecutando limpieza"

    cd "$SCRIPT_DIR"
    log "Deteniendo stack y eliminando volúmenes..."
    $COMPOSE_CMD -f "$COMPOSE_FILE" down --volumes --remove-orphans 2>/dev/null || true

    log "Eliminando imagen Docker..."
    docker rmi "$IMAGE_NAME" 2>/dev/null && ok "Imagen eliminada" || warn "Imagen no encontrada (ya eliminada o nunca construida)"

    ok "Entorno limpiado completamente."
}

# ---------------------------------------------------------------------------
# cmd_help
# Menú estructurado de ayuda.
# ---------------------------------------------------------------------------
cmd_help() {
    echo ""
    bold "  deploy.sh — SharePoint Connector · Gestor de Despliegue"
    echo ""
    bold "  USO:"
    echo "    ./deploy.sh <comando> [opciones]"
    echo ""
    bold "  COMANDOS:"
    echo ""
    echo -e "    ${_CYAN}start${_RESET}     Build + despliegue completo"
    echo -e "              ${_BOLD}--pull${_RESET}      Hace git pull antes de construir"
    echo -e "              ${_BOLD}--no-build${_RESET}  Usa imagen existente (sin rebuild)"
    echo ""
    echo -e "    ${_CYAN}stop${_RESET}      Detiene el stack"
    echo ""
    echo -e "    ${_CYAN}restart${_RESET}   Reinicio ligero sin destruir el contenedor"
    echo ""
    echo -e "    ${_CYAN}update${_RESET}    Rebuild imagen + recrear contenedor"
    echo -e "              ${_BOLD}--pull${_RESET}      Hace git pull antes de reconstruir"
    echo ""
    echo -e "    ${_CYAN}logs${_RESET}      Muestra logs del servicio principal"
    echo -e "              ${_BOLD}-f${_RESET}          Sigue los logs en tiempo real"
    echo -e "              ${_BOLD}-n N${_RESET}        Muestra las últimas N líneas (defecto: 100)"
    echo ""
    echo -e "    ${_CYAN}status${_RESET}    Estado del stack + health check HTTP"
    echo ""
    echo -e "    ${_CYAN}clean${_RESET}     Elimina TODOS los recursos (requiere confirmar con 'si')"
    echo "              Borra contenedor, imagen y volúmenes"
    echo ""
    echo -e "    ${_CYAN}help${_RESET}      Este mensaje"
    echo ""
    bold "  PRIMERA VEZ:"
    echo "    1. cp devops/.env.example devops/.env"
    echo "    2. Editar devops/.env con TENANT_ID, CLIENT_ID y CLIENT_SECRET"
    echo "    3. ./deploy.sh start"
    echo ""
    bold "  EJEMPLOS:"
    echo "    ./deploy.sh start                  # Build y arranque completo"
    echo "    ./deploy.sh start --no-build       # Arrancar sin reconstruir"
    echo "    ./deploy.sh update --pull          # Pull + rebuild + recrear"
    echo "    ./deploy.sh logs -f                # Logs en tiempo real"
    echo "    ./deploy.sh logs -n 50             # Últimas 50 líneas"
    echo "    ./deploy.sh status                 # Estado + health check"
    echo "    ./deploy.sh restart                # Reinicio rápido"
    echo "    ./deploy.sh clean                  # Reset total (destructivo)"
    echo ""
    bold "  REQUISITOS:"
    echo "    · Docker Engine >= 24.x"
    echo "    · Docker Compose plugin v2 (recomendado) o docker-compose v1"
    echo "    · Archivo devops/.env configurado con credenciales Azure AD"
    echo ""
}

# ===========================================================================
# DISPATCHER PRINCIPAL
# ===========================================================================
COMMAND="${1:-help}"
shift || true

case "$COMMAND" in
    start)          check_requirements; cmd_start   "$@" ;;
    stop)           check_requirements; cmd_stop    "$@" ;;
    restart)        check_requirements; cmd_restart "$@" ;;
    update)         check_requirements; cmd_update  "$@" ;;
    logs)           check_requirements; cmd_logs    "$@" ;;
    status)         check_requirements; cmd_status  "$@" ;;
    clean)          check_requirements; cmd_clean   "$@" ;;
    help|--help|-h) cmd_help ;;
    *)
        err "Comando desconocido: '$COMMAND'"
        cmd_help
        exit 1
        ;;
esac
