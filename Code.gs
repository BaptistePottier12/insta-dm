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
  COL_CHECKBOX: 'Checkbox',

  HEADERS: {
    coche:    'Checkbox',
    profil:   'Insta',
    prenom:   'Prénom',
    url:      'URL_thread',
    date:     'Date_envoi',
    erreur:   'Erreur'
  },

  ONGLET_MESSAGE: 'Message',

  HEADER_ROW: 1,

  WRITABLE: ['url', 'date', 'erreur'],

  MAX_TARGETS: 40
};

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

/**
 * Renvoie tous les onglets du classeur qui possedent une colonne Checkbox.
 * Chaque element : { sheet, colIdx }
 */
function getOnglets_() {
  console.log('[getOnglets_] Scan de tous les onglets...');
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheets = ss.getSheets();
  var result = [];

  for (var s = 0; s < sheets.length; s++) {
    var sh = sheets[s];
    var nom = sh.getName();
    var lastCol = sh.getLastColumn();
    if (lastCol === 0) {
      console.log('[getOnglets_] ' + nom + ' : vide, ignore');
      continue;
    }

    var entetes = sh.getRange(CONFIG.HEADER_ROW, 1, 1, lastCol).getValues()[0];
    var trouve = false;
    for (var i = 0; i < entetes.length; i++) {
      if (String(entetes[i]).trim().toLowerCase() === CONFIG.COL_CHECKBOX.toLowerCase()) {
        result.push({ sheet: sh, colIdx: i + 1 });
        console.log('[getOnglets_] ' + nom + ' : Checkbox en colonne ' + (i + 1));
        trouve = true;
        break;
      }
    }
    if (!trouve) {
      console.log('[getOnglets_] ' + nom + ' : pas de colonne Checkbox');
    }
  }
  console.log('[getOnglets_] ' + result.length + ' onglet(s) avec Checkbox');
  return result;
}

/**
 * Retrouve l'index (1-based) de chaque colonne a partir de son en-tete.
 * Si requis est false, les colonnes manquantes sont ignorees au lieu de lever une erreur.
 */
function mapColonnes_(sh, requis) {
  var nom = sh.getName();
  var lastCol = sh.getLastColumn();
  var entetes = sh.getRange(CONFIG.HEADER_ROW, 1, 1, lastCol).getValues()[0];
  var map = {};
  var manquantes = [];

  console.log('[mapColonnes_] ' + nom + ' : en-tetes = ' + JSON.stringify(entetes));

  Object.keys(CONFIG.HEADERS).forEach(function(cle) {
    var attendu = String(CONFIG.HEADERS[cle]).trim().toLowerCase();
    var idx = -1;
    for (var i = 0; i < entetes.length; i++) {
      if (String(entetes[i]).trim().toLowerCase() === attendu) { idx = i + 1; break; }
    }
    if (idx === -1) manquantes.push(CONFIG.HEADERS[cle]);
    else map[cle] = idx;
  });

  console.log('[mapColonnes_] ' + nom + ' : map = ' + JSON.stringify(map) +
              (manquantes.length ? ' | manquantes = ' + manquantes.join(', ') : ''));

  if (requis !== false && manquantes.length) {
    throw new Error('En-tetes manquants dans "' + nom + '" : ' + manquantes.join(', '));
  }
  return map;
}

function verifierStructure() {
  var ui = SpreadsheetApp.getUi();
  var onglets = getOnglets_();

  if (!onglets.length) {
    ui.alert('Aucun onglet avec une colonne "' + CONFIG.COL_CHECKBOX + '" trouve.');
    return;
  }

  var rapport = [];
  onglets.forEach(function(o) {
    var nom = o.sheet.getName();
    var lignes = o.sheet.getLastRow() - CONFIG.HEADER_ROW;
    var map = mapColonnes_(o.sheet, false);
    var cols = Object.keys(map).map(function(k) { return k + '=' + map[k]; }).join(', ');
    rapport.push(nom + ' : ' + lignes + ' lignes | ' + cols);
  });

  ui.alert('Structure OK.\n\n' + rapport.join('\n'));
}

