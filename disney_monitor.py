"""
=============================================================================
Disneyland Paris - Bistrot Chez Rémy Availability Monitor
=============================================================================
Script de monitoreo automatizado de disponibilidad de mesas en Disneyland Paris.
Diseñado para consultar directamente la API interna de Book-Dine de Disney
utilizando peticiones HTTP (requests) de alta eficiencia, sin Selenium ni Playwright.
Soporta ejecución continua local o ejecución individual (--once) para GitHub Actions/cron.
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

# Asegurar soporte de caracteres UTF-8 (emojis y acentos) en consolas de Windows
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

# Cargar variables de entorno desde el archivo .env si existe
load_dotenv()

# =============================================================================
# CONFIGURACIÓN Y CONSTANTES
# =============================================================================
DISNEY_AVAILABILITY_URL = (
    "https://dlp-is-sales-drs-book-dine.wdprapps.disney.com"
    "/prod/v4/book-dine/availabilities/en-int?scope=Restaurant"
)
BOOKING_PAGE_URL = "https://bookrestaurants.disneylandparis.com/en-int?id={restaurant_id}"

# Valores extraídos directamente de la inspección del archivo .HAR
DEFAULT_API_KEY = "AaQHDoRgDa66dl2PQuTEe9DjyBlH8ylV4LxnldFY"
DEFAULT_RESTAURANT_ID = "P2TR02"     # Bistrot Chez Rémy
DEFAULT_TARGET_DATE = "2026-11-22"   # 22 de noviembre de 2026
DEFAULT_PARTY_SIZE = 2              # 2 personas
DEFAULT_MEAL_PERIOD = "Lunch"        # Almuerzo / Comida

# Configuración de registro (Logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DisneyMonitor")


# =============================================================================
# 1. FUNCIÓN DE CABECERAS DINÁMICAS
# =============================================================================
def build_headers(api_key: str, auth_token: str) -> Dict[str, str]:
    """
    Construye las cabeceras HTTP necesarias para emular fielmente la petición
    del navegador a la API de Disney.
    """
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
# 2. FUNCIÓN DE CONSULTA POST (API DISNEY)
# =============================================================================
def check_availability(
    session: requests.Session,
    restaurant_id: str,
    target_date: str,
    party_size: int,
    headers: Dict[str, str],
    timeout: int = 15,
) -> Optional[List[Dict[str, Any]]]:
    """
    Realiza la petición HTTP POST al endpoint de disponibilidad de Disneyland Paris.
    """
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

        if response.status_code == 401:
            logger.error(
                "❌ Error 401 (No autorizado): El Token de Disney ha caducado o es inválido. "
                "Por favor, renueva el token desde el navegador y actualiza tu .env o Secrets."
            )
            return None

        if response.status_code == 403:
            logger.error("❌ Error 403 (Prohibido): Petición bloqueada por Disney o API Key inválida.")
            return None

        if response.status_code == 429:
            logger.warning("⚠️ Error 429 (Too Many Requests): Limitación temporal de peticiones.")
            return None

        logger.error(
            f"❌ Error inesperado de la API (HTTP {response.status_code}): {response.text[:200]}"
        )
        return None

    except requests.exceptions.Timeout:
        logger.warning("⏱️ Timeout de conexión con Disney. Se reintentará en el próximo ciclo.")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error de red durante la consulta: {e}")
        return None


# =============================================================================
# 3. FUNCIÓN DE PROCESAMIENTO DE RESPUESTA
# =============================================================================
def parse_available_slots(
    response_data: Any,
    target_period: str = "Lunch",
) -> List[str]:
    """
    Examina el JSON retornado por Disney y extrae los horarios disponibles para el período buscado.
    """
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
                    
                    # Soporta tanto string 'true' como booleano True
                    if str(is_available).strip().lower() == "true":
                        available_slots.append(slot_time)

    return available_slots


# =============================================================================
# 4. SISTEMA DE ALERTAS GRATUITO (ntfy.sh)
# =============================================================================
def send_ntfy_alert(
    ntfy_url_or_topic: str,
    restaurant_name: str,
    target_date: str,
    slots: List[str],
    restaurant_id: str,
) -> bool:
    """
    Envía una notificación push instantánea y gratuita a través de ntfy.sh con enlace directo de reserva.
    """
    if not ntfy_url_or_topic:
        logger.warning("⚠️ No se ha configurado NTFY_TOPIC. Alerta omitida.")
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
        "Priority": "urgent",       # Prioridad alta (sonido y vibración)
        "Tags": "tada,fork_and_knife,disney",
        "Click": booking_link,       # Al tocar la alerta abre la web de Disney
    }

    try:
        res = requests.post(url, data=message.encode("utf-8"), headers=headers, timeout=10)
        if res.status_code == 200:
            logger.info(f"🔔 ¡Alerta enviada exitosamente a ntfy ({url})!")
            return True
        else:
            logger.error(f"❌ Error al enviar notificación a ntfy: HTTP {res.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Error al conectar con ntfy: {e}")
        return False


# =============================================================================
# 5. EJECUCIÓN PRINCIPAL (SOPORTA LOCAL CONTINUO O GITHUB ACTIONS)
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
    api_key = os.getenv("DISNEY_API_KEY", DEFAULT_API_KEY)
    ntfy_channel = os.getenv("NTFY_TOPIC", "disney_chez_remy_alerta")
    
    restaurant_id = os.getenv("RESTAURANT_ID", DEFAULT_RESTAURANT_ID)
    restaurant_name = os.getenv("RESTAURANT_NAME", "Bistrot Chez Rémy")
    target_date = os.getenv("TARGET_DATE", DEFAULT_TARGET_DATE)
    party_size = int(os.getenv("PARTY_SIZE", DEFAULT_PARTY_SIZE))
    meal_period = os.getenv("MEAL_PERIOD", DEFAULT_MEAL_PERIOD)
    
    min_delay_mins = float(os.getenv("MIN_DELAY_MINUTES", "10"))
    max_delay_mins = float(os.getenv("MAX_DELAY_MINUTES", "15"))

    if not auth_token or auth_token == "TU_TOKEN_BEARER_AQUI":
        logger.error(
            "\n[ERROR CRÍTICO] Falta configurar DISNEY_AUTH_TOKEN en el entorno o archivo .env.\n"
        )
        sys.exit(1)

    logger.info("=" * 65)
    logger.info(f"🏰 MONITOR DE DISPONIBILIDAD - DISNEYLAND PARIS {'[MODO ONCE]' if args.once else ''}")
    logger.info("=" * 65)
    logger.info(f"📍 Restaurante : {restaurant_name} (ID: {restaurant_id})")
    logger.info(f"📅 Fecha        : {target_date}")
    logger.info(f"👥 Comensales   : {party_size} personas")
    logger.info(f"🍴 Período      : {meal_period}")
    logger.info(f"📢 Canal ntfy   : https://ntfy.sh/{ntfy_channel}")
    if not args.once:
        logger.info(f"⏳ Intervalo    : {min_delay_mins} a {max_delay_mins} minutos (con jitter)")
    logger.info("-" * 65)

    session = requests.Session()
    headers = build_headers(api_key=api_key, auth_token=auth_token)

    # Si se pasa --once (por ejemplo desde GitHub Actions)
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

    # Bucle continuo para ejecución local en PC
    iteration = 1
    while True:
        timestamp_str = datetime.now().strftime("%H:%M:%S")
        logger.info(f"🔍 [Intento #{iteration} - {timestamp_str}] Verificando disponibilidad...")

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
