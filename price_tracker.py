"""Price tracker robusto v7 per Apple Watch SE 3 GPS 44 mm M/L.

- Controllo immediato all'avvio + controllo periodico.
- 30 minuti normalmente; modalità intensiva temporanea dopo un forte calo.
- Storico completo per ogni pagina/SKU.
- Più pagine dello stesso ecommerce sono supportate.
- Filtra Cellular, usato/ricondizionato, cassa Galassia e cinturino bianco.
- Estrae il prezzo da JSON-LD/meta/itemprop/HTML visibile.
- Fallback a Chromium/Playwright quando requests non basta.
- Tiene traccia della disponibilità e dei venditori marketplace.
- Telegram per variazioni, minimi storici, disponibilità ed errori.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import shutil
import uuid
import re
import time
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import config

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    PlaywrightTimeoutError = Exception
    sync_playwright = None

BASE_DIR = Path(__file__).resolve().parent

HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

Decimal("0.01")


def now() -> datetime:
    return datetime.now().astimezone()


def iso_now() -> str:
    return now().isoformat(timespec="seconds")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", value).strip()


def money_to_decimal(raw: Any, *, enforce_range: bool = True) -> Decimal | None:
    """Parsa valori come 309,00 / 1.299,90 / 309,– / 309.00."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    text = (
        text.replace("€", "")
        .replace("EUR", "")
        .replace("eur", "")
        .replace("\xa0", " ")
        .strip()
    )
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)

    if re.fullmatch(r"\d+(?:[.,]\s*-{1,2})?", text):
        # 309,- / 309,-- -> 309.00
        if "," in text or "." in text:
            if re.search(r"[.,]\s*-{1,2}$", text):
                text = re.split(r"[.,]", text, maxsplit=1)[0]

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", ".", "-", "-."}:
        return None

    try:
        value = Decimal(text)
    except InvalidOperation:
        return None

    value = value.quantize(Decimal("0.01"))
    if enforce_range:
        if value < Decimal(str(config.MIN_VALID_PRICE)):
            return None
        if value > Decimal(str(config.MAX_VALID_PRICE)):
            return None
    return value


def format_price(value: Decimal | float | int) -> str:
    return f"{Decimal(str(value)):.2f}".replace(".", ",") + " €"


def format_diff(value: Decimal | float | int) -> str:
    dec = Decimal(str(value))
    sign = "+" if dec > 0 else ""
    return f"{sign}{dec:.2f}".replace(".", ",") + " €"



def safe_json_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise RuntimeError(f"{path} non contiene un oggetto JSON; non lo sovrascrivo.")
        return data
    except (OSError, json.JSONDecodeError) as exc:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if backup_path.exists():
            try:
                with backup_path.open("r", encoding="utf-8") as handle:
                    backup = json.load(handle)
                if isinstance(backup, dict):
                    print(f"[ATTENZIONE] {path} non leggibile; uso il backup {backup_path}.")
                    return backup
            except (OSError, json.JSONDecodeError):
                pass
        raise RuntimeError(
            f"Impossibile leggere {path}; non sovrascrivo lo storico. "
            f"Controllare il file o il backup {backup_path}."
        ) from exc


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    backup_path = path.with_suffix(path.suffix + ".bak")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            # Non sostituire un backup valido con il file che stiamo recuperando
            # proprio perché è corrotto o non leggibile.
            try:
                with path.open("r", encoding="utf-8") as current:
                    current_data = json.load(current)
                if isinstance(current_data, dict):
                    backup_temp = backup_path.with_name(f".{backup_path.name}.{os.getpid()}.tmp")
                    shutil.copy2(path, backup_temp)
                    os.replace(backup_temp, backup_path)
            except (OSError, json.JSONDecodeError):
                pass
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass

def data_file() -> Path:
    path = Path(config.DATA_FILE)
    return path if path.is_absolute() else BASE_DIR / path



def empty_data() -> dict[str, Any]:
    return {
        "version": 4,
        "last_daily_recap": None,
        "pending_notifications": [],
        "telegram_retry_at": None,
        "sites": {},
    }


def migrate_state(old_state: dict[str, Any]) -> dict[str, Any]:
    """Migra i formati storici senza perdere lo stato già associato alle URL."""
    data = empty_data()
    data["last_daily_recap"] = old_state.get("last_daily_recap")
    pending = old_state.get("pending_notifications", [])
    if isinstance(pending, list):
        data["pending_notifications"] = pending
    data["telegram_retry_at"] = old_state.get("telegram_retry_at")

    if isinstance(old_state.get("sites"), dict):
        old_sites = old_state["sites"]
        url_to_old: dict[str, dict[str, Any]] = {}
        for state in old_sites.values():
            if not isinstance(state, dict):
                continue
            last = state.get("last")
            if isinstance(last, dict) and last.get("url"):
                url_to_old[str(last["url"])] = state
        for listing_id, listing in config.SITES.items():
            old = url_to_old.get(listing.get("url", ""))
            if old:
                old = dict(old)
                if listing_id == "apple_mezzanotte_agave_ml" and isinstance(old.get("history"), list):
                    # Rimuove il falso 279 EUR registrato dal parser generico:
                    # la pagina Apple mostrava il prezzo di partenza di un'altra misura.
                    old["history"] = [
                        item for item in old["history"]
                        if not (
                            isinstance(item, dict)
                            and item.get("source") == "requests"
                            and item.get("availability") == "unavailable"
                            and item.get("price") == 279.0
                        )
                    ]
                data["sites"][listing_id] = old
        return data

    # Formato v1: una voce per negozio. Per negozi con più listing non
    # attribuiamo lo stesso prezzo a più SKU, perché sarebbe un falso storico.
    if old_state:
        for listing_id, listing in config.SITES.items():
            legacy = old_state.get(listing_id)
            if not isinstance(legacy, dict):
                store_matches = [
                    item for item in config.SITES.values()
                    if item.get("store") == listing.get("store")
                ]
                if len(store_matches) != 1:
                    continue
                legacy = old_state.get(listing.get("store"))
            if not isinstance(legacy, dict) or legacy.get("price") is None:
                continue
            price = money_to_decimal(legacy.get("price"), enforce_range=False)
            if price is None:
                continue
            checked_at = legacy.get("checked_at") or iso_now()
            data["sites"][listing_id] = {
                "last": {
                    "price": float(price),
                    "url": listing.get("url", legacy.get("url", "")),
                    "checked_at": checked_at,
                    "seller": legacy.get("seller"),
                    "product_name": legacy.get("product_name"),
                    "availability": legacy.get("availability", "unknown"),
                    "source": "migrated",
                },
                "history": [{
                    "price": float(price),
                    "checked_at": checked_at,
                    "seller": legacy.get("seller"),
                    "availability": legacy.get("availability", "unknown"),
                    "source": "migrated",
                }],
                "consecutive_failures": 0,
                "last_error": None,
                "last_success_at": checked_at,
            }
    return data


