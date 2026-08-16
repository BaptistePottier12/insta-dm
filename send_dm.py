#!/usr/bin/env python3
"""
send_dm.py
==========
Envoi de messages Instagram pilote depuis une Google Sheet.

ENTREE  : targets.json genere par Apps Script
SORTIE  : results.json (liens de conversation + erreurs), lu ensuite par Claude

Usage :
    python send_dm.py --login                     # premier login manuel + 2FA
    python send_dm.py --targets targets.json --dry-run
    python send_dm.py --targets targets.json
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ============================================================
#  CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PROFILE_DIR = BASE_DIR / os.getenv("PROFILE_DIR", "browser_profile")
LOG_DIR = BASE_DIR / "logs"
RESULTS_FILE = BASE_DIR / "results.json"

TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
VIEWPORT = {"width": 1440, "height": 900}
LOCALE = os.getenv("LOCALE", "fr-FR")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Paris")

DELAI_ENTRE_MESSAGES = (
    int(os.getenv("DELAI_MIN_S", "180")),
    int(os.getenv("DELAI_MAX_S", "420")),
)
MAX_PAR_RUN = int(os.getenv("MAX_PAR_RUN", "20"))
PAUSE_LONGUE_TOUS_LES = 5
PAUSE_LONGUE_S = (600, 1200)

DELAI_TOUCHE_MS = (45, 190)
PROBA_PAUSE_REFLEXION = 0.12
PAUSE_REFLEXION_S = (0.4, 1.6)

SEUIL_OCR = 0.85

# ============================================================
#  LOGGING
# ============================================================

LOG_DIR.mkdir(exist_ok=True)
_log_path = LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

logger = logging.getLogger("ig")
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.DEBUG)
_console.setFormatter(_fmt)
logger.addHandler(_console)

_file = logging.FileHandler(_log_path, encoding="utf-8")
_file.setLevel(logging.DEBUG)
_file.setFormatter(_fmt)
logger.addHandler(_file)

logger.info("Log ecrit dans %s", _log_path)


def brancher_logs_navigateur(page):
    page.on("console", lambda m: logger.debug("[JS:%s] %s", m.type, m.text[:300]))
    page.on("pageerror", lambda err: logger.error("[JS:pageerror] %s", str(err)[:300]))
    page.on(
        "requestfailed",
        lambda r: logger.warning("[NET] echec %s %s", r.method, r.url[:120]),
    )
    page.on(
        "response",
        lambda r: logger.warning("[NET] HTTP %s sur %s", r.status, r.url[:120])
        if r.status >= 400 and "instagram.com" in r.url
        else None,
    )


# ============================================================
#  OUTILS GENERAUX
# ============================================================

def pause(a, b, motif=""):
    d = random.uniform(a, b)
    logger.info("Pause %.1fs %s", d, f"({motif})" if motif else "")
    time.sleep(d)


def extraire_profil(brut):
    if not brut or not str(brut).strip():
        return None, None, "cellule vide"

    candidats = [c.strip() for c in str(brut).split() if c.strip()]
    if not candidats:
        return None, None, "cellule vide"

    vus = []
    for c in candidats:
        bas = c.lower()

        if "linkedin.com" in bas:
            vus.append("linkedin ignore")
            continue

        if "instagram.com" in bas:
            m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", c, re.IGNORECASE)
            if m:
                user = m.group(1).strip("/")
                if user.lower() in {"p", "reel", "reels", "stories", "explore", "direct"}:
                    vus.append(f"lien de contenu ignore ({user})")
                    continue
                return user, f"https://www.instagram.com/{user}/", None
            vus.append("url instagram illisible")
            continue

        if c.startswith("@") and len(c) > 1:
            user = c[1:].strip("/")
            if re.fullmatch(r"[A-Za-z0-9._]+", user):
                return user, f"https://www.instagram.com/{user}/", None

        vus.append(f"non exploitable ({bas[:30]})")

    return None, None, "aucune URL instagram exploitable [" + " ; ".join(vus) + "]"


# ============================================================
#  COURBES DE BEZIER + MOUVEMENT SOURIS
# ============================================================

def _bezier_point(t, p0, p1, p2, p3):
    u = 1 - t
    return (
        u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0],
        u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1],
    )


def clic_humain(page, x, y):
    sx = random.randint(200, VIEWPORT["width"] - 200)
    sy = random.randint(200, VIEWPORT["height"] - 200)

    dx, dy = x - sx, y - sy
    cp1 = (
        sx + random.uniform(0.2, 0.45) * dx + random.randint(-60, 60),
        sy + random.uniform(0.1, 0.35) * dy + random.randint(-80, 80),
    )
    cp2 = (
        sx + random.uniform(0.55, 0.8) * dx + random.randint(-40, 40),
        sy + random.uniform(0.65, 0.9) * dy + random.randint(-25, 25),
    )

    steps = random.randint(18, 35)
    for i in range(steps + 1):
        t = i / steps
        px, py = _bezier_point(t, (sx, sy), cp1, cp2, (x, y))
        page.mouse.move(int(px), int(py))
        page.wait_for_timeout(random.randint(4, 22))

    ox, oy = random.randint(-2, 2), random.randint(-2, 2)
    page.mouse.click(int(x + ox), int(y + oy))
    logger.debug("Clic humain (%d,%d) offset (%+d,%+d)", x, y, ox, oy)


def souris_aleatoire(page):
    try:
        for _ in range(random.randint(1, 3)):
            sx = random.randint(100, VIEWPORT["width"] - 100)
            sy = random.randint(100, VIEWPORT["height"] - 100)
            tx = random.randint(100, VIEWPORT["width"] - 100)
            ty = random.randint(100, VIEWPORT["height"] - 100)
            cp1 = (random.randint(100, VIEWPORT["width"] - 100),
                   random.randint(100, VIEWPORT["height"] - 100))
            cp2 = (random.randint(100, VIEWPORT["width"] - 100),
                   random.randint(100, VIEWPORT["height"] - 100))
            steps = random.randint(10, 20)
            for i in range(steps + 1):
                t = i / steps
                px, py = _bezier_point(t, (sx, sy), cp1, cp2, (tx, ty))
                page.mouse.move(int(px), int(py))
                page.wait_for_timeout(random.randint(5, 18))
            page.wait_for_timeout(random.randint(80, 300))
    except Exception as e:
        logger.debug("Mouvement souris ignore : %s", e)


def frappe_humaine(page, texte):
    for i, ch in enumerate(texte):
        if ch == "\n":
            page.keyboard.press("Shift+Enter")
        else:
            page.keyboard.type(ch)
        page.wait_for_timeout(random.randint(*DELAI_TOUCHE_MS))
        if ch == " " and random.random() < PROBA_PAUSE_REFLEXION:
            t = random.uniform(*PAUSE_REFLEXION_S)
            logger.debug("Pause de frappe %.2fs apres %d caracteres", t, i)
            time.sleep(t)


# ============================================================
#  OCR (FALLBACK)
# ============================================================

def _tesseract_disponible():
    return os.path.isfile(TESSERACT_CMD)


def _langues_ocr():
    tessdata = Path(TESSERACT_CMD).parent / "tessdata"
    langs = [f.stem for f in tessdata.glob("*.traineddata")]
    if "fra" in langs and "eng" in langs:
        return "fra+eng"
    if "eng" in langs:
        return "eng"
    return None


def trouver_texte(page, texte_cherche, seuil=SEUIL_OCR):
    """
    Cherche un texte visible a l'ecran via OCR + difflib.
    Retourne (x_centre, y_centre) ou None.
    """
    if not _tesseract_disponible():
        logger.debug("OCR indisponible (Tesseract absent)")
        return None

    lang = _langues_ocr()
    if not lang:
        logger.debug("OCR indisponible (aucun pack de langue)")
        return None

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.debug("OCR indisponible (pytesseract/PIL non installe)")
        return None

    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        page.screenshot(path=tmp_path)
        img = Image.open(tmp_path)
        data = pytesseract.image_to_data(
            img, lang=lang, output_type=pytesseract.Output.DICT
        )

        texte_lower = texte_cherche.lower().strip()
        mots_cherches = texte_lower.split()
        nb_mots = len(mots_cherches)

        mots = []
        for i in range(len(data["text"])):
            mot = str(data["text"][i]).strip()
            conf = int(data["conf"][i])
            if not mot or conf < 30:
                continue
            mots.append({
                "text": mot,
                "x": int(data["left"][i]),
                "y": int(data["top"][i]),
                "w": int(data["width"][i]),
                "h": int(data["height"][i]),
            })

        best_ratio = 0
        best_pos = None

        if nb_mots == 1:
            for m in mots:
                ratio = SequenceMatcher(None, m["text"].lower(), texte_lower).ratio()
                if ratio > best_ratio and ratio >= seuil:
                    best_ratio = ratio
                    best_pos = (m["x"] + m["w"] // 2, m["y"] + m["h"] // 2)

        if nb_mots > 1:
            for i in range(len(mots) - nb_mots + 1):
                group = mots[i : i + nb_mots]
                y_min = min(m["y"] for m in group)
                y_max = max(m["y"] + m["h"] for m in group)
                if y_max - y_min > 40:
                    continue
                group_text = " ".join(m["text"] for m in group).lower()
                ratio = SequenceMatcher(None, group_text, texte_lower).ratio()
                if ratio > best_ratio and ratio >= seuil:
                    best_ratio = ratio
                    x_min = min(m["x"] for m in group)
                    x_max = max(m["x"] + m["w"] for m in group)
                    best_pos = ((x_min + x_max) // 2, (y_min + y_max) // 2)

        if best_pos:
            logger.info(
                "OCR match '%s' ratio=%.2f pos=(%d,%d)",
                texte_cherche, best_ratio, *best_pos,
            )
        else:
            logger.debug(
                "OCR pas de match pour '%s' (meilleur=%.2f)",
                texte_cherche, best_ratio,
            )
        return best_pos

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ============================================================
#  CASCADE DOM -> OCR -> ECHEC
# ============================================================

def trouver_element(page, selecteurs_dom, texte_ocr, timeout=6000, etape=""):
    """
    1. Essaie chaque selecteur DOM
    2. Si echec, tente OCR avec texte_ocr
    3. Retourne ("dom", locator) | ("ocr", (x,y)) | None
    """
    for sel in selecteurs_dom:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            logger.info("[%s] DOM OK : %s", etape, sel)
            return ("dom", loc)
        except PWTimeout:
            logger.debug("[%s] DOM muet : %s", etape, sel)
        except Exception as e:
            logger.debug("[%s] DOM erreur %s : %s", etape, sel, e)

    if texte_ocr:
        logger.info("[%s] DOM echoue, OCR pour '%s'", etape, texte_ocr)
        pos = trouver_texte(page, texte_ocr)
        if pos:
            return ("ocr", pos)

    logger.warning("[%s] Element introuvable (DOM + OCR)", etape)
    return None


def cliquer_element(page, resultat):
    if resultat is None:
        return False
    mode, val = resultat
    if mode == "dom":
        box = val.bounding_box()
        if box:
            clic_humain(
                page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            )
        else:
            val.click()
    elif mode == "ocr":
        clic_humain(page, val[0], val[1])
    return True


# ============================================================
#  DETECTION DE BLOCAGE
# ============================================================

SIGNAUX_BLOCAGE = [
    "action bloquee", "action blocked",
    "reessayez plus tard", "try again later",
    "nous limitons certaines", "we restrict certain activity",
    "compte temporairement bloque", "temporarily blocked",
    "confirmez votre identite", "confirm your identity",
    "suspicious login", "connexion suspecte",
]


def detecter_blocage(page):
    url = page.url.lower()
    for marqueur in ("challenge", "checkpoint", "accounts/suspended", "/login"):
        if marqueur in url:
            return f"page de blocage detectee (url contient '{marqueur}')"
    try:
        corps = (page.inner_text("body", timeout=4000) or "").lower()
    except Exception:
        return None
    for s in SIGNAUX_BLOCAGE:
        if s in corps:
            return f"message de blocage detecte : '{s}'"
    return None


# ============================================================
#  NAVIGATEUR
# ============================================================

def ouvrir_navigateur(pw):
    PROFILE_DIR.mkdir(exist_ok=True)
    logger.info("Profil navigateur persistant : %s", PROFILE_DIR)

    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=False,
        channel="chrome",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
        user_agent=USER_AGENT,
        viewport=VIEWPORT,
        locale=LOCALE,
        timezone_id=TIMEZONE,
        geolocation={"latitude": 48.8566, "longitude": 2.3522},
        permissions=[],
        ignore_default_args=["--enable-automation"],
    )

    ctx.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr','en-US']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = window.chrome || {runtime: {}};
        """
    )
    return ctx


