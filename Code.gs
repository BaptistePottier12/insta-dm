/**
 * ============================================================
 *  CAMPAGNE DM INSTAGRAM - Apps Script
 * ------------------------------------------------------------
 *  ENTREE  : le client coche des cases, clique sur le menu,
 *            le script genere un payload JSON a coller dans Claude Code.
 *  SORTIE  : doPost() recoit les resultats du Python et ecrit
 *            les URL / statuts / erreurs dans la feuille.
 * ============================================================
 */

// ---------- CONFIGURATION ----------
var CONFIG = {
  SHEET_NAME: 'Campagne',

  // Noms EXACTS des en-tetes en ligne 1. Le script les retrouve tout seul.
  HEADERS: {
    coche:    'A_envoyer',    // case a cocher (TRUE / FALSE)
    profil:   'Profil',       // cellule pouvant contenir plusieurs URL separees par des espaces
    message:  'Message',      // texte a envoyer
    url:      'URL_thread',   // ECRIT par le webhook
    statut:   'Statut',       // ECRIT par le webhook
    date:     'Date_envoi',   // ECRIT par le webhook
    erreur:   'Erreur'        // ECRIT par le webhook
  },

  HEADER_ROW: 1,

  // Colonnes que le webhook a le DROIT d'ecrire. Rien d'autre ne sera modifie.
  WRITABLE: ['url', 'statut', 'date', 'erreur'],

  // Garde-fou : nombre max de cibles par lot.
  MAX_TARGETS: 40
};

// Le token n'est PAS stocke ici. Voir menu "Configurer le token".
function getToken_() {
  var t = PropertiesService.getScriptProperties().getProperty('WEBHOOK_TOKEN');
  if (!t) throw new Error('Token absent. Menu Campagne DM > Configurer le token.');
  return t;
}

// ---------- MENU ----------
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Campagne DM')
    .addItem('1. Generer le lot a envoyer', 'genererLot')
    .addSeparator()
    .addItem('Configurer le token', 'configurerToken')
    .addItem('Verifier la structure', 'verifierStructure')
    .addItem('Reinitialiser les cases cochees', 'decocherTout')
    .addToUi();
}

function configurerToken() {
  var ui = SpreadsheetApp.getUi();
  var res = ui.prompt(
    'Token du webhook',
    'Colle ici un token long et aleatoire. Il devra etre identique dans le .env du Python.',
    ui.ButtonSet.OK_CANCEL
  );
  if (res.getSelectedButton() !== ui.Button.OK) return;
  var token = res.getResponseText().trim();
  if (token.length < 24) {
    ui.alert('Token trop court. Utilise au moins 24 caracteres aleatoires.');
    return;
  }
  PropertiesService.getScriptProperties().setProperty('WEBHOOK_TOKEN', token);
  ui.alert('Token enregistre.');
}

// ---------- OUTILS STRUCTURE ----------
function getSheet_() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CONFIG.SHEET_NAME);
  if (!sh) throw new Error('Feuille introuvable : ' + CONFIG.SHEET_NAME);
  return sh;
}

/**
 * Retrouve l'index (1-based) de chaque colonne a partir de son en-tete.
 * Evite de cabler des lettres de colonnes en dur : si le client insere
 * une colonne, rien ne casse.
 */
function mapColonnes_(sh) {
  var lastCol = sh.getLastColumn();
  var entetes = sh.getRange(CONFIG.HEADER_ROW, 1, 1, lastCol).getValues()[0];
  var map = {};
  var manquantes = [];

  Object.keys(CONFIG.HEADERS).forEach(function(cle) {
    var attendu = String(CONFIG.HEADERS[cle]).trim().toLowerCase();
    var idx = -1;
    for (var i = 0; i < entetes.length; i++) {
      if (String(entetes[i]).trim().toLowerCase() === attendu) { idx = i + 1; break; }
    }
    if (idx === -1) manquantes.push(CONFIG.HEADERS[cle]);
    else map[cle] = idx;
  });

  if (manquantes.length) {
    throw new Error('En-tetes manquants en ligne ' + CONFIG.HEADER_ROW + ' : ' + manquantes.join(', '));
  }
  console.log('Colonnes detectees : ' + JSON.stringify(map));
  return map;
}

function verifierStructure() {
  var ui = SpreadsheetApp.getUi();
  try {
    var sh = getSheet_();
    var map = mapColonnes_(sh);
    var lignes = sh.getLastRow() - CONFIG.HEADER_ROW;
    ui.alert('Structure OK.\n\nFeuille : ' + CONFIG.SHEET_NAME +
             '\nLignes de donnees : ' + lignes +
             '\nColonnes : ' + JSON.stringify(map, null, 2));
  } catch (e) {
    ui.alert('Probleme de structure :\n\n' + e.message);
  }
}