def load_data() -> dict[str, Any]:
    raw = safe_json_load(data_file())
    if raw.get("version") == 4 and isinstance(raw.get("sites"), dict):
        raw.setdefault("last_daily_recap", None)
        raw.setdefault("pending_notifications", [])
        raw.setdefault("telegram_retry_at", None)
        return raw
    migrated = migrate_state(raw)
    if raw:
        print("[INFO] Migrazione automatica dello storico completata.")
    return migrated

def site_state(data: dict[str, Any], listing_id: str) -> dict[str, Any]:
    sites = data.setdefault("sites", {})
    state = sites.setdefault(listing_id, {})
    state.setdefault("last", None)
    state.setdefault("history", [])
    state.setdefault("consecutive_failures", 0)
    state.setdefault("last_error", None)
    state.setdefault("last_success_at", None)
    return state


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_with_requests(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=config.REQUEST_TIMEOUT)
    response.raise_for_status()
    if len(response.text or "") < 1000:
        raise RuntimeError("risposta HTML troppo corta")
    return response.text

def fetch_with_playwright(url: str) -> str:
    if sync_playwright is None:
        raise RuntimeError("Playwright non installato")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=config.USER_AGENT,
            locale="it-IT",
            extra_http_headers={
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8"
            },
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=config.BROWSER_TIMEOUT_MS,
            )

            if "apple.com" in url.lower():
                selettori = (
                    'input[name="watch_cases-dimensionColor"]'
                    '[value="midnight"]',
                    'input[name="watch_cases-dimensionCaseSize"]'
                    '[value="44mm"]',
                    'input[name="watch_cases-dimensionConnection"]'
                    '[value="gps"]',
                )

                try:
                    for selettore in selettori:
                        radio = page.locator(selettore)
                        radio.wait_for(
                            state="attached",
                            timeout=5_000,
                        )

                        id_input = radio.get_attribute("id")

                        if id_input:
                            etichetta = page.locator(
                                f'label[for="{id_input}"]'
                            )

                            if etichetta.count() > 0:
                                etichetta.evaluate(
                                    "(elemento) => elemento.click()"
                                )
                            else:
                                radio.evaluate(
                                    "(elemento) => elemento.click()"
                                )
                        else:
                            radio.evaluate(
                                "(elemento) => elemento.click()"
                            )

                        page.wait_for_function(
                            """selector => {
                                const input =
                                    document.querySelector(selector);
                                return input && input.checked;
                            }""",
                            arg=selettore,
                            timeout=5_000,
                        )

                    page.wait_for_timeout(1_500)

                    page.locator(
                        '[data-autom="priceBandcategoryrubber"] '
                        ".price-point-fullPrice-short"
                    ).wait_for(
                        state="attached",
                        timeout=15_000,
                    )
                    print(
                        "[DEBUG Apple] prezzo della categoria Gomma "
                        "presente nel DOM"
                    )

                except Exception as exc:
                    print(
                        f"[DEBUG Apple] errore selezione/attesa: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    print(
                        f"[DEBUG Apple Playwright] "
                        f"url finale={page.url!r}; titolo={page.title()!r}"
                    )

                    try:
                        testo = page.locator("body").inner_text(
                            timeout=3_000
                        )
                        print(
                            "[DEBUG Apple] testo configuratore="
                            f"{testo[1500:4000]!r}"
                        )

                        stati = page.locator(
                            'input[name="watch_cases-dimensionColor"], '
                            'input[name="watch_cases-dimensionCaseSize"], '
                            'input[name="watch_cases-dimensionConnection"], '
                            'input[name="category"]'
                        ).evaluate_all(
                            """els => els.map(el => ({
                                nome: el.name,
                                valore: el.value,
                                selezionato: el.checked
                            }))"""
                        )
                        print(f"[DEBUG Apple] stati opzioni={stati!r}")

                    except Exception as debug_exc:
                        print(
                            f"[DEBUG Apple] lettura pagina fallita: "
                            f"{type(debug_exc).__name__}: {debug_exc}"
                        )

                    raise RuntimeError(
                        "configurazione o prezzo Apple non disponibili"
                    ) from exc

            else:
                page.wait_for_timeout(config.BROWSER_SETTLE_MS)

            html = page.content()
            if len(html or "") < 1000:
                raise RuntimeError("pagina browser troppo corta")

            return html

        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"timeout browser: {exc}") from exc

        finally:
            try:
                context.close()
            finally:
                browser.close()

def fetch_page(url: str) -> tuple[str, str]:
    try:
        return fetch_with_requests(url), "requests"
    except Exception as req_error:
        if not config.USE_PLAYWRIGHT_FALLBACK:
            raise RuntimeError(f"requests fallito: {req_error}") from req_error
        try:
            return fetch_with_playwright(url), "playwright"
        except Exception as browser_error:
            raise RuntimeError(
                f"requests: {req_error}; browser: {browser_error}"
            ) from browser_error


# ---------------------------------------------------------------------------
# JSON-LD / identità prodotto
# ---------------------------------------------------------------------------


def jsonld_objects(soup: BeautifulSoup) -> list[Any]:
    result: list[Any] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            result.append(json.loads(raw))
        except (TypeError, json.JSONDecodeError):
            continue
    return result


