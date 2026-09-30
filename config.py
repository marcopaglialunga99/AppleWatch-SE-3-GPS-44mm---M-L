"""Configurazione del price tracker Apple Watch SE 3 — v7."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_TIMEOUT = 15
TELEGRAM_RETRY_BASE_SECONDS = 30
TELEGRAM_RETRY_MAX_SECONDS = 3600
TELEGRAM_MAX_BATCH = 20

# ---------------------------------------------------------------------------
# Frequenza
# ---------------------------------------------------------------------------

# Controllo normale: ogni 30 minuti.
CHECK_INTERVAL_MINUTES = 30

# Se viene rilevato un calo importante, il bot può controllare più spesso
# per un periodo limitato, così riduciamo il rischio di perdere un'offerta breve.
FAST_CHECK_ENABLED = True
FAST_CHECK_INTERVAL_MINUTES = 10
FAST_CHECK_DURATION_MINUTES = 60
FAST_CHECK_TRIGGER_DROP_EURO = 15.00
FAST_CHECK_TRIGGER_PRICE_EURO = 0.00  # 0 = disabilitato; altrimenti attiva il fast mode sotto questa soglia.

DAILY_RECAP_TIME = "07:00"
SCHEDULE_POLL_SECONDS = 10
SEND_MISSED_DAILY_RECAP_ON_START = True

# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

USE_PLAYWRIGHT_FALLBACK = True
REQUEST_TIMEOUT = 20
BROWSER_TIMEOUT_MS = 35_000
BROWSER_SETTLE_MS = 5_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

# Limiti anti-falso-positivo. Il bot non accetta prezzi fuori da questo range.
MIN_VALID_PRICE = 150
MAX_VALID_PRICE = 500

# ---------------------------------------------------------------------------
# Validazione prodotto
# ---------------------------------------------------------------------------

PRODUCT_NAME = "Apple Watch SE 3 GPS 44 mm M/L — cassa Mezzanotte — cinturino non bianco"

# Questi termini, quando compaiono nel NOME dell'offerta candidata, fanno
# scartare l'offerta. NON vengono cercati nell'intera pagina, perché molte
# pagine mostrano anche alternative come "GPS + Cellular".
FORBIDDEN_PRODUCT_TERMS = (
    "cellular",
    "gps + cellular",
    "usato",
    "used",
    "ricondizionato",
    "refurbished",
    "renewed",
    "refurb",
)

FORBIDDEN_BAND_TERMS = (
    "bianco",
    "bianca",
    "white",
)

# La cassa deve essere sempre Mezzanotte. Galassia viene quindi esclusa.
REQUIRED_CASE_TERMS = (
    "mezzanotte",
    "midnight",
)

# ---------------------------------------------------------------------------
# Notifiche / storico
# ---------------------------------------------------------------------------

ALERT_ON_PRICE_CHANGE = True
MIN_ALERT_CHANGE_EURO = 0.01
ALERT_NEW_HISTORICAL_MIN = True
# Limite per dimensione file: non equivale a uno storico illimitato.
MAX_HISTORY_PER_LISTING = 25_000

# Notifica anche il passaggio disponibile <-> non disponibile quando viene
# riconosciuto in modo sufficientemente affidabile.
ALERT_AVAILABILITY_CHANGE = True

USE_SHORT_LINKS = False
SHORT_URL_TIMEOUT = 8

# ---------------------------------------------------------------------------
# Pagine da monitorare
# ---------------------------------------------------------------------------
# Ogni voce è una "listing" indipendente. Questo ci permette di monitorare
# più SKU/pagine dello stesso ecommerce senza perdere uno sconto su una
# singola variante di colore.
#
# Criterio:
#   - Apple Watch SE 3
#   - GPS (NON GPS + Cellular)
#   - cassa 44 mm Mezzanotte
#   - cinturino M/L
#   - cinturino NON bianco
#   - nuovo, non usato/ricondizionato
#
# Per i marketplace il venditore può cambiare: il tracker lo registra.

SITES = {
        "apple_mezzanotte_mezzanotte_ml": {
        "enabled": True,
        "store": "Apple",
        "variant": "Mezzanotte + Sport mezzanotte M/L",
        "url": "https://www.apple.com/it/shop/buy-watch/apple-watch-se/44mm-gps-mezzanotte-alluminio-mezzanotte-cinturino-sport-m-l-se",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": True,
        "trusted_variant": True,
        "price_parser": "apple_configurator",
    },
    # Euronics: due pagine/SKU distinti.
    "euronics_mezzanotte_banda_mezzanotte_ml": {
        "enabled": True,
        "store": "Euronics",
        "variant": "Mezzanotte + Sport mezzanotte M/L",
        "url": "https://www.euronics.it/telefonia/wearable/smartwatch/apple---watch-se-3-gps-44mm-alluminio-sport-band-mezzanotte---m-l/252011591.html",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },
    "euronics_mezzanotte_blu_marino_ml": {
        "enabled": True,
        "store": "Euronics",
        "variant": "Mezzanotte + Sport blu marino M/L",
        "url": "https://www.euronics.it/telefonia/wearable/smartwatch/apple---watch-se-3-gps-44mm-alluminio-mezzanotte-sport-band-blu-marino---m-l/262010916.html",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    "unieuro_mezzanotte_mezzanotte_ml": {
        "enabled": True,
        "store": "Unieuro",
        "variant": "Mezzanotte + Sport mezzanotte M/L",
        "url": "https://www.unieuro.it/online/Smartwatch/Watch-SE-3-GPS-44mm-Cassa-Alluminio-Mezzanotte-con-Sport-Band-Mezzanotte---M-L-pidAPLSE3GPS44MAMSBML",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    "expert_mezzanotte_blu_marino_ml": {
        "enabled": True,
        "store": "Expert",
        "variant": "Mezzanotte + Sport blu marino M/L",
        "url": "https://www.expert.it/it/it/exp/shop/product/watch-se-3-gps-cassa-44-mm-in-alluminio-mezzanotte-con-cinturino-sport-blu-mari/exp933162",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    # MediaWorld: pagina prodotto diretta, nuova e non Cellular.
    "mediaworld_mezzanotte_blu_marino_ml": {
        "enabled": True,
        "store": "MediaWorld",
        "variant": "Mezzanotte + Sport blu marino M/L",
        "url": "https://www.mediaworld.it/it/product/_apple-apple-wtcse3gps44-alluminio-594759.html",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    "trony_mezzanotte_blu_marino_ml": {
        "enabled": True,
        "store": "Trony",
        "variant": "Mezzanotte + Sport blu marino M/L",
        "url": "https://www.trony.it/telefonia/apple-apple-watch-se-3-gps-cassa-44-mm-in-alluminio-mezzanotte-con-cinturino-sport-blu-marino-m-l-2260010335/",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    # Comet: pagina prodotto diretta.
    "comet_mezzanotte_blu_marino_ml": {
        "enabled": True,
        "store": "Comet",
        "variant": "Mezzanotte + Sport blu marino M/L",
        "url": "https://www.comet.it/apple-watch-se-3-gps-44mm-m-l-cassa-alluminio-mezzanotte-e-sport-band-blu-marino-APL049605-prdtt",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    # R-Store: rivenditore Apple/autorizzato; teniamo anche pagine che possono
    # essere esaurite oggi, perché il ritorno a stock è un'informazione utile.
    "rstore_mezzanotte_mezzanotte_ml": {
        "enabled": True,
        "store": "R-Store",
        "variant": "Mezzanotte + Sport mezzanotte M/L",
        "url": "https://www.rstore.it/products/2026-apple-watch-se-cinturino-sport-3-generazione-mjl94ql-a",
        "marketplace": False,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },

    # ePRICE è marketplace. In locale può restituire HTTP 403/anti-bot;
    # lo teniamo come link manuale anziché generare falsi errori ogni 30 minuti.
    "eprice_mezzanotte_mezzanotte_ml": {
        "enabled": False,
        "store": "ePRICE",
        "variant": "Mezzanotte + Sport mezzanotte M/L",
        "url": "https://www.eprice.it/Apple-Watch-SE-3nd-generation-SE-3-GPS-44mm-Cassa-Allu-MEHQ4ZR-A/d-70001505",
        "marketplace": True,
        "configured_size_ml": True,
        "configurable_page": False,
        "trusted_variant": True,
    },
}

# ---------------------------------------------------------------------------
# Amazon: non facciamo scraping; Keepa è la scelta più adatta.
# ---------------------------------------------------------------------------

EXTRA_LINKS = {
    "Apple (manuale — configuratore troppo fragile per lo scraping)": "https://www.apple.com/it/shop/buy-watch/apple-watch-se/44mm-gps-mezzanotte-alluminio-mezzanotte-cinturino-sport-m-l-se",
    "ePRICE (manuale — locale può dare 403)": "https://www.eprice.it/Apple-Watch-SE-3nd-generation-SE-3-GPS-44mm-Cassa-Allu-MEHQ4ZR-A/d-70001505",
    # Amazon: un solo listing/ASIN da seguire con Keepa.
    # Il link breve amzn.eu fornito dall'utente rimanda al medesimo ASIN B0FQFPJR2V.
    "Amazon (Keepa) — ASIN B0FQFPJR2V — mezzanotte M/L": "https://amzn.eu/d/0hJcJ5pH",
}
DATA_FILE = "prezzi.json"