def est_connecte(page):
    """Verification par URL + presence du lien DM (observe en Phase 1)."""
    logger.info("Verification de la session...")
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(random.randint(3000, 5000))

    url = page.url.lower()
    if "/login" in url or "/accounts/login" in url:
        logger.warning("Redirige vers login : session absente")
        return False
    if "challenge" in url or "checkpoint" in url or "suspended" in url:
        logger.warning("Page de blocage : %s", url)
        return False

    try:
        if page.locator('a[href="/direct/inbox/"]').count() > 0:
            logger.info("Session active (lien DM present)")
            return True
    except Exception:
        pass

    logger.info("Session presumee active (URL OK)")
    return True


def login_manuel(ctx):
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    brancher_logs_navigateur(page)

    page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")

    print("\n" + "=" * 62)
    print("  LOGIN MANUEL")
    print("  Connecte-toi dans la fenetre Chrome qui vient de s'ouvrir.")
    print("  Saisis le mot de passe et le code 2FA toi-meme.")
    print("  Coche 'Enregistrer les informations' si Instagram le propose.")
    print("  Quand ton fil d'actualite est affiche, reviens ici.")
    print("=" * 62)
    input("  Appuie sur Entree une fois connecte... ")

    if est_connecte(page):
        logger.info("Session enregistree dans %s", PROFILE_DIR)
        return True

    logger.error("Session toujours absente apres le login manuel")
    return False