// ---------- ETAPE 1 : GENERER LE LOT ----------
function genererLot() {
  var ui = SpreadsheetApp.getUi();
  try {
    var sh = getSheet_();
    var map = mapColonnes_(sh);
    var dernLigne = sh.getLastRow();

    if (dernLigne <= CONFIG.HEADER_ROW) { ui.alert('Aucune donnee.'); return; }

    var nb = dernLigne - CONFIG.HEADER_ROW;
    var valeurs = sh.getRange(CONFIG.HEADER_ROW + 1, 1, nb, sh.getLastColumn()).getValues();

    var cibles = [];
    var ignorees = [];

    for (var i = 0; i < valeurs.length; i++) {
      var ligne = CONFIG.HEADER_ROW + 1 + i;
      var row = valeurs[i];

      var coche = row[map.coche - 1];
      if (coche !== true && String(coche).toUpperCase() !== 'TRUE') continue;

      var statut = String(row[map.statut - 1] || '').trim();
      if (statut.toLowerCase() === 'envoye') {
        ignorees.push({ ligne: ligne, raison: 'deja envoye' });
        continue;
      }

      var profil = String(row[map.profil - 1] || '').trim();
      var message = String(row[map.message - 1] || '').trim();

      if (!profil) { ignorees.push({ ligne: ligne, raison: 'profil vide' }); continue; }
      if (!message) { ignorees.push({ ligne: ligne, raison: 'message vide' }); continue; }

      cibles.push({ row: ligne, profil_brut: profil, message: message });
    }

    console.log('Cibles retenues : ' + cibles.length + ' | ignorees : ' + ignorees.length);

    if (!cibles.length) {
      ui.alert('Aucune ligne cochee exploitable.\n\nIgnorees : ' + JSON.stringify(ignorees));
      return;
    }
    if (cibles.length > CONFIG.MAX_TARGETS) {
      ui.alert('Trop de lignes cochees (' + cibles.length + '). Maximum ' + CONFIG.MAX_TARGETS +
               ' par lot pour rester sous les seuils Instagram.');
      return;
    }

    var payload = {
      generated_at: new Date().toISOString(),
      sheet: CONFIG.SHEET_NAME,
      header_row: CONFIG.HEADER_ROW,
      colonnes: map,
      webhook_url: ScriptApp.getService().getUrl(),
      cibles: cibles,
      ignorees: ignorees
    };

    afficherPayload_(payload);

  } catch (e) {
    console.error(e);
    ui.alert('Erreur : ' + e.message);
  }
}

function afficherPayload_(payload) {
  var json = JSON.stringify(payload, null, 2);
  var prompt =
    "Voici un lot de cibles Instagram genere depuis la Google Sheet.\n\n" +
    "1. Verifie via le connecteur Drive que la structure de la feuille \"" + payload.sheet + "\" " +
    "correspond bien aux colonnes declarees ci-dessous.\n" +
    "2. Ecris ce JSON dans targets.json puis lance :\n" +
    "   python send_dm.py --targets targets.json\n" +
    "3. Quand le script a fini, lis results.json, montre-moi le recap " +
    "et DEMANDE-MOI CONFIRMATION avant d'envoyer quoi que ce soit au webhook.\n\n" +
    "```json\n" + json + "\n```";

  var html = HtmlService.createHtmlOutput(
    '<div style="font-family:system-ui,sans-serif;padding:12px">' +
    '<p style="margin:0 0 8px"><b>' + payload.cibles.length + ' cible(s)</b> retenue(s), ' +
    payload.ignorees.length + ' ignoree(s).</p>' +
    '<p style="margin:0 0 8px;font-size:12px;color:#555">Copie ce bloc et colle-le dans Claude Code.</p>' +
    '<textarea id="t" style="width:100%;height:300px;font-family:monospace;font-size:11px">' +
    prompt.replace(/</g, '&lt;') + '</textarea>' +
    '<button onclick="document.getElementById(\'t\').select();document.execCommand(\'copy\');this.textContent=\'Copie\'" ' +
    'style="margin-top:8px;padding:8px 16px;cursor:pointer">Copier</button>' +
    '</div>'
  ).setWidth(700).setHeight(460);

  SpreadsheetApp.getUi().showModalDialog(html, 'Lot a envoyer');
}

