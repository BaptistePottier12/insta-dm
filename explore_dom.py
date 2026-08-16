#!/usr/bin/env python3
"""
explore_dom.py
==============
Phase 1 : exploration de l'interface Instagram DM.

Produit un rapport complet (DOM interactif, OCR, captures, HTML des dialogs)
a chaque etape du flux, pour relever les selecteurs et coordonnees REELS.

Usage :
    uv run python explore_dom.py
"""

import csv
import io
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

# Reutilisation directe du contexte navigateur de send_dm
from send_dm import (
    ouvrir_navigateur,
    est_connecte,
    brancher_logs_navigateur,
    detecter_blocage,
    VIEWPORT,
)

# ============================================================
#  CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ============================================================
#  LOGGING
# ============================================================

LOG_DIR.mkdir(exist_ok=True)
_ts = f"{datetime.now():%Y%m%d_%H%M%S}"
EXPLORATION_DIR = LOG_DIR / f"exploration_{_ts}"
EXPLORATION_DIR.mkdir()

logger = logging.getLogger("explore")
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")

_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.DEBUG)
_console.setFormatter(_fmt)
logger.addHandler(_console)

_log_path = EXPLORATION_DIR / "exploration.log"
_file = logging.FileHandler(_log_path, encoding="utf-8")
_file.setLevel(logging.DEBUG)
_file.setFormatter(_fmt)
logger.addHandler(_file)

logger.info("Dossier de sortie : %s", EXPLORATION_DIR)

# ============================================================
#  OCR
# ============================================================

def _tesseract_disponible():
    return os.path.isfile(TESSERACT_CMD)


def _langues_disponibles():
    tessdata = Path(TESSERACT_CMD).parent / "tessdata"
    langs = []
    for f in tessdata.glob("*.traineddata"):
        langs.append(f.stem)
    return langs


def faire_ocr(image_path, output_path):
    """Lance pytesseract sur la capture et ecrit ocr.txt."""
    if not _tesseract_disponible():
        logger.warning("Tesseract introuvable a %s, OCR saute", TESSERACT_CMD)
        output_path.write_text("TESSERACT NON DISPONIBLE\n", encoding="utf-8")
        return

    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    langs_dispo = _langues_disponibles()
    if "fra" in langs_dispo and "eng" in langs_dispo:
        lang = "fra+eng"
    elif "eng" in langs_dispo:
        lang = "eng"
        logger.warning("Pack fra absent, OCR en anglais seul")
    else:
        logger.error("Aucun pack de langue trouve")
        output_path.write_text("AUCUN PACK DE LANGUE\n", encoding="utf-8")
        return

    img = Image.open(image_path)
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)

    lignes = []
    for i in range(len(data["text"])):
        mot = str(data["text"][i]).strip()
        conf = int(data["conf"][i])
        if not mot or conf < 30:
            continue
        x = int(data["left"][i])
        y = int(data["top"][i])
        w = int(data["width"][i])
        h = int(data["height"][i])
        x_centre = x + w // 2
        y_centre = y + h // 2
        lignes.append((mot, conf, x_centre, y_centre, w, h))

    lignes.sort(key=lambda r: (r[3], r[2]))

    out = []
    out.append(f"{'mot':<30} {'conf':>5} {'x_ctr':>6} {'y_ctr':>6} {'larg':>5} {'haut':>5}")
    out.append("-" * 80)
    for mot, conf, xc, yc, w, h in lignes:
        out.append(f"{mot:<30} {conf:>5} {xc:>6} {yc:>6} {w:>5} {h:>5}")

    output_path.write_text("\n".join(out), encoding="utf-8")
    logger.info("OCR : %d mots detectes (conf >= 30)", len(lignes))


# ============================================================
#  EXTRACTION DOM
# ============================================================

JS_EXTRACT_ELEMENTS = """
() => {
    const sels = 'input, textarea, button, [role=button], [role=textbox], ' +
                 '[contenteditable=true], [role=dialog], a, select, [role=tab], ' +
                 '[role=menuitem], [role=option], [role=listbox], [role=combobox], ' +
                 'svg, [data-testid], label, [accept]';
    const elems = document.querySelectorAll(sels);
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const result = [];

    for (const el of elems) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        if (rect.right < 0 || rect.bottom < 0 || rect.left > vw || rect.top > vh) continue;

        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;

        let cls = el.className;
        if (typeof cls !== 'string') cls = '';

        result.push({
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            placeholder: el.getAttribute('placeholder') || '',
            type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '',
            accept: el.getAttribute('accept') || '',
            dataTestId: el.getAttribute('data-testid') || '',
            className: cls.substring(0, 80),
            innerText: (el.innerText || '').substring(0, 60).replace(/\\n/g, ' '),
            href: el.tagName === 'A' ? (el.getAttribute('href') || '').substring(0, 100) : '',
            contentEditable: el.getAttribute('contenteditable') || '',
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
        });
    }
    return result;
}
"""

JS_EXTRACT_DIALOG_HTML = """
() => {
    const d = document.querySelector('[role=dialog]');
    if (!d) return null;
    const html = d.outerHTML;
    return html.substring(0, 20000);
}
"""