# ============================================================
#  SELECTEURS REELS
#  Source : logs/exploration_20260814_115035/
# ============================================================

# etape1 : input[name="searchInput"][placeholder="Rechercher"] x=120,y=129
SEL_RECHERCHE = [
    'input[name="searchInput"]',
]

# etape4 : div[role=textbox][contenteditable=true] x=538,y=852,w=739
SEL_ZONE_MESSAGE = [
    'div[role="textbox"][contenteditable="true"]',
]


# ============================================================
#  ENVOI D'UN MESSAGE
# ============================================================

def _selectionner_resultat(page, username):
    """
    Cherche le compte dans les resultats de recherche.
    Les resultats sont des div[role=button] contenant le username (Phase 1).
    Retourne True si trouve et clique, False sinon.
    """
    tous = page.locator('div[role="button"]')
    count = tous.count()
    for idx in range(count):
        btn = tous.nth(idx)
        try:
            txt = btn.inner_text(timeout=500)
        except Exception:
            continue
        if username.lower() in txt.lower():
            box = btn.bounding_box()
            if box and box["y"] > 150:
                souris_aleatoire(page)
                clic_humain(page, box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2)
                logger.info("Compte @%s selectionne (DOM, texte: %s)",
                            username, txt[:50].replace("\n", " "))
                return True

    pos = trouver_texte(page, username)
    if pos and pos[1] > 150:
        clic_humain(page, pos[0], pos[1])
        logger.info("Compte @%s selectionne (OCR)", username)
        return True

    return False


