"""
=============================================================================
Disneyland Paris - Restaurant Availability Monitor (Auto-Refresh Edition)
=============================================================================
Script de monitoreo automatizado de disponibilidad de mesas en Disneyland Paris.
Diseñado para consultar directamente la API interna de Book-Dine de Disney.
Soporta Auto-Refresh de sesión mediante el endpoint oficial de Disney OneID:
/guest/refresh-auth con DISNEY_REFRESH_TOKEN.
Zero-Hardcoding: Toda la configuración se carga estrictamente desde el entorno.
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

# Asegurar soporte de caracteres UTF-8 en consolas de Windows
if sys.platform.startswith("win"):
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import requests
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env si existe (modo local)
load_dotenv()

# =============================================================================
# CONSTANTES DE ENDPOINT DE DISNEY
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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DisneyMonitor")


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
                logger.error("❌ Respuesta 200 de Disney pero no se encontró access_token en el JSON.")
                return None
        else:
            logger.error(
                f"❌ Error al renovar token en Disney OneID: HTTP {response.status_code} - {response.text[:200]}"
            )
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de conexión al intentar renovar el token: {e}")
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
    ntfy_channel: Optional[str] = None,
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
            logger.error(
                f"❌ Error {response.status_code}: Token caducado o sin permisos."
            )
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
    """Envía notificación push de mesa disponible."""
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


def send_token_expired_alert(ntfy_url_or_topic: str) -> None:
    """Envía advertencia si el token caduca definitivamente."""
    url = (
        ntfy_url_or_topic.strip()
        if ntfy_url_or_topic.startswith("http")
        else f"https://ntfy.sh/{ntfy_url_or_topic.strip()}"
    )
    title = "⚠️ DISNEY MONITOR: TOKEN EXPIRADO"
    message = (
        "El token de autorización de Disney ha caducado. "
        "Inicia sesión en la web de Disney y actualiza DISNEY_REFRESH_TOKEN para reanudar el monitoreo."
    )
    headers = {
        "Title": title,
        "Priority": "high",
        "Tags": "warning,key",
    }
    try:
        requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception:
        pass


# =============================================================================
# 6. EJECUCIÓN PRINCIPAL
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Disney Restaurant Availability Monitor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola consulta y finaliza (ideal para GitHub Actions o Cron jobs)",
    )
    args = parser.parse_args()

    auth_token = os.getenv("DISNEY_AUTH_TOKEN")
    refresh_token = os.getenv("DISNEY_REFRESH_TOKEN")
    api_key = os.getenv("DISNEY_API_KEY")
    ntfy_channel = os.getenv("NTFY_TOPIC")
    
    restaurant_id = os.getenv("RESTAURANT_ID")
    restaurant_name = os.getenv("RESTAURANT_NAME", "Restaurante Disney")
    target_date = os.getenv("TARGET_DATE")
    party_size_raw = os.getenv("PARTY_SIZE")
    meal_period = os.getenv("MEAL_PERIOD", "Lunch")
    
    min_delay_mins = float(os.getenv("MIN_DELAY_MINUTES", "10"))
    max_delay_mins = float(os.getenv("MAX_DELAY_MINUTES", "15"))

    # Validaciones
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
        logger.error(
            f"\n[ERROR DE CONFIGURACIÓN] Faltan los siguientes secretos requeridos:\n"
            f" 👉 {', '.join(missing)}\n"
        )
        sys.exit(1)

    party_size = int(party_size_raw)

    # Lógica de Auto-Refresh Inteligente:
    # Si tenemos DISNEY_REFRESH_TOKEN, renovamos el access_token automáticamente
    if refresh_token:
        logger.info("🔄 DISNEY_REFRESH_TOKEN detectado. Obteniendo access_token fresco de Disney OneID...")
        new_token = refresh_access_token(refresh_token)
        if new_token:
            auth_token = new_token
        else:
            logger.warning("⚠️ No se pudo auto-renovar con refresh_token. Intentando con DISNEY_AUTH_TOKEN existente...")

    if not auth_token:
        logger.error("❌ No hay un access_token válido disponible.")
        if ntfy_channel:
            send_token_expired_alert(ntfy_channel)
        sys.exit(1)

    logger.info("=" * 65)
    logger.info(f"🏰 MONITOR DE DISPONIBILIDAD - DISNEYLAND PARIS {'[MODO ONCE]' if args.once else ''}")
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
    logger.info("-" * 65)

    session = requests.Session()
    headers = build_headers(api_key=api_key, auth_token=auth_token)

    # Modo puntual (GitHub Actions)
    if args.once:
        logger.info("🔍 Ejecutando verificación puntual...")
        data = check_availability(
            session=session,
            restaurant_id=restaurant_id,
            target_date=target_date,
            party_size=party_size,
            headers=headers,
            ntfy_channel=ntfy_channel,
        )

        if data is not None:
            available_slots = parse_available_slots(data, target_period=meal_period)
            if available_slots:
                logger.info("🎉" * 20)
                logger.info(f"🚨 ¡¡MESAS ENCONTRADAS!! Horarios: {available_slots}")
                logger.info("🎉" * 20)
                send_ntfy_alert(
                    ntfy_url_or_topic=ntfy_channel,
                    restaurant_name=restaurant_name,
                    target_date=target_date,
                    slots=available_slots,
                    restaurant_id=restaurant_id,
                )
            else:
                logger.info(f"ℹ️ Sin mesas libres de '{meal_period}' para {party_size} comensales el {target_date}.")
        else:
            logger.warning("⚠️ No se pudo obtener respuesta válida de Disney.")
        return

    # Modo continuo local
    iteration = 1
    while True:
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        logger.info(f"🔍 [Intento #{iteration} - {timestamp_str}] Verificando disponibilidad...")

        # Si tenemos refresh token, refrescamos periódicamente
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
            ntfy_channel=ntfy_channel,
        )

        if data is not None:
            available_slots = parse_available_slots(data, target_period=meal_period)

            if available_slots:
                logger.info("🎉" * 20)
                logger.info(f"🚨 ¡¡MESAS ENCONTRADAS!! Horarios: {available_slots}")
                logger.info("🎉" * 20)

                send_ntfy_alert(
                    ntfy_url_or_topic=ntfy_channel,
                    restaurant_name=restaurant_name,
                    target_date=target_date,
                    slots=available_slots,
                    restaurant_id=restaurant_id,
                )
            else:
                logger.info(
                    f"ℹ️ Sin disponibilidad de '{meal_period}' para {party_size} personas el {target_date}."
                )
        else:
            logger.warning("⚠️ No se pudo obtener respuesta válida en este intento.")

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
