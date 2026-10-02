"""
=============================================================================
Disneyland Paris - Restaurant Availability Monitor (Relay Edition)
=============================================================================
Script de monitoreo automatizado de disponibilidad de mesas en Disneyland Paris.
Diseñado para consultar directamente la API interna de Book-Dine de Disney.
Soporta:
1. Auto-Refresh de sesión mediante OneID (DISNEY_REFRESH_TOKEN).
2. Modo Relevo (--relay) para ejecución continua 24/7 en GitHub Actions
   (turnos de 5 horas que se re-disparan automáticamente sin parar).
3. Notificación de inicio a ntfy para comprobación inmediata en tu móvil.
=============================================================================
"""

import os
import sys
import time
import random
import logging
import argparse
from datetime import datetime
from typing import Dict, List, Optional, Any

# Asegurar soporte de caracteres UTF-8 en consolas de Windows y flushing inmediato
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

import requests
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env si existe (modo local)
load_dotenv()

# =============================================================================
# CONSTANTES DE ENDPOINTS
# =============================================================================
DISNEY_AVAILABILITY_URL = (
    "https://dlp-is-sales-drs-book-dine.wdprapps.disney.com"
    "/prod/v4/book-dine/availabilities/en-int?scope=Restaurant"
)
DISNEY_REFRESH_URL = (
    "https://registerdisney.go.com/jgc/v8/client/TPR-DLP.WEB-PROD/guest/refresh-auth"
)
BOOKING_PAGE_URL = "https://bookrestaurants.disneylandparis.com/en-int?id={restaurant_id}"

# Configuración de registro (Logging)
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
handler.setFormatter(formatter)

logger = logging.getLogger("DisneyMonitor")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(handler)


def mask_secret(value: Optional[str], visible_chars: int = 4) -> str:
    """Oculta un valor sensible para no exponerlo en logs públicos."""
    if not value:
        return "NO_CONFIGURADO"
    if len(value) <= visible_chars:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible_chars)}{value[-visible_chars:]}"


# =============================================================================
# 1. FUNCIÓN DE AUTO-REFRESH DE TOKEN (DISNEY ONEID)
# =============================================================================
def refresh_access_token(refresh_token: str) -> Optional[str]:
    """
    Renueva automáticamente el access_token utilizando el refreshToken de Disney OneID.
    Permite que el script funcione de forma 100% autónoma durante semanas o meses.
    """
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://bookrestaurants.disneylandparis.com",
        "Referer": "https://bookrestaurants.disneylandparis.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    payload = {"refreshToken": refresh_token.strip()}

    try:
        response = requests.post(DISNEY_REFRESH_URL, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            data = response.json().get("data", {})
            token_obj = data.get("token", {}) if data else {}
            new_access_token = token_obj.get("access_token")
            if new_access_token:
                logger.info("✨ Token de Disney renovado automáticamente con éxito vía OneID.")
                return new_access_token
            else:
                logger.error("❌ Respuesta 200 de Disney pero no se encontró access_token.")
                return None
        else:
            logger.error(
                f"❌ Error al renovar token en Disney OneID: HTTP {response.status_code} - {response.text[:200]}"
            )
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red al intentar renovar el token: {e}")
        return None


# =============================================================================
# 2. FUNCIÓN DE CABECERAS DINÁMICAS
# =============================================================================
def build_headers(api_key: str, auth_token: str) -> Dict[str, str]:
    """Construye las cabeceras HTTP necesarias para Disney API."""
    formatted_token = (
        auth_token.strip()
        if auth_token.strip().lower().startswith("bearer ")
        else f"Bearer {auth_token.strip()}"
    )

    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
        "Authorization": formatted_token,
        "Content-Type": "application/json",
        "Origin": "https://bookrestaurants.disneylandparis.com",
        "Referer": "https://bookrestaurants.disneylandparis.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-api-key": api_key.strip(),
    }