function decocherTout() {
  var ONGLETS = [
    'Modèles F', 'Modèles H',
    'Photographe', 'MUA', 'Créateurs', 'Nail/Hair',
    'Cinéma', 'Orga/PR/Lieu', 'Journaliste',
    'Arts', 'Musique', 'Autre', 'Big event'
  ];
  var COL_CHECKBOX = 'Checkbox';
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var total = 0;
  var traites = [];

  ONGLETS.forEach(function(nom) {
    var sh = ss.getSheetByName(nom);
    if (!sh) return;

    var dernLigne = sh.getLastRow();
    if (dernLigne <= 1) return;

    var lastCol = sh.getLastColumn();
    if (lastCol === 0) return;

    var entetes = sh.getRange(1, 1, 1, lastCol).getValues()[0];
    var colIdx = -1;

    for (var i = 0; i < entetes.length; i++) {
      if (String(entetes[i]).trim().toLowerCase() === COL_CHECKBOX.toLowerCase()) {
        colIdx = i + 1;
        break;
      }
    }
    if (colIdx === -1) return;

    var nb = dernLigne - 1;
    sh.getRange(2, colIdx, nb, 1).setValue(false);
    total += nb;
    traites.push(nom);
  });

  console.log('Cases decochees : ' + total + ' sur ' + traites.join(', '));
  SpreadsheetApp.getUi().alert(
    'Cases decochees : ' + total + ' lignes\n\nOnglets traites : ' + traites.join(', ')
  );
}

// ---------- SORTIE : WEBHOOK ----------
/**
 * Corps attendu :
 * {
 *   "token": "...",
 *   "updates": [
 *     { "row": 2, "url": "https://...", "statut": "Envoye", "erreur": "" }
 *   ]
 * }
 */
function doPost(e) {
  var debut = new Date();
  var log = [];

  function repondre(obj, niveau) {
    obj.log = log;
    console[niveau || 'log'](JSON.stringify(obj));
    return ContentService.createTextOutput(JSON.stringify(obj))
      .setMimeType(ContentService.MimeType.JSON);
  }

  try {
    if (!e || !e.postData || !e.postData.contents) {
      return repondre({ ok: false, error: 'Corps de requete vide' }, 'error');
    }

    var data;
    try {
      data = JSON.parse(e.postData.contents);
    } catch (err) {
      return repondre({ ok: false, error: 'JSON invalide : ' + err.message }, 'error');
    }

    // --- Authentification ---
    var attendu = getToken_();
    if (!data.token || data.token !== attendu) {
      log.push('Token refuse');
      return repondre({ ok: false, error: 'Non autorise' }, 'error');
    }
    log.push('Token valide');

    if (!Array.isArray(data.updates) || !data.updates.length) {
      return repondre({ ok: false, error: 'updates absent ou vide' }, 'error');
    }
    if (data.updates.length > CONFIG.MAX_TARGETS) {
      return repondre({ ok: false, error: 'Trop de mises a jour' }, 'error');
    }

    var sh = getSheet_();
    var map = mapColonnes_(sh);
    var dernLigne = sh.getLastRow();

    var ecrites = 0;
    var rejetees = [];

    // Verrou : evite deux ecritures concurrentes.
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(20000)) {
      return repondre({ ok: false, error: 'Feuille verrouillee, reessaie' }, 'error');
    }

    try {
      data.updates.forEach(function(u) {
        var ligne = parseInt(u.row, 10);

        // Validation stricte du numero de ligne.
        if (isNaN(ligne) || ligne <= CONFIG.HEADER_ROW || ligne > dernLigne) {
          rejetees.push({ row: u.row, raison: 'ligne hors bornes' });
          return;
        }

        // Ecriture limitee aux colonnes autorisees.
        CONFIG.WRITABLE.forEach(function(cle) {
          if (!(cle in u)) return;
          if (cle === 'date') return; // gere ci-dessous
          var valeur = u[cle];
          if (valeur === null || valeur === undefined) valeur = '';
          sh.getRange(ligne, map[cle]).setValue(String(valeur).substring(0, 500));
        });

        sh.getRange(ligne, map.date).setValue(new Date());

        // On decoche la ligne traitee pour eviter tout renvoi accidentel.
        sh.getRange(ligne, map.coche).setValue(false);

        ecrites++;
        log.push('Ligne ' + ligne + ' mise a jour');
      });

      SpreadsheetApp.flush();
    } finally {
      lock.releaseLock();
    }

    return repondre({
      ok: true,
      ecrites: ecrites,
      rejetees: rejetees,
      duree_ms: new Date() - debut
    });

  } catch (err) {
    log.push('Exception : ' + err.message);
    return repondre({ ok: false, error: err.message, stack: err.stack }, 'error');
  }
}

function doGet() {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, service: 'campagne-dm', method: 'POST attendu' }))
    .setMimeType(ContentService.MimeType.JSON);
}