def walk_json(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def candidate_product_names(soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []

    if soup.title:
        candidates.append(soup.title.get_text(" ", strip=True))

    for tag in soup.find_all("meta", attrs={"property": "og:title"}):
        if tag.get("content"):
            candidates.append(tag["content"])

    for tag in soup.find_all("h1"):
        text = tag.get_text(" ", strip=True)
        if text:
            candidates.append(text)

    for root in jsonld_objects(soup):
        for obj in walk_json(root):
            if not isinstance(obj, dict):
                continue
            name = obj.get("name")
            if isinstance(name, str) and name.strip():
                candidates.append(name.strip())

    # Dedup mantenendo l'ordine.
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        norm = normalize_text(candidate)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(candidate)
    return out


def has_44mm(text: str) -> bool:
    return bool(re.search(r"\b44\s*mm\b", text))


def has_ml(text: str) -> bool:
    normalized = normalize_text(text)
    return bool(
        re.search(r"\bm\s*/\s*l\b", normalized)
        or re.search(r"\bm\s*-\s*l\b", normalized)
        or re.search(r"\bm\s+l\b", normalized)
        or re.search(r"\bm/l\b", normalized)
        or re.search(r"taglia(?:\s+cinturino)?\s*[:\-]?\s*m\s*/\s*l", normalized)
    )


def is_forbidden_name(text: str) -> bool:
    norm = normalize_text(text)
    return any(term in norm for term in config.FORBIDDEN_PRODUCT_TERMS) or any(
        term in norm for term in config.FORBIDDEN_BAND_TERMS
    )



def product_name_matches(name: str, listing: dict[str, Any]) -> bool:
    text = normalize_text(name)
    if not re.search(r"\bapple\s*[-:]*\s*watch\b", text):
        return False
    if not re.search(r"\bse\s*3\b|\bse3\b|3rd|3a generazione|3ª", text):
        return False
    if not has_44mm(text) or "gps" not in text:
        return False
    if re.search(r"\bgps\s*\+\s*cellular\b|\bcellular\b", text):
        return False
    if not has_ml(text) or is_forbidden_name(text):
        return False
    required_cases = listing.get("required_case_terms", config.REQUIRED_CASE_TERMS)
    return not required_cases or any(normalize_text(term) in text for term in required_cases)

def page_is_plausible_for_config(
    soup: BeautifulSoup, listing: dict[str, Any]
) -> tuple[bool, str | None]:
    """Verifica il prodotto senza pretendere che ogni attributo compaia nello stesso titolo.

    Molti ecommerce mostrano varianti/alternative nella stessa pagina e Apple usa
    un configuratore che può ridirigere alla pagina generale. Per le URL curate
    da noi possiamo quindi usare una validazione a livelli:
      1) match stretto su titolo/h1/JSON-LD quando disponibile;
      2) match distribuito tra URL + testo della pagina;
      3) per pagine configurabili, identità minima + attributi attesi della listing.

    In nessun caso consideriamo la parola "Cellular" o "bianco" presente in una
    sezione di alternative come prova che il prodotto selezionato sia Cellular/bianco.
    """
    names = candidate_product_names(soup)
    matching = [n for n in names if product_name_matches(n, listing)]
    if matching:
        return True, max(matching, key=len)

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    og_titles = [
        tag.get("content", "")
        for tag in soup.find_all("meta", attrs={"property": "og:title"})
        if tag.get("content")
    ]
    primary = [title, *h1s, *og_titles]

    # Testo effettivo della pagina e URL: usati come segnali separati.
    body = normalize_text(soup.get_text(" ", strip=True)[:80000])
    url_text = normalize_text(str(listing.get("url", "")))
    combined = f"{url_text} {body}"

    # Identità minima del prodotto.
    has_identity = (
        "apple watch" in combined
        and bool(re.search(r"\bse\s*3\b|\bse3\b|3rd(?:\s+generation|\s+generation)?|3a generazione|3ª", combined))
        and has_44mm(combined)
        and "gps" in combined
    )
    if not has_identity:
        return False, None

    # Il GPS + Cellular può comparire in una pagina configurabile accanto al GPS.
    # Lo scartiamo solo quando la listing è effettivamente identificata come Cellular
    # o quando un nome candidato completo contiene Cellular.
    if any(product_name_matches(n, listing) for n in names):
        return True, max(names, key=len) if names else None

    # Le nostre URL sono pre-selezionate manualmente: qui verifichiamo che la
    # pagina mostri almeno il colore della cassa richiesto e M/L da qualche parte,
    # oppure che sia una pagina configurabile esplicitamente marcata come tale.
    required_cases = listing.get("required_case_terms", config.REQUIRED_CASE_TERMS)
    case_ok = any(normalize_text(term) in combined for term in required_cases)

    # La taglia può essere scritta come M /L, M/L, M-L o essere disponibile solo
    # nel configuratore. Per le listing curate possiamo dichiarare che l'URL/variante
    # è già M/L, evitando falsi negativi su pagine configurabili.
    ml_ok = has_ml(combined) or bool(listing.get("configured_size_ml", False))

    # Verifica esplicita della cassa: Galassia viene rifiutata quando è l'unica
    # variante identificata. Una semplice presenza di "galassia" in una pagina
    # con selettore non basta a scartare la listing Mezzanotte.
    galaxy_only = not case_ok and any(
        term in combined for term in ("galassia", "starlight")
    )

    if case_ok and ml_ok and not galaxy_only:
        display = max(
            [c for c in primary if c.strip()],
            key=len,
            default=None,
        )
        return True, display or listing.get("variant", "")

    # Apple: configuratore ufficiale. La URL/variant indica già cassa Mezzanotte,
    # GPS, 44 mm e M/L; la pagina generale può non ripeterli nello stesso titolo.
    if listing.get("configurable_page") and listing.get("trusted_variant"):
        if (
            any(term in combined for term in required_cases)
            and "apple watch" in combined
            and re.search(r"\bse\s*3\b|\bse3\b", combined)
            and has_44mm(combined)
            and "gps" in combined
        ):
            display = next((c for c in primary if c.strip()), listing.get("variant", ""))
            return True, display

    # Ultimo fallback per URL/SKU selezionate manualmente e curate in config.py.
    # Alcuni ecommerce restituiscono localmente una shell anti-bot o una pagina
    # diversa da quella visibile a un crawler pubblico. In quel caso il nome
    # corretto può non essere presente nel DOM, pur essendo la URL stessa la
    # pagina/SKU esatta che abbiamo verificato manualmente. Usiamo quindi la URL
    # come identità solo quando la listing è esplicitamente marcata trusted_variant.
    # Non usiamo mai questo fallback per una URL generica/non curata.
    if listing.get("trusted_variant"):
        url_norm = normalize_text(str(listing.get("url", "")))
        url_identity = (
            "apple-watch" in url_norm
            and ("se-3" in url_norm or "se3" in url_norm)
            and "gps" in url_norm
        )
        url_size = "44mm" in url_norm or "44-mm" in url_norm or "44 mm" in url_norm
        url_case = any(normalize_text(term) in url_norm for term in required_cases)

        # Per gli SKU statici, il nome della variante in config.py è parte della
        # configurazione curata; per Apple configuratore usiamo anche il flag
        # configurable_page. Il cinturino viene validato sulla configurazione,
        # non cercando "bianco" nell'intera pagina.
        if url_identity and (url_size or listing.get("configured_size_ml")) and (
            url_case or listing.get("configurable_page") or case_ok
        ):
            display = next((c for c in primary if c.strip()), listing.get("variant", ""))
            return True, display

    return False, None


# ---------------------------------------------------------------------------
# Seller / disponibilità
# ---------------------------------------------------------------------------


def extract_seller(soup: BeautifulSoup) -> str | None:
    text = soup.get_text(" ", strip=True)
    patterns = (
        r"venduto e spedito da\s+(.+?)(?:\s+altri\s+venditori|\s+eccellente|\s+ottimo|\s+buono|$)",
        r"venduto da\s+(.+?)(?:\s+spedito da|\s+altri\s+venditori|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            seller = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
            if seller:
                return seller[:120]
    return None



def extract_availability(soup: BeautifulSoup, listing: dict[str, Any]) -> str:
    """Legge la disponibilità solo da segnali collegabili alla listing."""
    def status(value: Any) -> str | None:
        norm = normalize_text(str(value or ""))
        if "instock" in norm or "in stock" in norm:
            return "available"
        if "outofstock" in norm or "out of stock" in norm or "soldout" in norm:
            return "unavailable"
        return None

    for root in jsonld_objects(soup):
        for obj in walk_json(root):
            if not isinstance(obj, dict):
                continue
            types = obj.get("@type", [])
            if isinstance(types, str):
                types = [types]
            type_names = {
                normalize_text(str(value).rsplit("/", 1)[-1].rsplit("#", 1)[-1])
                for value in types
            } if isinstance(types, list) else set()
            if "product" not in type_names:
                continue
            name = obj.get("name")
            if not isinstance(name, str) or not product_name_matches(name, listing):
                continue
            direct = status(obj.get("availability"))
            if direct:
                return direct
            offers = obj.get("offers")
            offer_list = offers if isinstance(offers, list) else [offers]
            offer_statuses = {
                result for offer in offer_list if isinstance(offer, dict)
                for result in [status(offer.get("availability"))] if result
            }
            if len(offer_statuses) == 1:
                return next(iter(offer_statuses))
            if len(offer_statuses) > 1:
                return "unknown"

    scopes = []
    for heading in soup.find_all("h1")[:3]:
        current = heading
        for _ in range(4):
            if current is None:
                break
            text = current.get_text(" ", strip=True)
            if 20 <= len(text) <= 2500:
                scopes.append(text)
            current = current.parent
    results = set()
    for text in scopes:
        norm = normalize_text(text)
        has_unavailable = any(term in norm for term in (
            "non disponibile", "esaurito", "out of stock", "sold out"
        ))
        has_available = any(term in norm for term in (
            "aggiungi al carrello", "aggiungi alla borsa", "acquista ora",
            "disponibile online", "spedizione disponibile"
        ))
        if has_available and has_unavailable:
            return "unknown"
        if has_available:
            results.add("available")
        if has_unavailable:
            results.add("unavailable")
    return next(iter(results)) if len(results) == 1 else "unknown"


def _apple_price_contexts(
    soup: BeautifulSoup, listing: dict[str, Any]
) -> list[tuple[Decimal, int, str]]:
    """Estrae prezzi Apple solo da contesti locali della variante richiesta."""
    contexts: list[tuple[Decimal, int, str]] = []
    seen: set[tuple[int, str]] = set()
    targets = []
    for tag in soup.find_all(True):
        text = tag.get_text(" ", strip=True)
        norm = normalize_text(text)
        if "44 mm" in norm and 5 <= len(text) <= 1800:
            targets.append(tag)

    required_terms = [normalize_text(x) for x in listing.get("price_context_terms", [])]
    for tag in targets:
        scopes = [tag]
        parent = tag.parent
        depth = 0
        while parent is not None and depth < 3:
            scopes.append(parent)
            parent = parent.parent
            depth += 1
        for sibling in list(tag.previous_siblings)[-1:] + list(tag.next_siblings)[:1]:
            if getattr(sibling, "get_text", None):
                scopes.append(sibling)

        for scope in scopes:
            text = scope.get_text(" ", strip=True)
            if not text or len(text) > 2200:
                continue
            norm = normalize_text(text)
            if not re.search(r"\b44\s*mm\b", norm):
                continue
            if "gps" not in norm or re.search(r"\bgps\s*\+\s*cellular\b|\bcellular\b", norm):
                continue
            if not any(term in norm for term in ("mezzanotte", "midnight")):
                continue
            if required_terms and not all(term in norm for term in required_terms):
                continue
            for price, base_score in extract_text_prices(text):
                score = base_score + 30
                if "cinturino sport" in norm or "sport band" in norm:
                    score += 4
                if has_ml(norm):
                    score += 2
                if any(word in norm for word in ("mese", "mensile", "rata", "rate")):
                    score -= 30
                key = (id(scope), str(price))
                if key not in seen:
                    seen.add(key)
                    contexts.append((price, score, text))
    return contexts


def extract_apple_configurator_price(
    soup: BeautifulSoup, listing: dict[str, Any]
) -> Decimal | None:
    summary = soup.select_one('[data-autom="summaryHeroPrice"]')
    if summary:
        price = money_to_decimal(summary.get_text(" ", strip=True))
        if price is not None:
            return price

    rubber_price = soup.select_one(
        '[data-autom="priceBandcategoryrubber"] '
        ".price-point-fullPrice-short"
    )
    if rubber_price:
        price = money_to_decimal(rubber_price.get_text(" ", strip=True))
        if price is not None:
            return price

    contexts = _apple_price_contexts(soup, listing)
    if contexts:
        contexts.sort(key=lambda item: (-item[1], len(item[2])))
        return contexts[0][0]

    return None

def extract_apple_availability(soup: BeautifulSoup) -> str:
    """Disponibilità conservativa per il configuratore Apple.

    La pagina generale contiene anche varianti non acquistabili; non bisogna
    quindi trasformare un "non disponibile" relativo a un'altra variante in un
    dato della nostra 44 mm GPS. Senza un segnale chiaramente associato alla
    variante selezionata, restituiamo unknown.
    """
    # Segnali molto specifici, cercati in un contesto locale che contenga 44 mm
    # e GPS ma NON Cellular. Se non li troviamo, meglio "unknown" che un falso.
    for tag in soup.find_all(True):
        text = tag.get_text(" ", strip=True)
        norm = normalize_text(text)
        if "44 mm" not in norm or "gps" not in norm:
            continue
        if "gps + cellular" in norm or "cellular" in norm:
            continue
        if len(norm) > 3500:
            continue
        if any(term in norm for term in ("aggiungi alla borsa", "aggiungi al carrello", "acquista ora", "disponibile online")):
            return "available"
        if any(term in norm for term in ("esaurito", "non disponibile", "sold out", "out of stock")):
            return "unavailable"
    return "unknown"


# ---------------------------------------------------------------------------
# Prezzo
# ---------------------------------------------------------------------------



def offer_prices(offer: Any) -> list[Decimal]:
    """Accetta un prezzo concreto, non il limite inferiore di un range."""
    if not isinstance(offer, dict):
        return []
    price = money_to_decimal(offer.get("price"))
    return [price] if price is not None else []


def extract_jsonld_price(
    soup: BeautifulSoup, listing: dict[str, Any]
) -> Decimal | None:
    for root in jsonld_objects(soup):
        for obj in walk_json(root):
            if not isinstance(obj, dict):
                continue
            types = obj.get("@type")
            types = [types] if isinstance(types, str) else types
            type_names = {
                normalize_text(str(value).rsplit("/", 1)[-1].rsplit("#", 1)[-1])
                for value in types
            } if isinstance(types, list) else set()
            if "product" not in type_names:
                continue
            name = obj.get("name")
            if not isinstance(name, str) or not product_name_matches(name, listing):
                continue
            offers = obj.get("offers")
            offer_list = offers if isinstance(offers, list) else [offers]
            prices = [
                price for offer in offer_list if isinstance(offer, dict)
                for price in offer_prices(offer)
            ]
            if prices:
                return min(prices)
    return None

def extract_meta_price(soup: BeautifulSoup) -> Decimal | None:
    selectors = (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
        {"name": "price"},
    )
    for attrs in selectors:
        tag = soup.find("meta", attrs=attrs)
        if tag:
            price = money_to_decimal(tag.get("content"))
            if price is not None:
                return price
    return None


def extract_itemprop_price(soup: BeautifulSoup) -> Decimal | None:
    for tag in soup.find_all(attrs={"itemprop": "price"}):
        for raw in (tag.get("content"), tag.get_text(" ", strip=True)):
            price = money_to_decimal(raw)
            if price is not None:
                return price
    return None


PRICE_PATTERN = re.compile(
    r"(?<!\d)(\d{1,4}(?:[.\s]\d{3})*(?:[,.]\s*\d{2}|[,.]\s*-{1,2})?)(?:\s*)€"
    r"|€(?:\s*)(\d{1,4}(?:[.\s]\d{3})*(?:[,.]\s*\d{2}|[,.]\s*-{1,2})?)",
    flags=re.IGNORECASE,
)


def extract_text_prices(text: str) -> list[tuple[Decimal, int]]:
    """Ritorna (prezzo, score). Score più alto = contesto più plausibile."""
    candidates: list[tuple[Decimal, int]] = []
    for match in PRICE_PATTERN.finditer(text):
        raw = match.group(1) or match.group(2)
        price = money_to_decimal(raw)
        if price is None:
            continue

        start = max(0, match.start() - 90)
        end = min(len(text), match.end() + 90)
        context = normalize_text(text[start:end])
        score = 0

        # Penalizza rate/finanziamenti e favorisce prezzi di vendita.
        if any(word in context for word in ("mese", "mensile", "rata", "rate")):
            score -= 10
        if any(word in context for word in ("prezzo", "prezzo online", "acquista", "carrello")):
            score += 5
        if "iva inclusa" in context or "iva inclusa" in context:
            score += 2
        if any(word in context for word in ("a partire", "da €", "da ")):
            score -= 2
        # Più vicino all'inizio della pagina = piccolo vantaggio.
        score += max(0, 4 - match.start() // 5000)
        candidates.append((price, score))
    return candidates



def extract_class_price(soup: BeautifulSoup) -> Decimal | None:
    candidates: list[tuple[Decimal, int]] = []
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        ident = tag.get("id", "")
        marker = normalize_text(f"{classes} {ident}")
        if "price" not in marker and "prezzo" not in marker:
            continue
        raw_text = tag.get("content") or tag.get_text(" ", strip=True)
        if raw_text:
            candidates.extend(extract_text_prices(raw_text))
        if len(candidates) > 100:
            break
    if not candidates:
        return None
    candidates.sort(key=lambda pair: -pair[1])
    return candidates[0][0]

def extract_expert_price(soup: BeautifulSoup) -> Decimal | None:
    box = soup.select_one(".productDetail-purchase-prices")
    if box is None:
        return None

    row = box.select_one(".discount-price-row")
    if row is None:
        row = box.select_one(".full-price-row")
    if row is None:
        return None

    euro = row.select_one(".sell-price")
    centesimi = row.select_one(".sell-price-lower")
    if euro is None or centesimi is None:
        return None

    raw_price = euro.get_text(strip=True) + centesimi.get_text(strip=True)
    return money_to_decimal(raw_price)
    
def extract_price(soup: BeautifulSoup, listing: dict[str, Any]) -> Decimal | None:
    if listing.get("store") == "Expert":
        return extract_expert_price(soup)
    
    for extractor in (
        lambda: extract_jsonld_price(soup, listing),
        lambda: extract_meta_price(soup),
        lambda: extract_itemprop_price(soup),
        lambda: extract_class_price(soup),
    ):
        price = extractor()
        if price is not None:
            return price

    candidates = extract_text_prices(soup.get_text(" ", strip=True))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: -pair[1])
    return candidates[0][0]

def shorten_url(url: str) -> str:
    if not config.USE_SHORT_LINKS:
        return url
    if url in SHORT_URL_CACHE:
        return SHORT_URL_CACHE[url]
    try:
        response = requests.get(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
            timeout=config.SHORT_URL_TIMEOUT,
        )
        if response.status_code == 200 and response.text.startswith("http"):
            SHORT_URL_CACHE[url] = response.text.strip()
            return SHORT_URL_CACHE[url]
    except requests.RequestException:
        pass
    return url


def telegram_link(url: str) -> str:
    target = shorten_url(url)
    return f'<a href="{html_lib.escape(target, quote=True)}">Apri pagina</a>'



def send_telegram_message(text: str) -> bool:
    """Invia senza mai stampare l'URL Telegram, che contiene il token."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        response = requests.post(url, data=payload, timeout=config.TELEGRAM_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("ok") is not True:
            print("[ERRORE] Telegram ha rifiutato il messaggio (risposta API non valida).")
            return False
        return True
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        suffix = f" HTTP {status}" if status else ""
        print(f"[ERRORE] Invio Telegram fallito ({type(exc).__name__}{suffix}); token oscurato.")
        return False
    except (ValueError, TypeError):
        print("[ERRORE] Telegram ha restituito una risposta non interpretabile.")
        return False


def queue_telegram_message(data: dict[str, Any], text: str) -> None:
    data.setdefault("pending_notifications", []).append({
        "id": uuid.uuid4().hex,
        "text": text,
        "created_at": iso_now(),
        "attempts": 0,
    })


def flush_telegram_outbox(data: dict[str, Any]) -> None:
    queue = data.setdefault("pending_notifications", [])
    retry_at = parse_iso(data.get("telegram_retry_at"))
    if retry_at and now() < retry_at:
        return

    sent = 0
    while queue and sent < config.TELEGRAM_MAX_BATCH:
        item = queue[0]
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            queue.pop(0)
            save_json(data_file(), data)
            continue
        if send_telegram_message(item["text"]):
            queue.pop(0)
            data["telegram_retry_at"] = None
            save_json(data_file(), data)
            sent += 1
            continue
        attempts = int(item.get("attempts", 0)) + 1
        item["attempts"] = attempts
        delay = min(
            config.TELEGRAM_RETRY_BASE_SECONDS * (2 ** min(attempts - 1, 7)),
            config.TELEGRAM_RETRY_MAX_SECONDS,
        )
        data["telegram_retry_at"] = (
            now() + timedelta(seconds=delay)
        ).isoformat(timespec="seconds")
        save_json(data_file(), data)
        return

def check_listing(listing_id: str, listing: dict[str, Any]) -> dict[str, Any]:
    url = listing["url"]
    html_text, source = fetch_page(url)
    soup = BeautifulSoup(html_text, "html.parser")

    ok, product_name = page_is_plausible_for_config(soup, listing)
    if not ok:
        # Se requests ha ricevuto una pagina apparentemente valida ma non
        # verificabile, una seconda prova con Playwright può sbloccare DOM JS.
        if source == "requests" and config.USE_PLAYWRIGHT_FALLBACK:
            html_text = fetch_with_playwright(url)
            soup = BeautifulSoup(html_text, "html.parser")
            source = "playwright"
            ok, product_name = page_is_plausible_for_config(soup, listing)

    if not ok:
        raise RuntimeError(
            "prodotto non verificato: servono SE 3 + GPS + 44 mm + M/L + cassa Mezzanotte e cinturino non bianco"
        )

    if listing.get("price_parser") == "apple_configurator":
        price = extract_apple_configurator_price(soup, listing)
        availability = extract_apple_availability(soup)
    else:
        price = extract_price(soup, listing)
        availability = extract_availability(soup, listing)

    if price is None and source == "requests" and config.USE_PLAYWRIGHT_FALLBACK:
        html_text = fetch_with_playwright(url)
        soup = BeautifulSoup(html_text, "html.parser")
        source = "playwright"
        ok, product_name = page_is_plausible_for_config(soup, listing)
        if not ok:
            raise RuntimeError("prodotto non verificato dopo fallback browser")
        if listing.get("price_parser") == "apple_configurator":
            price = extract_apple_configurator_price(soup, listing)
            availability = extract_apple_availability(soup)
        else:
            price = extract_price(soup, listing)
            availability = extract_availability(soup, listing)
    if listing.get("store") == "Apple":
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        price_nodes = soup.select('[data-autom="summaryHeroPrice"]')
        price_texts = [
            node.get_text(" ", strip=True)
            for node in price_nodes[:3]
        ]
        print(
            f"[DEBUG Apple] file={__file__!r} | "
            f"url={url!r} | source={source!r} | "
            f"parser={listing.get('price_parser')!r} | "
            f"title={title!r} | prezzo_DOM={price_texts!r}"
        )
    if price is None:
        raise RuntimeError("prezzo non trovato per la configurazione richiesta")

    return {
        "price": price,
        "url": url,
        "product_name": product_name or listing.get("variant", listing_id),
        "seller": extract_seller(soup),
        "availability": availability,
        "source": source,
        "checked_at": iso_now(),
    }


def display_name(listing: dict[str, Any]) -> str:
    return f"{listing.get('store', '')} — {listing.get('variant', '')}".strip(" —")


def record_failure(
    data: dict[str, Any],
    listing_id: str,
    listing: dict[str, Any],
    error: Exception,
    notify_errors: bool,
) -> None:
    state = site_state(data, listing_id)
    previous_failures = int(state.get("consecutive_failures", 0))
    state["consecutive_failures"] = previous_failures + 1
    state["last_error"] = str(error)
    state["last_error_at"] = iso_now()

    print(f"[ERRORE] {display_name(listing)}: {error}")

    # Solo quando entriamo nello stato di errore.
    if notify_errors and previous_failures == 0:
        queue_telegram_message(data,
            f"⚠️ <b>{html_lib.escape(display_name(listing))}</b>: non riesco a leggere il prezzo.\n"
            f"{html_lib.escape(str(error))}\n"
            f"{telegram_link(listing['url'])}"
        )


def prices_from_history(state: dict[str, Any]) -> list[Decimal]:
    out: list[Decimal] = []
    for item in state.get("history", []):
        if isinstance(item, dict):
            price = money_to_decimal(item.get("price"), enforce_range=False)
            if price is not None:
                out.append(price)
    return out


def record_success(
    data: dict[str, Any],
    listing_id: str,
    listing: dict[str, Any],
    result: dict[str, Any],
    notify_changes: bool,
    notify_errors: bool,
) -> tuple[bool, str | None]:
    state = site_state(data, listing_id)
    previous_failures = int(state.get("consecutive_failures", 0))
    old_last = state.get("last") or {}
    old_price = money_to_decimal(old_last.get("price"))
    old_availability = old_last.get("availability", "unknown")

    price: Decimal = result["price"]
    availability = result.get("availability", "unknown")
    timestamp = result["checked_at"]
    history = state.setdefault("history", [])

    previous_prices = prices_from_history(state)
    previous_min = min(previous_prices) if previous_prices else None

    # Ogni controllo riuscito viene registrato.
    history.append(
        {
            "price": float(price),
            "checked_at": timestamp,
            "seller": result.get("seller"),
            "availability": availability,
            "source": result.get("source"),
        }
    )
    if len(history) > config.MAX_HISTORY_PER_LISTING:
        del history[:-config.MAX_HISTORY_PER_LISTING]

    state["last"] = {
        "price": float(price),
        "url": result["url"],
        "checked_at": timestamp,
        "seller": result.get("seller"),
        "product_name": result.get("product_name"),
        "availability": availability,
        "source": result.get("source"),
    }
    state["consecutive_failures"] = 0
    state["last_error"] = None
    state["last_success_at"] = timestamp
    state["last_error_at"] = None

    # Recupero dal blocco/error state.
    if notify_errors and previous_failures > 0:
        queue_telegram_message(data,
            f"✅ <b>{html_lib.escape(display_name(listing))}</b> nuovamente leggibile.\n"
            f"Prezzo: <b>{format_price(price)}</b>\n"
            f"{telegram_link(result['url'])}"
        )

    # Disponibilità.
    if (
        notify_changes
        and config.ALERT_AVAILABILITY_CHANGE
        and old_availability in {"available", "unavailable"}
        and availability in {"available", "unavailable"}
        and availability != old_availability
    ):
        if availability == "available":
            msg = (
                f"🟢 <b>TORNATO DISPONIBILE</b> — {html_lib.escape(display_name(listing))}\n"
                f"Prezzo: <b>{format_price(price)}</b>\n{telegram_link(result['url'])}"
            )
        else:
            msg = (
                f"🔴 <b>NON DISPONIBILE</b> — {html_lib.escape(display_name(listing))}\n"
                f"Ultimo prezzo letto: <b>{format_price(price)}</b>\n{telegram_link(result['url'])}"
            )
        queue_telegram_message(data, msg)

    # Cambio prezzo.
    fast_trigger = False
    trigger_reason: str | None = None
    threshold = Decimal(str(config.FAST_CHECK_TRIGGER_PRICE_EURO))
    if notify_changes and config.FAST_CHECK_ENABLED and threshold > 0 and price <= threshold:
        fast_trigger = True
        trigger_reason = f"prezzo sotto {format_price(threshold)}"
    if notify_changes and config.ALERT_ON_PRICE_CHANGE and old_price is not None:
        diff = price - old_price
        if abs(diff) >= Decimal(str(config.MIN_ALERT_CHANGE_EURO)):
            arrow = "🔻" if diff < 0 else "🔺"
            text = (
                f"{arrow} <b>{html_lib.escape(display_name(listing))}</b>\n"
                f"{format_price(old_price)} → <b>{format_price(price)}</b> "
                f"({format_diff(diff)})\n"
            )
            if result.get("seller"):
                text += f"Venditore: {html_lib.escape(str(result['seller']))}\n"
            text += f"Disponibilità: {availability}\n{telegram_link(result['url'])}"
            queue_telegram_message(data, text)

        if diff <= -Decimal(str(config.FAST_CHECK_TRIGGER_DROP_EURO)):
            fast_trigger = True
            trigger_reason = f"calo di {format_diff(diff)}"

    # Nuovo minimo storico.
    if (
        notify_changes
        and config.ALERT_NEW_HISTORICAL_MIN
        and previous_min is not None
        and price < previous_min
    ):
        queue_telegram_message(data,
            f"🚨 <b>NUOVO MINIMO STORICO</b> — {html_lib.escape(display_name(listing))}\n\n"
            f"Prezzo: <b>{format_price(price)}</b>\n"
            f"Precedente minimo: {format_price(previous_min)}\n"
            f"{telegram_link(result['url'])}"
        )

    return fast_trigger, trigger_reason


def check_prices(notify_changes: bool = True, notify_errors: bool = True) -> tuple[dict[str, Any], bool, str | None]:
    data = load_data()
    fast_triggered = False
    fast_reason: str | None = None

    for listing_id, listing in config.SITES.items():
        if not listing.get("enabled", True):
            continue
        try:
            result = check_listing(listing_id, listing)
            triggered, reason = record_success(
                data,
                listing_id,
                listing,
                result,
                notify_changes=notify_changes,
                notify_errors=notify_errors,
            )
            if triggered:
                fast_triggered = True
                fast_reason = reason
            print(
                f"[OK] {display_name(listing)} -> {format_price(result['price'])} "
                f"[{result.get('availability', 'unknown')}]"
            )
        except Exception as exc:
            record_failure(data, listing_id, listing, exc, notify_errors=notify_errors)

    save_json(data_file(), data)
    flush_telegram_outbox(data)
    return data, fast_triggered, fast_reason


# ---------------------------------------------------------------------------
# Recap
# ---------------------------------------------------------------------------



def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.astimezone() if dt.tzinfo else dt.astimezone()
    except (TypeError, ValueError, OverflowError):
        return None

def format_staleness(checked_at: str | None) -> str:
    dt = parse_iso(checked_at)
    if not dt:
        return "mai"
    minutes = max(0, int((now() - dt).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min fa"
    return f"{minutes // 60} h fa"


def build_daily_recap(data: dict[str, Any]) -> str:
    rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for listing_id, listing in config.SITES.items():
        state = data.get("sites", {}).get(listing_id, {})
        last = state.get("last")
        if not isinstance(last, dict):
            continue
        price = money_to_decimal(last.get("price"))
        if price is None:
            continue
        rows.append((listing_id, listing, state))

    if not rows:
        return "⚠️ Nessun prezzo valido disponibile oggi."

    rows.sort(
        key=lambda item: money_to_decimal(item[2]["last"].get("price"))
        or Decimal("999999")
    )

    lines = [
        f"📊 <b>Recap giornaliero — {html_lib.escape(config.PRODUCT_NAME)}</b>",
        "",
    ]

    for index, (_, listing, state) in enumerate(rows):
        last = state["last"]
        price = money_to_decimal(last.get("price")) or Decimal("0")
        marker = "🏆 " if index == 0 else ""
        line = (
            f"{marker}<b>{html_lib.escape(display_name(listing))}</b>: "
            f"<b>{format_price(price)}</b>"
        )
        if last.get("seller"):
            line += f" — {html_lib.escape(str(last['seller']))}"
        availability = last.get("availability", "unknown")
        if availability == "available":
            line += " — 🟢 disponibile"
        elif availability == "unavailable":
            line += " — 🔴 non disponibile"
        else:
            line += " — ⚪ disponibilità non verificata"

        line += f"\n🕒 {format_staleness(last.get('checked_at'))}"
        failures = int(state.get("consecutive_failures", 0))
        if failures:
            line += " — ⚠️ ultimo controllo fallito"
        line += f"\n{telegram_link(last.get('url', listing['url']))}"
        lines.append(line)
        lines.append("")

    # Minimo globale tra tutte le listing storicizzate.
    global_min: tuple[Decimal, str, dict[str, Any]] | None = None
    for _, listing, state in rows:
        hist = prices_from_history(state)
        if not hist:
            continue
        minimum = min(hist)
        if global_min is None or minimum < global_min[0]:
            global_min = (minimum, display_name(listing), listing)

    if global_min:
        lines.append(
            f"📉 <b>Minimo storico complessivo:</b> {format_price(global_min[0])}\n"
            f"{html_lib.escape(global_min[1])}"
        )
        lines.append("")

    if config.EXTRA_LINKS:
        lines.append("<i>Amazon / altri link da controllare con Keepa o manualmente:</i>")
        for name, url in config.EXTRA_LINKS.items():
            lines.append(f"{html_lib.escape(name)} — {telegram_link(url)}")

    lines.append("")
    lines.append(f"🕖 {now().strftime('%d/%m/%Y %H:%M')}")
    return "\n".join(lines)



def send_daily_recap(recheck: bool = True) -> None:
    data = load_data()
    if recheck:
        data, _, _ = check_prices(notify_changes=False, notify_errors=False)
    queue_telegram_message(data, build_daily_recap(data))
    data["last_daily_recap"] = iso_now()
    save_json(data_file(), data)
    flush_telegram_outbox(data)


def validate_config() -> None:
    errors: list[str] = []
    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{20,}", config.TELEGRAM_BOT_TOKEN):
        errors.append("TELEGRAM_BOT_TOKEN mancante o in formato non valido nel file .env")
    if not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", config.TELEGRAM_CHAT_ID):
        errors.append("TELEGRAM_CHAT_ID mancante o in formato non valido nel file .env")
    if not config.SITES:
        errors.append("nessuna pagina configurata")
    if config.CHECK_INTERVAL_MINUTES <= 0:
        errors.append("CHECK_INTERVAL_MINUTES deve essere > 0")
    if config.REQUEST_TIMEOUT <= 0 or config.BROWSER_TIMEOUT_MS <= 0:
        errors.append("i timeout di rete devono essere > 0")
    if config.SCHEDULE_POLL_SECONDS <= 0:
        errors.append("SCHEDULE_POLL_SECONDS deve essere > 0")
    if config.MIN_VALID_PRICE <= 0 or config.MAX_VALID_PRICE <= config.MIN_VALID_PRICE:
        errors.append("intervallo MIN_VALID_PRICE/MAX_VALID_PRICE non valido")
    if config.FAST_CHECK_ENABLED and config.FAST_CHECK_INTERVAL_MINUTES <= 0:
        errors.append("FAST_CHECK_INTERVAL_MINUTES deve essere > 0")
    if config.FAST_CHECK_ENABLED and config.FAST_CHECK_DURATION_MINUTES <= 0:
        errors.append("FAST_CHECK_DURATION_MINUTES deve essere > 0")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", config.DAILY_RECAP_TIME):
        errors.append("DAILY_RECAP_TIME deve essere HH:MM")
    for listing_id, listing in config.SITES.items():
        parsed = urlparse(str(listing.get("url", "")))
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append(f"URL HTTPS non valido per {listing_id}")
    if errors:
        print("[ERRORE CONFIG]")
        for error in errors:
            print(f" - {error}")
        raise SystemExit(1)


def recap_due(now_dt: datetime, last_recap: str | None) -> bool:
    try:
        hour, minute = map(int, config.DAILY_RECAP_TIME.split(":"))
    except (AttributeError, ValueError):
        return False
    scheduled = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_dt < scheduled:
        return False
    last_dt = parse_iso(last_recap)
    return last_dt is None or last_dt.astimezone(now_dt.tzinfo).date() < now_dt.date()


def acquire_instance_lock():
    lock_path = BASE_DIR / ".price_tracker.lock"
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(bytes([0]))
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        raise SystemExit("Un'altra istanza del tracker è già in esecuzione.")
    return handle


def main() -> None:
    validate_config()
    lock_handle = acquire_instance_lock()
    try:
        print("Bot avviato.")
        print(f"Pagine abilitate: {len([s for s in config.SITES.values() if s.get('enabled', True)])}")
        print(f"Controllo normale: ogni {config.CHECK_INTERVAL_MINUTES} minuti | Recap: ogni giorno alle {config.DAILY_RECAP_TIME}")
        print("Primo controllo in corso... (Ctrl+C per fermare)")

        data, fast_triggered, reason = check_prices(notify_changes=True, notify_errors=True)
        if recap_due(now(), data.get("last_daily_recap")):
            if config.SEND_MISSED_DAILY_RECAP_ON_START:
                print("Recap giornaliero mancato: invio in coda.")
                send_daily_recap(recheck=False)
            else:
                data["last_daily_recap"] = iso_now()
                save_json(data_file(), data)
        if os.getenv("PRICE_TRACKER_ONCE", "").lower() == "true":
            print("Controllo singolo completato.")
            return
        completed = now()
        next_normal = completed + timedelta(minutes=config.CHECK_INTERVAL_MINUTES)
        next_fast: datetime | None = None
        fast_until: datetime | None = None
        if config.FAST_CHECK_ENABLED and fast_triggered:
            fast_until = completed + timedelta(minutes=config.FAST_CHECK_DURATION_MINUTES)
            next_fast = completed + timedelta(minutes=config.FAST_CHECK_INTERVAL_MINUTES)
            print(f"[FAST] Attivato: {reason or 'evento prezzo'}")

        while True:
            current = now()
            data = load_data()
            if recap_due(current, data.get("last_daily_recap")):
                send_daily_recap(recheck=True)
                current = now()
                data = load_data()

            flush_telegram_outbox(data)
            did_check = False
            if config.FAST_CHECK_ENABLED and fast_until and next_fast:
                if current >= fast_until:
                    fast_until = None
                    next_fast = None
                    print("[FAST] Terminato; torno al controllo normale.")
                elif current >= next_fast:
                    _, triggered, reason = check_prices(True, True)
                    did_check = True
                    completed = now()
                    next_fast = completed + timedelta(minutes=config.FAST_CHECK_INTERVAL_MINUTES)
                    next_normal = completed + timedelta(minutes=config.CHECK_INTERVAL_MINUTES)
                    if triggered:
                        fast_until = completed + timedelta(minutes=config.FAST_CHECK_DURATION_MINUTES)
                        print(f"[FAST] Prolungato: {reason or 'nuovo evento'}")

            if not did_check and current >= next_normal:
                _, triggered, reason = check_prices(True, True)
                if os.getenv("PRICE_TRACKER_ONCE", "").lower() == "true":
                    print("Controllo singolo completato.")
                    return
                completed = now()
                next_normal = completed + timedelta(minutes=config.CHECK_INTERVAL_MINUTES)
                if config.FAST_CHECK_ENABLED and triggered:
                    fast_until = completed + timedelta(minutes=config.FAST_CHECK_DURATION_MINUTES)
                    next_fast = completed + timedelta(minutes=config.FAST_CHECK_INTERVAL_MINUTES)
                    print(f"[FAST] Attivato: {reason or 'evento prezzo'}")
            time.sleep(config.SCHEDULE_POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nBot fermato.")
    finally:
        lock_handle.close()



if __name__ == "__main__":
    main()
