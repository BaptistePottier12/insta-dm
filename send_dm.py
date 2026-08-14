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
import time
from datetime import datetime
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

# Fingerprint FIXE. On ne fait volontairement PAS de rotation de user-agent :
# avec une session persistante, changer d'UA revient a presenter le meme cookie
# depuis plusieurs appareils, ce qui est un signal bien plus suspect qu'un UA stable.
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
VIEWPORT = {"width": 1440, "height": 900}
LOCALE = os.getenv("LOCALE", "fr-FR")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Paris")

# Cadence. Volontairement lente : c'est le principal facteur de survie du compte.
DELAI_ENTRE_MESSAGES = (
    int(os.getenv("DELAI_MIN_S", "180")),
    int(os.getenv("DELAI_MAX_S", "420")),
)
MAX_PAR_RUN = int(os.getenv("MAX_PAR_RUN", "20"))
PAUSE_LONGUE_TOUS_LES = 5          # toutes les N cibles, pause prolongee
PAUSE_LONGUE_S = (600, 1200)

# Frappe clavier humanisee
DELAI_TOUCHE_MS = (45, 190)
PROBA_PAUSE_REFLEXION = 0.12       # pause plus longue entre deux mots
PAUSE_REFLEXION_S = (0.4, 1.6)

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
    """Remonte la console JS, les erreurs de page et les requetes echouees."""
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
#  OUTILS
# ============================================================

def pause(a, b, motif=""):
    d = random.uniform(a, b)
    logger.info("Pause %.1fs %s", d, f"({motif})" if motif else "")
    time.sleep(d)


def extraire_profil(brut):
    """
    Regle metier :
      - la cellule peut contenir plusieurs URL separees par des espaces
      - on parcourt dans l'ordre et on prend la premiere URL Instagram exploitable
      - une entree vide est ignoree, on passe a la suivante
      - une URL LinkedIn est ignoree, on passe a la suivante
      - si rien d'exploitable, la ligne est sautee

    Retourne (username, url_normalisee, erreur)
    """
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

        # Pseudo brut du type @monpseudo
        if c.startswith("@") and len(c) > 1:
            user = c[1:].strip("/")
            if re.fullmatch(r"[A-Za-z0-9._]+", user):
                return user, f"https://www.instagram.com/{user}/", None

        vus.append(f"non exploitable ({bas[:30]})")

    return None, None, "aucune URL instagram exploitable [" + " ; ".join(vus) + "]"


def frappe_humaine(page, texte):
    """Tape le texte caractere par caractere avec une cadence irreguliere."""
    for i, ch in enumerate(texte):
        page.keyboard.type(ch)
        page.wait_for_timeout(random.randint(*DELAI_TOUCHE_MS))
        if ch == " " and random.random() < PROBA_PAUSE_REFLEXION:
            t = random.uniform(*PAUSE_REFLEXION_S)
            logger.debug("Pause de frappe %.2fs apres %d caracteres", t, i)
            time.sleep(t)


def souris_aleatoire(page):
    """Quelques mouvements de souris avant une action."""
    try:
        for _ in range(random.randint(2, 4)):
            page.mouse.move(
                random.randint(100, VIEWPORT["width"] - 100),
                random.randint(100, VIEWPORT["height"] - 100),
                steps=random.randint(8, 20),
            )
            page.wait_for_timeout(random.randint(80, 260))
    except Exception as e:
        logger.debug("Mouvement souris ignore : %s", e)


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
    """Retourne un message si Instagram affiche un blocage, sinon None."""
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
        headless=False,                       # visible, comme demande
        channel="chrome",                     # vrai Chrome si dispo
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

    # Masque les marqueurs d'automatisation les plus evidents.
    ctx.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr','en-US']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        window.chrome = window.chrome || {runtime: {}};
        console.log('[campagne-dm] contexte initialise');
        """
    )
    return ctx


def est_connecte(page):
    logger.info("Verification de la session...")
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(random.randint(2500, 5000))

    if "/login" in page.url or "/accounts/login" in page.url:
        logger.warning("Redirige vers la page de login : session absente")
        return False

    for sel in ('svg[aria-label*="Accueil"]', 'svg[aria-label*="Home"]',
                'a[href="/direct/inbox/"]', 'nav'):
        try:
            if page.locator(sel).count() > 0:
                logger.info("Session active (repere : %s)", sel)
                return True
        except Exception:
            continue

    logger.warning("Aucun repere de session trouve")
    return False


def login_manuel(ctx):
    """Premier login : l'utilisateur tape lui-meme identifiants et code 2FA."""
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
#  ENVOI D'UN MESSAGE
# ============================================================

SEL_RECHERCHE = [
    'input[placeholder*="Rechercher"]',
    'input[placeholder*="Search"]',
    'input[name="queryBox"]',
    'div[role="dialog"] input[type="text"]',
]

SEL_BOUTON_SUIVANT = [
    'div[role="dialog"] button:has-text("Chat")',
    'div[role="dialog"] button:has-text("Suivant")',
    'div[role="dialog"] button:has-text("Next")',
    'div[role="dialog"] div[role="button"]:has-text("Chat")',
]