def _saisir_et_envoyer(page, zone_result, username, message, dry_run):
    cliquer_element(page, zone_result)
    page.wait_for_timeout(random.randint(500, 1200))

    if dry_run:
        logger.warning("[DRY-RUN] message NON envoye a @%s", username)
        return True, page.url, None

    frappe_humaine(page, message)
    page.wait_for_timeout(random.randint(700, 1600))
    page.keyboard.press("Enter")
    logger.info("Message envoye")
    page.wait_for_timeout(random.randint(2500, 4500))

    blocage = detecter_blocage(page)
    if blocage:
        return False, None, f"BLOCAGE apres envoi: {blocage}"

    url = page.url
    if "/direct/t/" not in url:
        logger.warning("URL inattendue apres envoi : %s", url)
    logger.info("URL de conversation : %s", url)
    return True, url, None


def envoyer_message(page, username, message, url_thread=None, dry_run=False):
    logger.info("--- Cible @%s ---", username)

    # ---- raccourci URL_thread ----
    if url_thread and "/direct/t/" in str(url_thread):
        logger.info("Raccourci URL_thread : %s", url_thread)
        page.goto(url_thread, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(random.randint(2000, 4000))

        blocage = detecter_blocage(page)
        if blocage:
            return False, None, f"BLOCAGE: {blocage}"

        zone = trouver_element(page, SEL_ZONE_MESSAGE, "Votre message",
                               timeout=8000, etape="zone-direct")
        if zone:
            return _saisir_et_envoyer(page, zone, username, message, dry_run)

        logger.warning("Zone message introuvable via URL_thread, repli sur recherche")

    # ---- flux normal ----
    page.goto("https://www.instagram.com/direct/new/",
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(random.randint(2000, 4500))

    blocage = detecter_blocage(page)
    if blocage:
        return False, None, f"BLOCAGE: {blocage}"

    souris_aleatoire(page)

    # 1. champ de recherche
    champ = trouver_element(page, SEL_RECHERCHE, "Rechercher",
                            timeout=8000, etape="recherche")
    if not champ:
        page.screenshot(path=str(LOG_DIR / f"err_recherche_{username}.png"))
        return False, None, "champ de recherche introuvable"

    cliquer_element(page, champ)
    page.wait_for_timeout(random.randint(400, 900))
    frappe_humaine(page, username)
    logger.info("Pseudo saisi, attente des resultats")
    page.wait_for_timeout(random.randint(2500, 4500))

    # 2. selection du compte
    if not _selectionner_resultat(page, username):
        page.screenshot(path=str(LOG_DIR / f"err_resultat_{username}.png"))
        return False, None, "compte introuvable dans les resultats"

    page.wait_for_timeout(random.randint(1500, 3000))

    # 3. zone de message (la conversation s'ouvre apres la selection)
    zone = trouver_element(page, SEL_ZONE_MESSAGE, "Votre message",
                           timeout=8000, etape="zone-message")
    if not zone:
        page.screenshot(path=str(LOG_DIR / f"err_zone_{username}.png"))
        return False, None, "zone de saisie du message introuvable"

    blocage = detecter_blocage(page)
    if blocage:
        return False, None, f"BLOCAGE: {blocage}"

    return _saisir_et_envoyer(page, zone, username, message, dry_run)


# ============================================================
#  BOUCLE PRINCIPALE
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", help="chemin du targets.json")
    ap.add_argument("--login", action="store_true", help="login manuel initial")
    ap.add_argument("--dry-run", action="store_true",
                    help="parcourt tout le flux sans envoyer les messages")
    args = ap.parse_args()

    if not args.login and not args.targets:
        ap.error("--targets ou --login requis")

    with sync_playwright() as pw:
        ctx = ouvrir_navigateur(pw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            brancher_logs_navigateur(page)

            if args.login:
                ok = login_manuel(ctx)
                print("\nSession enregistree." if ok else "\nEchec du login.")
                return 0 if ok else 1

            if not est_connecte(page):
                logger.warning("Pas de session valide, login manuel necessaire")
                page.goto("https://www.instagram.com/accounts/login/",
                          wait_until="domcontentloaded")
                print("\n" + "=" * 62)
                print("  LOGIN REQUIS")
                print("  Connecte-toi dans la fenetre Chrome.")
                print("  Saisis le mot de passe et le code 2FA toi-meme.")
                print("=" * 62)
                input("  Appuie sur Entree une fois connecte... ")

                if not est_connecte(page):
                    logger.error("Session toujours absente apres le login")
                    return 1
                logger.info("Session OK, on continue")

            payload = json.loads(Path(args.targets).read_text(encoding="utf-8"))
            cibles = payload["cibles"]
            message_template = payload.get("message_template", "")
            if not message_template:
                logger.error("message_template absent du payload")
                return 1
            logger.info("%d cible(s) chargee(s), template : %s...",
                        len(cibles), message_template[:60])

            if len(cibles) > MAX_PAR_RUN:
                logger.error("Trop de cibles (%d > %d)", len(cibles), MAX_PAR_RUN)
                return 1

            resultats, envoyes, arret = [], 0, None

            for i, cible in enumerate(cibles, 1):
                ligne = cible["row"]
                sheet = cible.get("sheet", "")
                logger.info("=== %d/%d | %s ligne %d ===",
                            i, len(cibles), sheet, ligne)

                username, url_profil, err = extraire_profil(cible.get("profil_brut"))
                if err:
                    logger.warning("Ligne %d ignoree : %s", ligne, err)
                    resultats.append({
                        "sheet": sheet, "row": ligne, "username": None,
                        "url": "", "statut": "Ignore", "erreur": err,
                    })
                    continue

                logger.info("Profil retenu : @%s (%s)", username, url_profil)

                prenom = cible.get("prenom", "").strip()
                message = message_template.replace("{prenom}", prenom)
                if not prenom:
                    message = message.replace(" ,", ",")
                    message = re.sub(r"  +", " ", message)
                logger.info("Message personnalise pour @%s (prenom=%s)",
                            username, prenom or "(vide)")

                url_thread = cible.get("url_thread", "")

                try:
                    ok, url, err = envoyer_message(
                        page, username, message,
                        url_thread=url_thread,
                        dry_run=args.dry_run,
                    )
                except Exception as e:
                    logger.exception("Exception sur la ligne %d", ligne)
                    ok, url, err = False, None, f"exception : {e}"

                resultats.append({
                    "sheet": sheet,
                    "row": ligne,
                    "username": username,
                    "url": url or "",
                    "statut": ("Simule" if args.dry_run else "Envoye") if ok else "Echec",
                    "erreur": err or "",
                })

                if ok:
                    envoyes += 1
                elif err and err.startswith("BLOCAGE"):
                    arret = err
                    logger.critical("ARRET DU LOT : %s", err)
                    break

                if i < len(cibles):
                    if envoyes and envoyes % PAUSE_LONGUE_TOUS_LES == 0:
                        pause(*PAUSE_LONGUE_S, motif="pause longue")
                    else:
                        pause(*DELAI_ENTRE_MESSAGES, motif="entre deux messages")

            sortie = {
                "termine_a": datetime.now().isoformat(),
                "dry_run": args.dry_run,
                "total": len(cibles),
                "envoyes": envoyes,
                "echecs": sum(1 for r in resultats if r["statut"] == "Echec"),
                "ignores": sum(1 for r in resultats if r["statut"] == "Ignore"),
                "arret_premature": arret,
                "log_file": str(_log_path),
                "resultats": resultats,
            }
            RESULTS_FILE.write_text(
                json.dumps(sortie, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            logger.info("=" * 50)
            logger.info("Termine : %d envoye(s), %d echec(s), %d ignore(s)",
                        sortie["envoyes"], sortie["echecs"], sortie["ignores"])
            logger.info("Resultats : %s", RESULTS_FILE)
            if arret:
                logger.critical("Lot interrompu : %s", arret)
            return 0

        finally:
            logger.info("Fermeture du navigateur dans 5s...")
            time.sleep(5)
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
