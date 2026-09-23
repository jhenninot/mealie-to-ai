# Installation avec Dockge + Nginx Proxy Manager

Guide pas à pas pour déployer le connecteur sur un serveur géré avec
[Dockge](https://dockge.kuma.pet), l'exposer en HTTPS via
[Nginx Proxy Manager](https://nginxproxymanager.com) (NPM) et l'ajouter à claude.ai.

Pour l'installation générique (ligne de commande, autres reverse proxies), voir le
[README](README.md).

## 0. Prérequis

- Un serveur avec **Dockge** installé (stacks dans `/opt/stacks` par défaut).
- **Nginx Proxy Manager** fonctionnel, avec un domaine et des certificats Let's Encrypt.
- Un **sous-domaine libre** pointant sur le serveur, par ex. `mealie-mcp.mondomaine.fr`.
  Les exemples ci-dessous l'utilisent : remplacez-le partout par le vôtre.
- Une instance **Mealie** joignable depuis le serveur (ici : via son URL publique HTTPS).

Le conteneur est **sans état** : aucun volume, aucune base de données. Toute la configuration
tient dans trois variables d'environnement.

## 1. Obtenir le jeton API Mealie

1. Ouvrez Mealie et connectez-vous.
2. Cliquez sur votre avatar → **Profil**, puis **Jetons API**
   (URL directe : `https://mealie.mondomaine.fr/user/profile/api-tokens`).
3. Donnez un nom au jeton, par ex. `claude`, choisissez une durée, puis **Générer**.
4. **Copiez le jeton immédiatement** : Mealie ne l'affiche qu'une seule fois. Copiez uniquement
   le jeton, sans le mot `Bearer` s'il apparaît dans l'exemple affiché.

> **Droits** : le connecteur agit avec les droits de l'utilisateur qui a créé le jeton, et de son
> foyer (*household*). Claude pourra lire et modifier ses recettes, son planning et ses listes de
> courses. Si vous voulez limiter la portée, créez d'abord un utilisateur Mealie dédié plutôt que
> d'utiliser le compte administrateur.

## 2. Générer le jeton du serveur MCP

Ce second jeton n'existe nulle part ailleurs : **c'est vous qui l'inventez**. Il protège votre
serveur MCP, pour que seul Claude puisse l'utiliser.

Sur le serveur, en SSH (le plus simple : le jeton ne transite par aucune autre machine) :

```bash
openssl rand -hex 32
```

Ce qui donne par exemple `9f2c…`, soit 64 caractères hexadécimaux.

<details>
<summary>Depuis Windows</summary>

Dans **Git Bash**, la commande ci-dessus fonctionne telle quelle (Git for Windows fournit
openssl). Dans **PowerShell**, sans rien installer :

```powershell
$b=[byte[]]::new(32); [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [BitConverter]::ToString($b).Replace('-','').ToLower()
```

N'utilisez pas `Get-Random` : ce n'est pas un générateur cryptographique.

</details>

> **Pourquoi `-hex`** : ce jeton se retrouve dans un chemin d'URL (étape 7). L'hexadécimal ne
> contient que des caractères sans ambiguïté. Évitez `openssl rand -base64 32`, qui produit des
> `/`, `+` et `=`. Si vous préférez Python :
> `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` convient aussi.

Contraintes vérifiées au démarrage par [`config.py`](src/mealie_mcp/config.py) :

- **24 caractères minimum**, sinon le conteneur s'arrête avec
  `MCP_AUTH_TOKEN doit faire au moins 24 caractères.`
- Ce jeton fera partie de l'URL du connecteur : **cette URL est donc un secret**, à ne pas
  partager ni coller dans une conversation publique.

Gardez les deux jetons sous la main pour l'étape 4.

## 3. Créer le stack dans Dockge

Dans Dockge : **+ Compose** → nom du stack `mealie-mcp`.

Collez ce `compose.yaml` :

```yaml
services:
  mealie-mcp:
    image: ghcr.io/jhenninot/mealie-to-ai:latest
    pull_policy: always
    container_name: mealie-mcp
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
```

L'image est publique sur GHCR, construite automatiquement pour **amd64 et arm64** (elle fonctionne
donc aussi sur un Raspberry Pi ou un NAS ARM). Aucune authentification n'est nécessaire pour la
récupérer.

> Ne changez pas le port `8000` **à l'intérieur** du conteneur : le `EXPOSE` et le `HEALTHCHECK`
> du [Dockerfile](Dockerfile) le codent en dur. Si le port 8000 est déjà pris sur l'hôte, modifiez
> seulement la partie gauche : `"8001:8000"`.

### Raccorder NPM au conteneur

Deux cas, selon où tourne Nginx Proxy Manager.

**Cas A — NPM tourne sur la même machine (recommandé).** Faites rejoindre au conteneur le réseau
Docker de NPM : NPM pourra alors le joindre par son nom, sans passer par le réseau local.

Trouvez d'abord le nom du réseau de NPM :

```bash
docker inspect <nom-du-conteneur-npm> --format '{{json .NetworkSettings.Networks}}'
# ou simplement :
docker network ls
```

C'est typiquement `npm_default` ou `nginx-proxy-manager_default`. Ajoutez-le au compose :

```yaml
services:
  mealie-mcp:
    image: ghcr.io/jhenninot/mealie-to-ai:latest
    pull_policy: always
    container_name: mealie-mcp
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"   # à retirer une fois l'étape 6 validée
    networks:
      - npm

networks:
  npm:
    external: true
    name: npm_default   # ← le nom trouvé ci-dessus
```

Gardez `ports:` le temps des tests de l'étape 5, puis retirez ces deux lignes et redéployez : le
service ne sera plus accessible que par NPM, ce qui est plus propre.

**Cas B — NPM tourne ailleurs.** Gardez le bloc `ports:` tel quel et, à l'étape 6, pointez NPM sur
l'**IP LAN du serveur** au lieu du nom de conteneur.

## 4. Renseigner les variables d'environnement

Dans Dockge, la page du stack contient une zone **Environment variables** (ou un onglet `.env`)
qui écrit le fichier `/opt/stacks/mealie-mcp/.env`, celui que `env_file: .env` va lire.

```env
MEALIE_URL=https://mealie.mondomaine.fr
MEALIE_API_TOKEN=le-jeton-de-l-etape-1
MCP_AUTH_TOKEN=le-jeton-de-l-etape-2
```

Points d'attention :

- **Pas de guillemets** autour des valeurs : ils seraient pris pour une partie du jeton.
- **`MEALIE_URL` ne contient ni `/api` ni slash final** — le client ajoute `/api` lui-même.
  `https://mealie.mondomaine.fr` ✅ · `https://mealie.mondomaine.fr/api` ❌
- Le fichier `.env` doit **exister avant le premier déploiement**, sinon `env_file:` fait échouer
  Compose. Enregistrez les variables dans Dockge avant de cliquer sur *Deploy*.

Variables optionnelles, rarement utiles :

| Variable | Défaut | Rôle |
|---|---|---|
| `MEALIE_TIMEOUT` | `30` | Délai d'attente (s) des appels vers Mealie. À augmenter si votre Mealie est lent. |
| `PORT` | `8000` | Port d'écoute **dans** le conteneur. À ne pas modifier (voir étape 3). |
| `HOST` | `0.0.0.0` | Interface d'écoute. À ne pas modifier. |
| `MCP_ALLOW_NO_AUTH` | — | Désactive **toute** authentification. Réservé à un usage purement local : **ne l'utilisez pas ici**, le service est exposé sur Internet. |

## 5. Déployer et vérifier en local

Cliquez sur **Deploy** dans Dockge, puis vérifiez :

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}