# =============================================================================
# 3. FUNCIÓN DE CONSULTA POST (API DISNEY)
# =============================================================================
def check_availability(
    session: requests.Session,
    restaurant_id: str,
    target_date: str,
    party_size: int,
    headers: Dict[str, str],
    timeout: int = 15,
) -> Optional[List[Dict[str, Any]]]:
    """Realiza la petición HTTP POST al endpoint de disponibilidad de Disney."""
    payload = {
        "partyMix": party_size,
        "session": 0,
        "restaurantId": restaurant_id,
        "sourceSite": "web",
        "date": target_date,
    }

    try:
        response = session.post(
            DISNEY_AVAILABILITY_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )

        if response.status_code == 200:
            return response.json()

        if response.status_code in (401, 403):
            logger.error(f"❌ Error {response.status_code}: Token caducado o sin permisos.")
            return None

        if response.status_code == 429:
            logger.warning("⚠️ Error 429: Limitación temporal de peticiones.")
            return None

        logger.error(
            f"❌ Error inesperado de la API (HTTP {response.status_code}): {response.text[:200]}"
        )
        return None

    except requests.exceptions.Timeout:
        logger.warning("⏱️ Timeout de conexión con Disney.")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red durante la consulta: {e}")
        return None


# =============================================================================
# 4. FUNCIÓN DE PROCESAMIENTO DE RESPUESTA
# =============================================================================
def parse_available_slots(
    response_data: Any,
    target_period: str = "Lunch",
) -> List[str]:
    """Examina el JSON de Disney y extrae los horarios disponibles."""
    available_slots: List[str] = []

    if not isinstance(response_data, list):
        return available_slots

    for day_schedule in response_data:
        meal_periods = day_schedule.get("mealPeriods", [])
        for period in meal_periods:
            current_period = period.get("mealPeriod", "")
            
            if target_period.lower() in current_period.lower():
                slots = period.get("slotList", [])
                for slot in slots:
                    slot_time = slot.get("time", "Hora desconocida")
                    is_available = slot.get("available")
                    
                    if str(is_available).strip().lower() == "true":
                        available_slots.append(slot_time)

    return available_slots


# =============================================================================
# 5. SISTEMA DE ALERTAS (ntfy.sh)
# =============================================================================
def send_ntfy_alert(
    ntfy_url_or_topic: str,
    restaurant_name: str,
    target_date: str,
    slots: List[str],
    restaurant_id: str,
) -> bool:
    """Envía notificación push urgente cuando se encuentra mesa libre."""
    if not ntfy_url_or_topic:
        return False

    url = (
        ntfy_url_or_topic.strip()
        if ntfy_url_or_topic.startswith("http")
        else f"https://ntfy.sh/{ntfy_url_or_topic.strip()}"
    )

    booking_link = BOOKING_PAGE_URL.format(restaurant_id=restaurant_id)
    slots_str = ", ".join(slots)
    
    title = f"🍽️ ¡MESA DISPONIBLE EN {restaurant_name.upper()}!"
    message = (
        f"¡Se han encontrado horarios disponibles para el {target_date}!\n\n"
        f"⏰ Horarios: {slots_str}\n\n"
        f"Toca esta notificación para reservar de inmediato antes de que se agoten:"
    )

    headers = {
        "Title": title,
        "Priority": "urgent",
        "Tags": "tada,fork_and_knife,disney",
        "Click": booking_link,
    }

    try:
        res = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        return res.status_code == 200
    except Exception:
        return False


def send_startup_ping(ntfy_url_or_topic: str, restaurant_name: str, target_date: str, mode: str) -> None:
    """Envía una notificación informativa de inicio para confirmar que el bot está activo."""
    if not ntfy_url_or_topic:
        return
    url = (
        ntfy_url_or_topic.strip()
        if ntfy_url_or_topic.startswith("http")
        else f"https://ntfy.sh/{ntfy_url_or_topic.strip()}"
    )
    title = "🚀 MONITOR ACTIVO EN LA NUBE (GITHUB)"
    message = (
        f"El monitor ha comenzado su turno en GitHub Actions ({mode}).\n"
        f"📍 Restaurante: {restaurant_name}\n"
        f"📅 Fecha: {target_date}\n"
        f"⏱️ Chequeando disponibilidad cada 10 a 15 minutos de forma continua."
    )
    headers = {
        "Title": title,
        "Priority": "default",
        "Tags": "white_check_mark,robot",
    }
    try:
        requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        logger.info("📢 Notificación push de inicio enviada a ntfy.")
    except Exception as e:
        logger.warning(f"No se pudo enviar ping de inicio: {e}")