SEL_ZONE_MESSAGE = [
    'textarea[placeholder*="Message"]',
    'div[role="textbox"][contenteditable="true"]',
    'div[aria-label*="Message"][contenteditable="true"]',
    'p[contenteditable="true"]',
]


def premier_selecteur(page, selecteurs, timeout=6000, etape=""):
    """Essaie plusieurs selecteurs et renvoie le premier qui repond."""
    for sel in selecteurs:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            logger.info("[%s] selecteur retenu : %s", etape, sel)
            return loc
        except PWTimeout:
            logger.debug("[%s] selecteur muet : %s", etape, sel)
        except Exception as e:
            logger.debug("[%s] selecteur en erreur %s : %s", etape, sel, e)
    return None


def envoyer_message(page, username, message, dry_run=False):
    """
    Envoie un DM. Retourne (ok, url_thread, erreur).
    """
    logger.info("--- Cible @%s ---", username)

    page.goto("https://www.instagram.com/direct/new/",
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(random.randint(2000, 4500))

    blocage = detecter_blocage(page)
    if blocage:
        return False, None, f"BLOCAGE: {blocage}"

    souris_aleatoire(page)

    champ = premier_selecteur(page, SEL_RECHERCHE, etape="recherche")
    if not champ:
        page.screenshot(path=str(LOG_DIR / f"err_recherche_{username}.png"))
        return False, None, "champ de recherche introuvable (capture enregistree)"

    champ.click()
    page.wait_for_timeout(random.randint(400, 900))
    frappe_humaine(page, username)
    logger.info("Pseudo saisi, attente des resultats")
    page.wait_for_timeout(random.randint(2500, 4500))

    # Selection du bon compte dans la liste
    try:
        resultat = page.locator(f'div[role="dialog"] :text-is("{username}")').first
        resultat.wait_for(state="visible", timeout=8000)
        souris_aleatoire(page)
        resultat.click()
        logger.info("Compte selectionne dans la liste")
    except Exception as e:
        page.screenshot(path=str(LOG_DIR / f"err_resultat_{username}.png"))
        return False, None, f"compte introuvable dans les resultats : {e}"

    page.wait_for_timeout(random.randint(900, 1800))

    suivant = premier_selecteur(page, SEL_BOUTON_SUIVANT, etape="bouton-suivant")
    if not suivant:
        page.screenshot(path=str(LOG_DIR / f"err_suivant_{username}.png"))
        return False, None, "bouton Chat/Suivant introuvable"
    suivant.click()
    logger.info("Conversation ouverte")
    page.wait_for_timeout(random.randint(2000, 4000))

    blocage = detecter_blocage(page)
    if blocage:
        return False, None, f"BLOCAGE: {blocage}"

    zone = premier_selecteur(page, SEL_ZONE_MESSAGE, etape="zone-message")
    if not zone:
        page.screenshot(path=str(LOG_DIR / f"err_zone_{username}.png"))
        return False, None, "zone de saisie du message introuvable"

    zone.click()
    page.wait_for_timeout(random.randint(500, 1200))

    if dry_run:
        logger.warning("[DRY-RUN] message NON envoye a @%s", username)
        url = page.url
        return True, url, None

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
                logger.error("Pas de session valide. Lance d'abord : python send_dm.py --login")
                return 1

            cibles = json.loads(Path(args.targets).read_text(encoding="utf-8"))["cibles"]
            logger.info("%d cible(s) chargee(s)", len(cibles))

            if len(cibles) > MAX_PAR_RUN:
                logger.error("Trop de cibles (%d > %d)", len(cibles), MAX_PAR_RUN)
                return 1

            resultats, envoyes, arret = [], 0, None

            for i, cible in enumerate(cibles, 1):
                ligne = cible["row"]
                logger.info("=== %d/%d | ligne %d ===", i, len(cibles), ligne)

                username, url_profil, err = extraire_profil(cible.get("profil_brut"))
                if err:
                    logger.warning("Ligne %d ignoree : %s", ligne, err)
                    resultats.append({
                        "row": ligne, "username": None, "url": "",
                        "statut": "Ignore", "erreur": err,
                    })
                    continue

                logger.info("Profil retenu : @%s (%s)", username, url_profil)

                try:
                    ok, url, err = envoyer_message(
                        page, username, cible["message"], dry_run=args.dry_run
                    )
                except Exception as e:
                    logger.exception("Exception sur la ligne %d", ligne)
                    ok, url, err = False, None, f"exception : {e}"

                resultats.append({
                    "row": ligne,
                    "username": username,
                    "url": url or "",
                    "statut": ("Simule" if args.dry_run else "Envoye") if ok else "Echec",
                    "erreur": err or "",
                })

                if ok:
                    envoyes += 1
                elif err and err.startswith("BLOCAGE"):
                    # Arret immediat : continuer aggrave le blocage.
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