docker logs mealie-mcp
# aucune ligne "Erreur de configuration : ..."

docker ps --filter name=mealie-mcp
# STATUS doit passer à "healthy" au bout d'une trentaine de secondes
```

Si le conteneur redémarre en boucle, les logs indiquent précisément la variable fautive
(le processus quitte avec le code 2 en cas de configuration invalide). Voir le
[dépannage](#9-dépannage).

## 6. Publier en HTTPS avec Nginx Proxy Manager

claude.ai se connecte **depuis Internet** : le serveur doit être joignable en HTTPS avec un
certificat valide.

Dans NPM : **Hosts → Proxy Hosts → Add Proxy Host**.

Onglet **Details** :

| Champ | Valeur |
|---|---|
| Domain Names | `mealie-mcp.mondomaine.fr` |
| Scheme | `http` |
| Forward Hostname / IP | `mealie-mcp` (cas A) ou l'IP LAN du serveur (cas B) |
| Forward Port | `8000` |
| Cache Assets | désactivé |
| Block Common Exploits | activé |
| Websockets Support | activé |

Onglet **SSL** : *Request a new SSL Certificate* (Let's Encrypt), puis cochez **Force SSL** et
**HTTP/2 Support**, acceptez les conditions et indiquez votre e-mail.

Onglet **Advanced** — les réponses MCP peuvent être longues (import de recette, grosse liste de
courses) :

```nginx
proxy_read_timeout 300s;
proxy_send_timeout 300s;
```

Enregistrez, puis testez depuis l'extérieur (votre PC, ou votre téléphone en 4G) :

```bash
curl https://mealie-mcp.mondomaine.fr/health
# {"status":"ok"}

