#!/usr/bin/env python3
"""
push_results.py
===============
Envoie results.json vers le webhook Apps Script pour mettre a jour la Sheet.

A n'executer QU'APRES confirmation explicite. Le script redemande
lui-meme une validation sauf si --yes est passe.

Usage :
    python push_results.py                  # affiche le recap puis demande confirmation
    python push_results.py --yes            # sans invite (Claude confirme en amont)
    python push_results.py --only 2,5,7     # ne pousse que certaines lignes
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "").strip()
RESULTS_FILE = BASE_DIR / "results.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="ne pas demander de confirmation")
    ap.add_argument("--only", help="liste de numeros de lignes separes par des virgules")
    ap.add_argument("--file", default=str(RESULTS_FILE))
    args = ap.parse_args()

    if not WEBHOOK_URL or not WEBHOOK_TOKEN:
        sys.exit("ERREUR : WEBHOOK_URL ou WEBHOOK_TOKEN absent du .env")

    if not WEBHOOK_URL.startswith("https://"):
        sys.exit("ERREUR : le webhook doit etre en https")

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    resultats = data["resultats"]

    if args.only:
        garder = {int(x.strip()) for x in args.only.split(",") if x.strip()}
        resultats = [r for r in resultats if r["row"] in garder]

    if not resultats:
        sys.exit("Aucune ligne a pousser.")

    updates = [
        {
            "row": r["row"],
            "url": r.get("url", ""),
            "statut": r.get("statut", ""),
            "erreur": (r.get("erreur", "") or "")[:500],
        }
        for r in resultats
    ]

    print(f"\n{len(updates)} ligne(s) a mettre a jour :")
    for u in updates:
        print(f"  ligne {u['row']:>4} | {u['statut']:<8} | {u['url'][:60]} | {u['erreur'][:40]}")

    if data.get("dry_run"):
        print("\nATTENTION : ces resultats viennent d'un DRY-RUN, aucun message n'a ete envoye.")

    if not args.yes:
        rep = input("\nConfirmer l'ecriture dans la Sheet ? (oui/non) ").strip().lower()
        if rep not in ("oui", "o", "yes", "y"):
            sys.exit("Annule.")

    try:
        r = requests.post(
            WEBHOOK_URL,
            json={"token": WEBHOOK_TOKEN, "updates": updates},
            timeout=60,
            allow_redirects=True,
        )
        print(f"\nHTTP {r.status_code}")
        try:
            rep = r.json()
            print(json.dumps(rep, indent=2, ensure_ascii=False))
            if not rep.get("ok"):
                sys.exit(1)
        except ValueError:
            print(r.text[:1000])
            sys.exit("Reponse non JSON : verifie le deploiement du webhook.")
    except requests.RequestException as e:
        sys.exit(f"Echec de l'appel webhook : {e}")

    print("\nSheet mise a jour.")


if __name__ == "__main__":
    main()