// ---------- ETAPE 1 : GENERER LE LOT ----------
function genererLot() {
  var ui = SpreadsheetApp.getUi();
  try {
    console.log('[genererLot] Debut');
    var onglets = getOnglets_();
    if (!onglets.length) {
      ui.alert('Aucun onglet avec une colonne "' + CONFIG.COL_CHECKBOX + '" trouve.');
      return;
    }

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var shMsg = ss.getSheetByName(CONFIG.ONGLET_MESSAGE);
    if (!shMsg) {
      console.error('[genererLot] Onglet Message introuvable');
      ui.alert('Onglet "' + CONFIG.ONGLET_MESSAGE + '" introuvable.');
      return;
    }
    var messageTemplate = String(shMsg.getRange('A1').getValue() || '').trim();
    if (!messageTemplate) {
      ui.alert('La cellule A1 de l\'onglet "' + CONFIG.ONGLET_MESSAGE + '" est vide.');
      return;
    }
    console.log('[genererLot] Template : ' + messageTemplate.substring(0, 60) + '...');

    var cibles = [];
    var ignorees = [];

    onglets.forEach(function(o) {
      var sh = o.sheet;
      var nom = sh.getName();
      var map = mapColonnes_(sh, false);

      if (!map.coche || !map.profil) {
        console.log('[genererLot] ' + nom + ' : colonnes coche/profil manquantes, ignore');
        return;
      }

      var dernLigne = sh.getLastRow();
      if (dernLigne <= CONFIG.HEADER_ROW) {
        console.log('[genererLot] ' + nom + ' : aucune donnee');
        return;
      }

      var nb = dernLigne - CONFIG.HEADER_ROW;
      var valeurs = sh.getRange(CONFIG.HEADER_ROW + 1, 1, nb, sh.getLastColumn()).getValues();
      var cochees = 0;

      for (var i = 0; i < valeurs.length; i++) {
        var ligne = CONFIG.HEADER_ROW + 1 + i;
        var row = valeurs[i];

        var coche = row[map.coche - 1];
        if (coche !== true && String(coche).toUpperCase() !== 'TRUE') continue;
        cochees++;

        var profil = String(row[map.profil - 1] || '').trim();
        var prenom = map.prenom ? String(row[map.prenom - 1] || '').trim() : '';

        if (!profil) { ignorees.push({ sheet: nom, ligne: ligne, raison: 'profil vide' }); continue; }

        cibles.push({ sheet: nom, row: ligne, profil_brut: profil, prenom: prenom });
      }
      console.log('[genererLot] ' + nom + ' : ' + cochees + ' cochee(s), ' +
                  cibles.length + ' cible(s) cumulees');
    });

    console.log('[genererLot] Total : ' + cibles.length + ' cible(s), ' + ignorees.length + ' ignoree(s)');

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
      header_row: CONFIG.HEADER_ROW,
      message_template: messageTemplate,
      webhook_url: ScriptApp.getService().getUrl(),
      cibles: cibles,
      ignorees: ignorees
    };

    console.log('[genererLot] Payload genere, ' + cibles.length + ' cible(s)');
    afficherPayload_(payload);

  } catch (e) {
    console.error('[genererLot] ERREUR : ' + e.message + '\n' + e.stack);
    ui.alert('Erreur : ' + e.message);
  }
}

function afficherPayload_(payload) {
  var json = JSON.stringify(payload, null, 2);
  var prompt =
    "Voici un lot de cibles Instagram genere depuis la Google Sheet.\n\n" +
    "1. Verifie via le connecteur Drive que la structure des onglets " +
    "correspond bien aux colonnes declarees ci-dessous.\n" +
    "2. Ecris ce JSON dans targets.json puis lancer :\n" +
    "   Start-Process -FilePath 'uv' -ArgumentList 'run','python','send_dm.py','--targets','targets.json' -WorkingDirectory 'C:\\Users\\Admin\\Documents\\Claude\\insta-dm' -WindowStyle Normal\n" +
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
  console.log('[decocherTout] Debut');
  var onglets = getOnglets_();
  var total = 0;
  var traites = [];

  onglets.forEach(function(o) {
    var sh = o.sheet;
    var dernLigne = sh.getLastRow();
    if (dernLigne <= 1) return;

    var nb = dernLigne - 1;
    sh.getRange(2, o.colIdx, nb, 1).setValue(false);
    total += nb;
    traites.push(sh.getName());
  });

  console.log('[decocherTout] ' + total + ' cases decochees sur ' + traites.join(', '));
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
 *     { "sheet": "Photographe", "row": 2, "url": "https://...", "erreur": "" }
 *   ]
 * }
 */