curl -i https://mealie-mcp.mondomaine.fr/mcp
# HTTP/2 401  {"error":"unauthorized"}   ← normal : aucun jeton fourni
```

Ces deux réponses signifient que le proxy fonctionne **et** que l'authentification est active.

## 7. Ajouter le connecteur dans claude.ai

1. Ouvrez claude.ai → **Paramètres** → **Connecteurs**.
2. **Ajouter un connecteur personnalisé**.
3. Nom : `Mealie`. URL :

```
https://mealie-mcp.mondomaine.fr/mcp/VOTRE_MCP_AUTH_TOKEN
```

Remplacez `VOTRE_MCP_AUTH_TOKEN` par le jeton de l'étape 2, **sans slash final**.

> Les connecteurs personnalisés claude.ai ne permettent pas d'ajouter un en-tête HTTP : c'est
> pourquoi le jeton passe par le chemin de l'URL. Cette URL complète est équivalente à un mot de
> passe — ne la partagez pas.

4. Activez le connecteur, puis testez dans une conversation :
   *« Cherche mes recettes avec du poulet »* ou *« Qu'est-ce qu'il y a au menu cette semaine ? »*

Claude demande une confirmation avant toute suppression : les outils destructifs sont annotés comme
tels.

## 8. Exploitation

**Mettre à jour** — le bouton **Update** de Dockge récupère la dernière image et redémarre le
conteneur (`pull_policy: always` garantit qu'un simple redéploiement suffit aussi). En ligne de
commande :

```bash
cd /opt/stacks/mealie-mcp && docker compose pull && docker compose up -d
```

Pour figer une version ou revenir en arrière, remplacez `:latest` par un tag précis —
`ghcr.io/jhenninot/mealie-to-ai:sha-<commit>` — puis redéployez.

**Changer le jeton MCP** (en cas de fuite de l'URL) :

1. Générez un nouveau jeton (étape 2).
2. Mettez à jour `MCP_AUTH_TOKEN` dans Dockge et redéployez.
3. Mettez à jour l'URL du connecteur dans claude.ai — l'ancienne renvoie désormais 401.

**Changer le jeton Mealie** : révoquez-le dans Mealie, générez-en un nouveau, mettez à jour
`MEALIE_API_TOKEN` et redéployez. L'URL du connecteur, elle, ne change pas.

## 9. Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| Le conteneur redémarre en boucle, log `MEALIE_URL et MEALIE_API_TOKEN sont obligatoires.` | Le `.env` n'a pas été lu, ou une variable est vide | Vérifiez `/opt/stacks/mealie-mcp/.env` ; pas de guillemets, pas d'espace autour du `=` |
| Log `MCP_AUTH_TOKEN doit faire au moins 24 caractères.` | Jeton trop court ou tronqué à la copie | Regénérez-le avec `openssl rand -hex 32` |
| `502 Bad Gateway` dans NPM | NPM ne joint pas le conteneur | Cas A : le conteneur est-il bien sur le réseau de NPM (`docker inspect mealie-mcp`) ? Cas B : le port 8000 est-il publié et le pare-feu ouvert ? |
| `/health` OK mais le connecteur échoue avec 401 | Jeton erroné, tronqué, ou slash final dans l'URL | Comparez l'URL au `MCP_AUTH_TOKEN` du `.env`, caractère par caractère |
| `403` renvoyé par NPM | *Block Common Exploits* interprète mal le jeton dans le chemin | Désactivez cette option sur ce proxy host |
| Claude répond que Mealie renvoie 401 | Jeton Mealie expiré ou révoqué | Générez un nouveau jeton (étape 1) |
| Claude répond que Mealie renvoie 404 | `MEALIE_URL` contient `/api` ou un slash final | Corrigez : uniquement `https://mealie.mondomaine.fr` |
| Le conteneur reste `unhealthy` alors que le service répond | `PORT` a été modifié | Le `HEALTHCHECK` teste le port 8000 en dur : laissez `PORT` par défaut |
| Timeouts sur les grosses opérations | Mealie lent, ou timeout NPM | Augmentez `MEALIE_TIMEOUT` et les `proxy_*_timeout` de l'étape 6 |

Pour inspecter en détail :

```bash
docker logs -f mealie-mcp
docker exec mealie-mcp python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"
```