JS_EXTRACT_UPLOAD_ZONE = """
() => {
    // Cherche tous les elements proches du textbox de message
    const textbox = document.querySelector('div[role="textbox"][contenteditable="true"]');
    if (!textbox) return {found: false, elements: []};

    // Remonte au conteneur parent qui englobe textbox + icones
    let container = textbox;
    for (let i = 0; i < 5; i++) {
        if (container.parentElement) container = container.parentElement;
    }

    const all = container.querySelectorAll('*');
    const result = [];
    for (const el of all) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;

        const tag = el.tagName.toLowerCase();
        let cls = el.className;
        if (typeof cls !== 'string') cls = '';

        const isInteresting = (
            tag === 'svg' || tag === 'button' || tag === 'input' ||
            tag === 'label' || tag === 'div' || tag === 'span' ||
            el.getAttribute('role') === 'button' ||
            el.getAttribute('type') === 'file' ||
            el.getAttribute('accept') ||
            el.getAttribute('data-testid') ||
            el.getAttribute('aria-label')
        );
        if (!isInteresting) continue;

        result.push({
            tag: tag,
            role: el.getAttribute('role') || '',
            ariaLabel: el.getAttribute('aria-label') || '',
            type: el.getAttribute('type') || '',
            accept: el.getAttribute('accept') || '',
            dataTestId: el.getAttribute('data-testid') || '',
            title: el.getAttribute('title') || '',
            className: cls.substring(0, 80),
            innerText: (el.innerText || '').substring(0, 40).replace(/\\n/g, ' '),
            x: Math.round(rect.x),
            y: Math.round(rect.y),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
            outerHTML: el.outerHTML.substring(0, 200),
        });
    }
    return {found: true, elements: result};
}
"""


def extraire_zone_upload(page, output_path):
    """Extrait les elements autour de la zone de saisie pour trouver l'icone upload."""
    data = page.evaluate(JS_EXTRACT_UPLOAD_ZONE)
    lignes = []
    if not data["found"]:
        lignes.append("TEXTBOX NON TROUVE - impossible d'analyser la zone upload")
    else:
        lignes.append(f"Elements trouves autour du textbox : {len(data['elements'])}")
        lignes.append("")
        for el in data["elements"]:
            lignes.append(f"--- {el['tag']} ---")
            for k in ['role', 'ariaLabel', 'type', 'accept', 'dataTestId', 'title',
                       'className', 'innerText']:
                if el.get(k):
                    lignes.append(f"  {k}: {el[k]}")
            lignes.append(f"  pos: x={el['x']} y={el['y']} w={el['w']} h={el['h']}")
            lignes.append(f"  html: {el['outerHTML']}")
            lignes.append("")
    output_path.write_text("\n".join(lignes), encoding="utf-8")
    logger.info("Zone upload : %d elements extraits", len(data.get("elements", [])))


def extraire_dom(page, output_path):
    """Extrait tous les elements interactifs visibles et ecrit dom.txt."""
    elements = page.evaluate(JS_EXTRACT_ELEMENTS)

    lignes = []
    header = (f"{'tag':<12} {'role':<12} {'aria-label':<30} {'placeholder':<25} "
              f"{'type':<10} {'name':<15} {'accept':<20} {'data-testid':<25} "
              f"{'innerText':<40} "
              f"{'x':>5} {'y':>5} {'w':>5} {'h':>5} {'class'}")
    lignes.append(header)
    lignes.append("=" * 250)

    for el in elements:
        line = (f"{el['tag']:<12} {el['role']:<12} {el['ariaLabel']:<30} "
                f"{el['placeholder']:<25} {el['type']:<10} {el['name']:<15} "
                f"{el['accept']:<20} {el['dataTestId']:<25} "
                f"{el['innerText'][:40]:<40} "
                f"{el['x']:>5} {el['y']:>5} {el['w']:>5} {el['h']:>5} "
                f"{el['className']}")
        if el.get('href'):
            line += f"  href={el['href']}"
        if el.get('contentEditable') == 'true':
            line += "  [contenteditable]"
        lignes.append(line)

    output_path.write_text("\n".join(lignes), encoding="utf-8")
    logger.info("DOM : %d elements interactifs visibles", len(elements))


def extraire_dialog_html(page, output_path):
    """Extrait le outerHTML du [role=dialog] s'il existe."""
    html = page.evaluate(JS_EXTRACT_DIALOG_HTML)
    if html:
        output_path.write_text(html, encoding="utf-8")
        logger.info("Dialog HTML : %d caracteres", len(html))
    else:
        output_path.write_text("AUCUN ELEMENT [role=dialog] TROUVE\n", encoding="utf-8")
        logger.info("Aucun dialog [role=dialog] sur la page")


# ============================================================
#  CAPTURE COMPLETE D'UNE ETAPE
# ============================================================