function doPostClaude(e) {
  var debut = new Date();
  var log = [];

  console.log('[doPost] Requete recue');

  function repondre(obj, niveau) {
    obj.log = log;
    console[niveau || 'log']('[doPost] Reponse : ' + JSON.stringify(obj));
    return ContentService.createTextOutput(JSON.stringify(obj))
      .setMimeType(ContentService.MimeType.JSON);
  }

  try {
    if (!e || !e.postData || !e.postData.contents) {
      console.error('[doPost] Corps de requete vide');
      return repondre({ ok: false, error: 'Corps de requete vide' }, 'error');
    }

    console.log('[doPost] Body brut : ' + e.postData.contents.substring(0, 200));

    var data;
    try {
      data = JSON.parse(e.postData.contents);
    } catch (err) {
      console.error('[doPost] JSON invalide : ' + err.message);
      return repondre({ ok: false, error: 'JSON invalide : ' + err.message }, 'error');
    }

    var attendu = getToken_();
    if (!data.token || data.token !== attendu) {
      console.error('[doPost] Token refuse (recu: ' + String(data.token).substring(0, 10) + '...)');
      log.push('Token refuse');
      return repondre({ ok: false, error: 'Non autorise' }, 'error');
    }
    log.push('Token valide');
    console.log('[doPost] Token OK');

    if (!Array.isArray(data.updates) || !data.updates.length) {
      return repondre({ ok: false, error: 'updates absent ou vide' }, 'error');
    }
    if (data.updates.length > CONFIG.MAX_TARGETS) {
      return repondre({ ok: false, error: 'Trop de mises a jour' }, 'error');
    }

    console.log('[doPost] ' + data.updates.length + ' update(s) a traiter');

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    console.log('[doPost] Spreadsheet : ' + ss.getName() + ' (id=' + ss.getId() + ')');

    var ecrites = 0;
    var rejetees = [];
    var cache = {};

    var lock = LockService.getScriptLock();
    if (!lock.tryLock(20000)) {
      return repondre({ ok: false, error: 'Feuille verrouillee, reessaie' }, 'error');
    }

    try {
      data.updates.forEach(function(u, idx) {
        var nomOnglet = u.sheet;
        console.log('[doPost] Update ' + (idx + 1) + ' : sheet=' + nomOnglet +
                    ' row=' + u.row + ' url=' + (u.url || '').substring(0, 50));

        if (!nomOnglet) {
          rejetees.push({ row: u.row, raison: 'sheet manquant' });
          return;
        }

        if (!cache[nomOnglet]) {
          var sh = ss.getSheetByName(nomOnglet);
          if (!sh) {
            console.error('[doPost] Onglet introuvable : "' + nomOnglet + '"');
            var noms = ss.getSheets().map(function(s) { return s.getName(); });
            console.log('[doPost] Onglets existants : ' + JSON.stringify(noms));
            rejetees.push({ row: u.row, sheet: nomOnglet, raison: 'onglet introuvable' });
            return;
          }
          console.log('[doPost] Onglet "' + nomOnglet + '" trouve, mappage colonnes...');
          var map = mapColonnes_(sh, false);
          cache[nomOnglet] = { sheet: sh, map: map, lastRow: sh.getLastRow() };
        }

        var ctx = cache[nomOnglet];
        var ligne = parseInt(u.row, 10);

        if (isNaN(ligne) || ligne <= CONFIG.HEADER_ROW || ligne > ctx.lastRow) {
          console.error('[doPost] Ligne hors bornes : ' + u.row +
                       ' (lastRow=' + ctx.lastRow + ')');
          rejetees.push({ row: u.row, sheet: nomOnglet, raison: 'ligne hors bornes' });
          return;
        }

        CONFIG.WRITABLE.forEach(function(cle) {
          if (!(cle in u) || !ctx.map[cle]) return;
          if (cle === 'date') return;
          var valeur = u[cle];
          if (valeur === null || valeur === undefined) valeur = '';
          ctx.sheet.getRange(ligne, ctx.map[cle]).setValue(String(valeur).substring(0, 500));
          console.log('[doPost]   ' + nomOnglet + ' L' + ligne + ' : ' +
                     cle + ' (col ' + ctx.map[cle] + ') = ' + String(valeur).substring(0, 60));
        });

        if (ctx.map.date) {
          ctx.sheet.getRange(ligne, ctx.map.date).setValue(new Date());
          console.log('[doPost]   ' + nomOnglet + ' L' + ligne + ' : date = now');
        }

        if (ctx.map.coche) {
          ctx.sheet.getRange(ligne, ctx.map.coche).setValue(false);
          console.log('[doPost]   ' + nomOnglet + ' L' + ligne + ' : checkbox = false');
        }

        ecrites++;
        log.push(nomOnglet + ' ligne ' + ligne + ' mise a jour');
      });

      SpreadsheetApp.flush();
      console.log('[doPost] Flush OK');
    } finally {
      lock.releaseLock();
    }

    console.log('[doPost] Termine : ' + ecrites + ' ecrite(s), ' +
                rejetees.length + ' rejetee(s), ' + (new Date() - debut) + 'ms');

    return repondre({
      ok: true,
      ecrites: ecrites,
      rejetees: rejetees,
      duree_ms: new Date() - debut
    });

  } catch (err) {
    console.error('[doPost] EXCEPTION : ' + err.message + '\n' + err.stack);
    log.push('Exception : ' + err.message);
    return repondre({ ok: false, error: err.message, stack: err.stack }, 'error');
  }
}

function doGet() {
  console.log('[doGet] Requete GET recue');
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, service: 'campagne-dm', method: 'POST attendu' }))
    .setMimeType(ContentService.MimeType.JSON);
}
