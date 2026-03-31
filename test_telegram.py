"""
Prueba la configuración de Telegram directamente.
Ejecutar: python test_telegram.py
"""
import os
import json
import urllib.request
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

print(f"TOKEN   : '{TOKEN}'")
print(f"CHAT_ID : '{CHAT_ID}'")
print()

if not TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN no está definido en .env")
    exit(1)

if not CHAT_ID:
    print("ERROR: TELEGRAM_CHAT_ID no está definido en .env")
    exit(1)

# Paso 1 — Verificar que el token es válido
print("1. Verificando token...")
try:
    url = f"https://api.telegram.org/bot{TOKEN}/getMe"
    with urllib.request.urlopen(url, timeout=5) as r:
        data = json.loads(r.read())
    if data.get('ok'):
        bot = data['result']
        print(f"   OK — Bot: @{bot['username']} ({bot['first_name']})")
    else:
        print(f"   ERROR: {data}")
        exit(1)
except Exception as e:
    print(f"   ERROR: {e}")
    exit(1)

# Paso 2 — Enviar mensaje de prueba
print("2. Enviando mensaje de prueba...")
try:
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({
        'chat_id':  CHAT_ID,
        'text':     '✅ Prueba exitosa desde MT5 Trading Bot',
        'parse_mode': 'HTML'
    }).encode('utf-8')

    req = urllib.request.Request(
        url, data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read())

    if data.get('ok'):
        print("   OK — Mensaje enviado. Revisa tu Telegram.")
    else:
        print(f"   ERROR Telegram: {data}")
        # Diagnóstico del error
        desc = data.get('description', '')
        if 'chat not found' in desc.lower():
            print()
            print("   CAUSA: El chat_id es incorrecto.")
            print("   SOLUCIÓN: Abre Telegram, escribe /start a tu bot,")
            print(f"   luego abre: https://api.telegram.org/bot{TOKEN}/getUpdates")
            print("   y copia el número en result[0].message.from.id")
        elif 'bot was blocked' in desc.lower():
            print()
            print("   CAUSA: Bloqueaste el bot. Abre el chat del bot y presiona START.")
except Exception as e:
    print(f"   ERROR: {e}")