def capturer_etape(page, nom_etape):
    """Produit le rapport complet pour une etape."""
    dossier = EXPLORATION_DIR / nom_etape
    dossier.mkdir(exist_ok=True)
    logger.info("--- Capture etape : %s ---", nom_etape)

    capture_path = dossier / "capture.png"
    page.screenshot(path=str(capture_path), full_page=False)
    logger.info("Screenshot : %s", capture_path)

    extraire_dom(page, dossier / "dom.txt")
    faire_ocr(capture_path, dossier / "ocr.txt")
    extraire_dialog_html(page, dossier / "html_dialog.txt")
    extraire_zone_upload(page, dossier / "zone_upload.txt")

    blocage = detecter_blocage(page)
    if blocage:
        logger.warning("BLOCAGE detecte : %s", blocage)
        (dossier / "BLOCAGE.txt").write_text(blocage, encoding="utf-8")

    logger.info("URL courante : %s", page.url)
    logger.info("Etape %s terminee", nom_etape)


# ============================================================
#  MAIN
# ============================================================

ETAPES = [
    (
        "etape1_recherche",
        "Page /direct/new/ chargee.\n"
        "  Ne touche a rien, appuie sur Entree pour capturer l'etat initial.",
    ),
    (
        "etape2_liste_resultats",
        "Maintenant dans Chrome :\n"
        "  1. Ouvre la boite de recherche\n"
        "  2. Tape un pseudo\n"
        "  3. Attends que la liste de resultats s'affiche\n"
        "  Puis appuie sur Entree ici.",
    ),
    (
        "etape3_apres_selection",
        "Maintenant dans Chrome :\n"
        "  1. Clique sur le bon compte dans la liste\n"
        "  Puis appuie sur Entree ici.",
    ),
    (
        "etape4_conversation_ouverte",
        "Maintenant dans Chrome :\n"
        "  1. Clique sur le bouton pour ouvrir la conversation\n"
        "     (Chat / Suivant / Next...)\n"
        "  2. Attends que la zone de saisie du message soit visible\n"
        "  Puis appuie sur Entree ici.",
    ),
    (
        "etape5_zone_message_focus",
        "On cherche l'icone d'upload image (photo/clip).\n"
        "  1. Clique dans la zone de saisie du message\n"
        "  2. NE CLIQUE SUR RIEN D'AUTRE\n"
        "  3. Regarde les icones autour de la zone de saisie\n"
        "  Puis appuie sur Entree ici.\n"
        "  (On capture le DOM pour trouver le bouton upload)",
    ),
    (
        "etape6_apres_clic_upload",
        "Maintenant dans Chrome :\n"
        "  1. Clique sur l'icone image/photo/clip\n"
        "  2. Si un dialog fichier s'ouvre, ANNULE-le\n"
        "  3. Reviens ici\n"
        "  Puis appuie sur Entree ici.\n"
        "  (On capture le DOM apres le clic pour voir les changements)",
    ),
]


def verifier_session(page):
    """Verification permissive : on regarde juste l'URL apres navigation."""
    logger.info("Verification de la session...")
    page.goto("https://www.instagram.com/",
              wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    url = page.url.lower()
    if "/login" in url or "/accounts/login" in url:
        logger.warning("Redirige vers login : session absente")
        return False
    if "challenge" in url or "checkpoint" in url or "suspended" in url:
        logger.warning("Page de blocage detectee : %s", url)
        return False
    logger.info("Session detectee (URL : %s)", page.url)
    return True


def main():
    logger.info("=== EXPLORATION DOM/OCR - Instagram DM ===")

    with sync_playwright() as pw:
        ctx = ouvrir_navigateur(pw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            brancher_logs_navigateur(page)

            if not verifier_session(page):
                logger.warning("Session absente, login necessaire")
                page.goto("https://www.instagram.com/accounts/login/",
                          wait_until="domcontentloaded")
                print("\n" + "=" * 62)
                print("  LOGIN REQUIS")
                print("  Connecte-toi dans la fenetre Chrome.")
                print("  Prends ton temps (mot de passe, 2FA, etc.)")
                print("  Attends d'etre sur ton fil d'actualite.")
                print("=" * 62)
                input("  Appuie sur Entree une fois connecte... ")

                page.wait_for_timeout(3000)
                if not verifier_session(page):
                    logger.error("Session toujours absente")
                    print("\n  Si tu es bien connecte, relance le script.")
                    return 1

            logger.info("Session OK, navigation vers /direct/new/")
            page.goto("https://www.instagram.com/direct/new/",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)

            for nom, instructions in ETAPES:
                print("\n" + "=" * 62)
                print(f"  ETAPE : {nom}")
                print(f"  {instructions}")
                print("=" * 62)
                input("  Entree pour capturer... ")

                capturer_etape(page, nom)
                print(f"  -> Rapport ecrit dans {EXPLORATION_DIR / nom}/")

            logger.info("=== EXPLORATION TERMINEE ===")
            logger.info("Dossier complet : %s", EXPLORATION_DIR)
            print(f"\nTermine. Tous les rapports sont dans :\n  {EXPLORATION_DIR}")
            return 0

        finally:
            input("\n  Appuie sur Entree pour fermer le navigateur... ")
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
