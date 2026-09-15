# Écran vitrine Nauleau — Tracteurs

Ce dossier contient tout ce qu'il faut pour afficher, sur un écran dans le
hall d'entrée, les tracteurs actuellement en vente sur nauleau-agricole.com,
mis à jour automatiquement toutes les 6 heures.

## Comment ça marche

1. `scraper.py` va lire les pages "Tracteurs" du site actuel et note pour
   chaque annonce : le modèle, la marque, la puissance, le compteur, le prix
   et la photo. Il enregistre tout dans `data/tracteurs.json`.
2. Le fichier `.github/workflows/update-tracteurs.yml` demande à GitHub de
   lancer ce script tout seul, automatiquement, toutes les 6 heures — sans
   que personne n'ait besoin d'intervenir.
3. `index.html` est l'écran affiché sur la TV : il lit `data/tracteurs.json`
   et fait défiler les tracteurs, un par un, en boucle.

## Ajouter une page d'information (ex: pour un salon)

Ouvrez le fichier `data/infos.json` dans GitHub (cliquez dessus, puis sur le
crayon ✏️ pour éditer) et modifiez-le comme ceci :

```json
[
  {
    "active": true,
    "title": "Retrouvez-nous au salon",
    "message": "Nous sommes présents au SIMA du 10 au 14 mars, Hall 3 - Stand B45."
  }
]
```

- `"active": true` → la page s'affiche, intercalée tous les 5 tracteurs
- `"active": false` → la page ne s'affiche plus (mais reste enregistrée,
  pratique pour la réactiver l'année suivante)
- Vous pouvez ajouter plusieurs pages dans le tableau, séparées par une
  virgule ; elles s'afficheront chacune leur tour.

Une fois modifié, cliquez sur **Commit changes** en bas de la page. L'écran
récupère automatiquement le changement dans les 30 minutes (ou immédiatement
si vous rechargez la page sur la TV).

## Mise en place (à faire une seule fois)

### 1. Créer un compte GitHub (si vous n'en avez pas déjà un)

Allez sur https://github.com/signup et créez un compte gratuit.

### 2. Créer un nouveau dépôt ("repository")

- Cliquez sur le bouton **New** (ou allez sur https://github.com/new)
- Nom du dépôt : `nauleau-vitrine-tracteurs` (ou ce que vous voulez)
- Cochez **Public**
- Cliquez sur **Create repository**

### 3. Envoyer les fichiers de ce dossier dans le dépôt

Le plus simple : sur la page de votre nouveau dépôt, cliquez sur
**"uploading an existing file"**, puis glissez-déposez TOUS les fichiers et
dossiers présents ici (en gardant bien la structure des dossiers
`.github/workflows/` et `data/`), et validez avec **Commit changes**.

### 4. Activer GitHub Pages (pour que l'écran soit accessible en ligne)

- Dans votre dépôt, allez dans **Settings** (Paramètres)
- Dans le menu de gauche, cliquez sur **Pages**
- Sous "Build and deployment", choisissez **Deploy from a branch**
- Branche : `main`, dossier : `/ (root)`
- Cliquez sur **Save**

Après une à deux minutes, GitHub vous donnera une adresse du type :
`https://votre-nom-utilisateur.github.io/nauleau-vitrine-tracteurs/`

**C'est cette adresse qu'il faudra ouvrir en plein écran sur la TV du hall.**

### 5. Vérifier que la récupération automatique fonctionne

- Dans votre dépôt, allez dans l'onglet **Actions**
- Vous devriez voir le workflow "Mise à jour des tracteurs"
- Cliquez dessus, puis sur **Run workflow** pour le lancer une première fois
  manuellement (sans attendre les 6h)
- Au bout d'une ou deux minutes, le fichier `data/tracteurs.json` doit se
  remplir avec les vrais tracteurs, et le fichier `index.html` (rechargé)
  doit les afficher.

## Afficher ça sur la TV du hall

- Un boîtier (Fire TV Stick, mini-PC, Raspberry Pi) ou une Smart TV avec
  navigateur, réglé pour ouvrir automatiquement l'adresse GitHub Pages ci-
  dessus au démarrage, en plein écran.

## Si besoin d'aide

Si une étape bloque (le scraper ne trouve rien, une page a changé de
structure sur le site, etc.), revenez avec le message d'erreur ou une
capture d'écran et on ajustera le script ensemble.