def trigger_next_relay_workflow(repo_slug: str, pat_token: str) -> bool:
    """Dispara automáticamente el siguiente turno de 5 horas en GitHub Actions."""
    url = f"https://api.github.com/repos/{repo_slug}/actions/workflows/disney_monitor.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {pat_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Disney-Dining-Monitor-Relay",
    }
    payload = {"ref": "main"}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code == 204:
            logger.info("🎉 ¡Siguiente turno de relevo disparado con éxito en GitHub Actions!")
            return True
        else:
            logger.error(f"❌ Error al disparar relevo en GitHub API: HTTP {res.status_code} - {res.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Excepción al disparar relevo en GitHub: {e}")
        return False


# =============================================================================
# 6. EJECUCIÓN PRINCIPAL
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Disney Restaurant Availability Monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola consulta y finaliza",
    )
    parser.add_argument(
        "--relay",
        action="store_true",
        help="Modo relevo continuo 24/7 para GitHub Actions (corre 5 horas continuas y se re-dispara)",
    )
    parser.add_argument(
        "--max-hours",
        type=float,
        default=5.0,
        help="Duración máxima en horas del turno antes del relevo (default: 5.0)",
    )
    args = parser.parse_args()

    auth_token = os.getenv("DISNEY_AUTH_TOKEN")
    refresh_token = os.getenv("DISNEY_REFRESH_TOKEN")
    api_key = os.getenv("DISNEY_API_KEY")
    ntfy_channel = os.getenv("NTFY_TOPIC")
    github_pat = os.getenv("GH_PAT")
    github_repo = os.getenv("GITHUB_REPOSITORY", "j0serca/disney-dining-monitor")
    
    restaurant_id = os.getenv("RESTAURANT_ID")
    restaurant_name = os.getenv("RESTAURANT_NAME", "Restaurante Disney")
    target_date = os.getenv("TARGET_DATE")
    party_size_raw = os.getenv("PARTY_SIZE")
    meal_period = os.getenv("MEAL_PERIOD", "Lunch")
    
    min_delay_mins = float(os.getenv("MIN_DELAY_MINUTES", "10"))
    max_delay_mins = float(os.getenv("MAX_DELAY_MINUTES", "15"))

    # Validaciones obligatorias
    missing = []
    if not auth_token and not refresh_token:
        missing.append("DISNEY_AUTH_TOKEN o DISNEY_REFRESH_TOKEN")
    if not api_key:
        missing.append("DISNEY_API_KEY")
    if not ntfy_channel:
        missing.append("NTFY_TOPIC")
    if not restaurant_id:
        missing.append("RESTAURANT_ID")
    if not target_date:
        missing.append("TARGET_DATE")
    if not party_size_raw:
        missing.append("PARTY_SIZE")

    if missing:
        logger.error(f"\n[ERROR DE CONFIGURACIÓN] Faltan los siguientes secretos:\n 👉 {', '.join(missing)}\n")
        sys.exit(1)

    party_size = int(party_size_raw)

    # Auto-Refresh de access_token al iniciar
    if refresh_token:
        logger.info("🔄 DISNEY_REFRESH_TOKEN detectado. Obteniendo access_token fresco...")
        new_token = refresh_access_token(refresh_token)
        if new_token:
            auth_token = new_token

    if not auth_token:
        logger.error("❌ No hay un access_token válido disponible.")
        sys.exit(1)

    mode_label = "MODO ONCE" if args.once else ("MODO RELEVO 24/7" if args.relay else "MODO LOCAL")
    logger.info("=" * 65)
    logger.info(f"🏰 MONITOR DE DISPONIBILIDAD - DISNEYLAND PARIS [{mode_label}]")
    logger.info("=" * 65)
    logger.info(f"📍 Restaurante  : {restaurant_name} (ID: {restaurant_id})")
    logger.info(f"📅 Fecha         : {target_date}")
    logger.info(f"👥 Comensales    : {party_size} personas")
    logger.info(f"🍴 Período       : {meal_period}")
    logger.info(f"📢 Canal ntfy    : https://ntfy.sh/{mask_secret(ntfy_channel)}")
    logger.info(f"🔑 API Key       : {mask_secret(api_key)}")
    logger.info(f"🎟️ Auth Token    : {mask_secret(auth_token)}")
    if refresh_token:
        logger.info(f"🔄 Refresh Token : {mask_secret(refresh_token)} (Auto-Refresh ACTIVO ✅)")
    if not args.once:
        logger.info(f"⏳ Intervalo     : {min_delay_mins} a {max_delay_mins} minutos (con jitter)")
        if args.relay:
            logger.info(f"⏱️ Turno Relevo  : {args.max_hours} horas continuas")
    logger.info("-" * 65)

    # Notificación push informativa a tu móvil de que el monitor está activo
    if args.relay or not args.once:
        send_startup_ping(ntfy_channel, restaurant_name, target_date, mode_label)

    session = requests.Session()
    headers = build_headers(api_key=api_key, auth_token=auth_token)

    # Modo puntual individual
    if args.once:
        logger.info("🔍 Ejecutando verificación puntual...")
        data = check_availability(
            session=session,
            restaurant_id=restaurant_id,
            target_date=target_date,
            party_size=party_size,
            headers=headers,
        )
        if data is not None:
            available_slots = parse_available_slots(data, target_period=meal_period)
            if available_slots:
                logger.info(f"🚨 ¡¡MESAS ENCONTRADAS!! Horarios: {available_slots}")
                send_ntfy_alert(ntfy_channel, restaurant_name, target_date, available_slots, restaurant_id)
            else:
                logger.info(f"ℹ️ Sin mesas libres de '{meal_period}' para {party_size} comensales el {target_date}.")
        return

    # Modo bucle continuo (Local o Relevo 24/7 en GitHub Actions)
    start_time = time.time()
    max_seconds = args.max_hours * 3600
    iteration = 1

    while True:
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        logger.info(f"🔍 [Intento #{iteration} - {timestamp_str}] Verificando disponibilidad...")

        # Renovar access token cada 2 horas automáticamente si tenemos refresh token
        if refresh_token and iteration > 1:
            fresh_token = refresh_access_token(refresh_token)
            if fresh_token:
                auth_token = fresh_token
                headers = build_headers(api_key=api_key, auth_token=auth_token)

        data = check_availability(
            session=session,
            restaurant_id=restaurant_id,
            target_date=target_date,
            party_size=party_size,
            headers=headers,
        )

        if data is not None:
            available_slots = parse_available_slots(data, target_period=meal_period)
            if available_slots:
                logger.info("🎉" * 20)
                logger.info(f"🚨 ¡¡MESAS ENCONTRADAS!! Horarios: {available_slots}")
                logger.info("🎉" * 20)
                send_ntfy_alert(ntfy_channel, restaurant_name, target_date, available_slots, restaurant_id)
            else:
                logger.info(
                    f"ℹ️ Sin disponibilidad de '{meal_period}' para {party_size} comensales el {target_date}."
                )
        else:
            logger.warning("⚠️ No se pudo obtener respuesta válida en este intento.")

        # Si estamos en modo relevo, verificar si cumplimos el tiempo del turno
        elapsed = time.time() - start_time
        if args.relay and elapsed >= max_seconds:
            logger.info("=" * 65)
            logger.info(f"🏁 Turno completado ({elapsed / 3600:.2f} horas). Pasando el relevo al siguiente runner...")
            logger.info("=" * 65)
            if github_pat:
                trigger_next_relay_workflow(github_repo, github_pat)
            else:
                logger.warning("⚠️ No se encontró GH_PAT para re-disparar el relevo automáticamente.")
            break

        jitter_seconds = random.uniform(min_delay_mins * 60, max_delay_mins * 60)
        next_check_mins = jitter_seconds / 60
        next_time = datetime.fromtimestamp(time.time() + jitter_seconds).strftime("%H:%M:%S")

        logger.info(f"💤 Esperando {next_check_mins:.2f} minutos (próxima revisión a las {next_time})...\n")
        iteration += 1

        try:
            time.sleep(jitter_seconds)
        except KeyboardInterrupt:
            logger.info("\n🛑 Monitor detenido por el usuario. ¡Hasta pronto!")
            break


if __name__ == "__main__":
    main()
