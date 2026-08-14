# Campagne DM Instagram

Pilotage depuis Google Sheet, execution locale sur le PC du client.

## Flux complet

```
Client coche des cases dans la Sheet
        v
Menu "Campagne DM > Generer le lot"   (Apps Script)
        v
Dialogue avec un prompt a copier      -> colle dans Claude Code
        v
Claude verifie la structure via le connecteur Drive
Claude ecrit targets.json puis lance send_dm.py
        v
Chrome s'ouvre, envoie les DM un par un, ecrit results.json
        v
Claude lit results.json, affiche le recap, DEMANDE CONFIRMATION
        v
Claude lance push_results.py --yes  ->  webhook Apps Script  ->  Sheet mise a jour
```

---

## 1. Installation (PC du client)

```powershell
cd C:\Users\Admin\claude_mcp
mkdir ig_campaign
cd ig_campaign
# deposer les fichiers ici
uv venv
uv pip install -r requirements.txt
uv run playwright install chromium
```

## 2. Configuration

```powershell
copy .env.example .env
# generer un token :
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Reporter ce token dans deux endroits, identiques :
- `.env` -> `WEBHOOK_TOKEN`
- Sheet -> menu `Campagne DM > Configurer le token`

## 3. Google Sheet

En-tetes obligatoires en ligne 1 (noms exacts, ordre libre) :

| A_envoyer | Profil | Message | URL_thread | Statut | Date_envoi | Erreur |
|-----------|--------|---------|------------|--------|------------|--------|
| case a cocher | rempli par le client | rempli par le client | ecrit par le webhook | ecrit | ecrit | ecrit |

Pour la colonne `A_envoyer` : Insertion > Case a cocher.

## 4. Apps Script

1. Extensions > Apps Script, coller `Code.gs`
2. Deployer > Nouveau deploiement > **Application Web**
   - Executer en tant que : **moi**
   - Acces : **Tout le monde**
3. Copier l'URL `/exec` dans `.env` -> `WEBHOOK_URL`
4. Recharger la Sheet, le menu "Campagne DM" apparait
5. `Campagne DM > Verifier la structure` doit repondre OK

## 5. Premier login (une seule fois)

```powershell
uv run python send_dm.py --login
```

Chrome s'ouvre. Saisir identifiants et code 2FA **a la main**. La session est
conservee dans `browser_profile/`, les executions suivantes ne redemandent rien.

## 6. Test a blanc

```powershell
uv run python send_dm.py --targets targets.json --dry-run
```

Parcourt tout le flux (recherche, ouverture de conversation, ciblage de la zone
de saisie) **sans envoyer**. A faire systematiquement apres une mise a jour
d'Instagram pour verifier que les selecteurs tiennent toujours.

---

## Prompts Claude Code

### Lancement (colle par le client depuis le dialogue Apps Script)

Le dialogue genere deja le prompt complet. Il contient le JSON du lot et
demande a Claude de verifier la structure avant de lancer.

### Retour (apres execution)

```
Lis results.json. Montre-moi un tableau : ligne, pseudo, statut, URL, erreur.
Verifie via le connecteur Drive que les colonnes URL_thread, Statut,
Date_envoi et Erreur existent bien dans la feuille Campagne.
Ne pousse RIEN tant que je n'ai pas dit oui.
Apres mon accord : uv run python push_results.py --yes
```

---

## Securite

| Point | Traitement |
|---|---|
| Endpoint webhook public | Token de 32 caracteres verifie dans `doPost`, refus sinon |
| Ecriture arbitraire dans la Sheet | Colonnes limitees a `WRITABLE`, numeros de ligne valides contre les bornes reelles |
| Ecritures concurrentes | `LockService` avec timeout de 20s |
| Secrets | `.env` hors git, token cote Sheet dans `ScriptProperties` (jamais dans le code) |
| Session Instagram | Reste dans `browser_profile/`, jamais transmise, dossier gitignore |
| Renvois accidentels | Statut ecrit + case decochee automatiquement apres traitement |
| Volume | Plafond a 40 cibles cote Apps Script, 20 cote Python |
| Blocage Instagram | Arret immediat du lot des qu'une page de challenge ou un message de restriction est detecte |
| Injection de contenu | Valeurs tronquees a 500 caracteres avant ecriture |

## Maintenance

Les selecteurs DOM d'Instagram changent souvent. Ils sont centralises en haut de
`send_dm.py` :

```python
SEL_RECHERCHE     = [...]
SEL_BOUTON_SUIVANT = [...]
SEL_ZONE_MESSAGE  = [...]
```

Chaque etape essaie les selecteurs dans l'ordre et **logue celui qui a marche**.
Quand un envoi echoue, le log indique l'etape exacte et une capture d'ecran est
ecrite dans `logs/err_<etape>_<pseudo>.png`.

Tous les logs vont a la fois dans la console et dans `logs/run_<date>.log` :
console JS de la page, erreurs JS, requetes reseau en echec, reponses HTTP >= 400,
selecteur retenu a chaque etape, duree de chaque pause.

## Limites connues

- Les selecteurs cassent a chaque refonte de l'interface Instagram. Faire un
  `--dry-run` avant chaque campagne.
- La cadence par defaut (3 a 7 minutes entre deux messages, pause longue toutes
  les 5) rend un lot de 20 messages long de plusieurs heures. C'est volontaire.
- Le PC doit rester allume et la session Chrome ouverte pendant toute la duree.
- Un blocage Instagram interrompt le lot : les lignes non traitees restent
  cochees et repartiront au lot suivant.
