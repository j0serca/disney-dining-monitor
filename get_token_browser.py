"""
Obtiene automáticamente el access_token de alto privilegio de Disney OneID
utilizando Playwright (Chromium headless) dentro de GitHub Actions.
"""

import os
import sys
import time
import json
import logging
import requests
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("DisneyPlaywrightAuth")


def test_token_availability(token: str) -> bool:
    """Verifica si el token obtenido es aceptado con HTTP 200 por la API de restaurantes."""
    url = "https://dlp-is-sales-drs-book-dine.wdprapps.disney.com/prod/v4/book-dine/availabilities/en-int?scope=Restaurant"
    api_key = os.getenv("DISNEY_API_KEY", "AaQHDoRgDa66dl2PQuTEe9DjyBlH8ylV4LxnldFY")
    restaurant_id = os.getenv("RESTAURANT_ID", "P2TR02")
    target_date = os.getenv("TARGET_DATE", "2026-11-22")
    party_size = int(os.getenv("PARTY_SIZE", "2"))

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://bookrestaurants.disneylandparis.com",
        "Referer": "https://bookrestaurants.disneylandparis.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-api-key": api_key,
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "partyMix": party_size,
        "session": 0,
        "restaurantId": restaurant_id,
        "sourceSite": "web",
        "date": target_date,
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        logger.info(f"🧪 Prueba de API de Restaurantes: HTTP {res.status_code}")
        if res.status_code == 200:
            logger.info("🎉 ¡TOKEN VALIDADO CON ÉXITO! La API de Disney respondió 200 OK.")
            return True
        else:
            logger.error(f"❌ Error al consultar disponibilidad con el token: {res.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"❌ Excepción al probar el token: {e}")
        return False


def get_token_via_browser(email: str, password: str) -> str:
    logger.info("🚀 Iniciando navegador Chromium para login automático en Disney OneID...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="es-ES",
        )

        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            logger.info("🌐 Navegando a la página de reservas de Disneyland Paris...")
            page.goto("https://bookrestaurants.disneylandparis.com/en-int?id=P2TR02", wait_until="networkidle", timeout=60000)

            # Aceptar cookies de OneTrust si aparecen
            try:
                cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept All'), button:has-text('Aceptar todas')").first
                if cookie_btn.is_visible(timeout=5000):
                    cookie_btn.click()
                    logger.info("🍪 Cookies de OneTrust aceptadas.")
            except Exception:
                pass

            # Esperar a que window.did esté cargado
            page.wait_for_function("typeof window.did !== 'undefined'", timeout=20000)
            logger.info("✅ Objeto window.did detectado.")

            # Abrir modal de login
            logger.info("🔑 Abriendo modal de inicio de sesión de Disney OneID...")
            page.evaluate("window.did.login()")
            time.sleep(3)

            # Localizar el iframe de OneID
            oneid_frame = None
            for _ in range(10):
                for frame in page.frames:
                    if "registerdisney.go.com" in frame.url or "bundle/web" in frame.url or "TPR-DLP" in frame.url:
                        oneid_frame = frame
                        break
                if oneid_frame:
                    break
                time.sleep(1)

            if not oneid_frame:
                page.screenshot(path="error_login.png")
                raise RuntimeError("No se encontró el iframe de autenticación de Disney OneID.")

            logger.info(f"🖼️ Iframe de OneID encontrado: {oneid_frame.url[:80]}...")

            # Ingresar correo electrónico
            logger.info("✍️ Ingresando correo...")
            email_input = oneid_frame.locator("input[type='email'], input[name='loginValue'], input[name='email']").first
            email_input.wait_for(state="visible", timeout=15000)
            email_input.fill(email)

            # Verificar si la contraseña ya está visible o si hay paso 'Continue'
            pass_input = oneid_frame.locator("input[type='password'], input[name='password']").first
            if not pass_input.is_visible():
                continue_btn = oneid_frame.locator("button:has-text('Continue'), button:has-text('Continuar'), button[id*='btn-continue']").first
                if continue_btn.is_visible():
                    continue_btn.click()
                    time.sleep(2)

            logger.info("✍️ Ingresando contraseña...")
            pass_input.wait_for(state="visible", timeout=15000)
            pass_input.fill(password)

            submit_btn = oneid_frame.locator("button[type='submit'], button:has-text('Sign In'), button:has-text('Iniciar sesión')").first
            submit_btn.click()
            logger.info("📤 Formulario de login enviado.")

            # Esperar a que OneID procese el login
            logger.info("⏳ Esperando respuesta de sesión autenticada...")
            time.sleep(6)

            # Consultar window.did.getGuest()
            token = page.evaluate("""
                () => new Promise((resolve) => {
                    let attempts = 0;
                    const check = () => {
                        attempts++;
                        if (window.did && window.did.getGuest) {
                            window.did.getGuest().then(g => {
                                if (g && g.token && g.token.access_token) {
                                    resolve(g.token.access_token);
                                } else if (attempts < 15) {
                                    setTimeout(check, 1000);
                                } else {
                                    resolve(null);
                                }
                            }).catch(() => {
                                if (attempts < 15) setTimeout(check, 1000);
                                else resolve(null);
                            });
                        } else {
                            if (attempts < 15) setTimeout(check, 1000);
                            else resolve(null);
                        }
                    };
                    check();
                })
            """)

            if not token:
                page.screenshot(path="error_login.png")
                # Intentar leer si hay algún mensaje de error dentro del iframe
                try:
                    err_texts = oneid_frame.locator(".error, [role='alert'], .notification").all_text_contents()
                    if err_texts:
                        logger.error(f"Mensaje de error en OneID: {err_texts}")
                except Exception:
                    pass
                raise RuntimeError("El navegador no pudo extraer el token tras el login.")

            logger.info(f"🎉 ¡TOKEN DE DISNEY OBTENIDO EXITOSAMENTE! (...{token[-6:]})")
            browser.close()
            return token

        except Exception as e:
            try:
                page.screenshot(path="error_login.png")
            except Exception:
                pass
            browser.close()
            raise e


if __name__ == "__main__":
    email = os.getenv("DISNEY_EMAIL")
    password = os.getenv("DISNEY_PASSWORD")
    if not email or not password:
        logger.error("Faltan las variables DISNEY_EMAIL o DISNEY_PASSWORD.")
        sys.exit(1)

    try:
        token = get_token_via_browser(email, password)
        print(f"::set-output name=token::{token}")
        success = test_token_availability(token)
        if not success:
            sys.exit(1)
    except Exception as e:
        logger.error(f"Fallo en get_token_via_browser: {e}")
        sys.exit(1)
