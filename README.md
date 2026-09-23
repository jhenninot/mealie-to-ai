# mealie-to-ai

Connecteur entre [Mealie](https://mealie.io) et Claude : un serveur **MCP** (Model Context Protocol)
distant, en HTTP, à ajouter comme **connecteur personnalisé** sur claude.ai (web, mobile, Desktop)
ou dans Claude Code.

Testé avec Mealie v3.27.

## Outils exposés

| Domaine | Outils |
|---|---|
| Recettes | `search_recipes`, `get_recipe`, `create_recipe`, `update_recipe`, `import_recipe_from_url`, `delete_recipe` |
| Planning des repas | `get_meal_plan`, `add_meal_plan_entry`, `delete_meal_plan_entry` |
| Listes de courses | `list_shopping_lists`, `get_shopping_list`, `add_shopping_items`, `set_shopping_item_checked`, `delete_shopping_item`, `add_recipe_to_shopping_list` |
| Organisation | `list_organizers`, `create_organizer` (tags, catégories, ustensiles) |

Exemples de demandes à Claude :
- « Qu'est-ce que je peux cuisiner avec du poulet et des courgettes ? »
- « Planifie les dîners de la semaine prochaine avec des recettes végétariennes. »
- « Ajoute les ingrédients du gratin dauphinois à ma liste de courses, pour 12 personnes. »
- « Importe cette recette : https://… »

## Installation

### 1. Jeton API Mealie
Dans Mealie : **Profil > Jetons API > Générer**. Le connecteur agit avec les droits de cet
utilisateur (et de son foyer).

### 2. Configuration
```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # -> MCP_AUTH_TOKEN
```
Renseigner `MEALIE_URL`, `MEALIE_API_TOKEN` et `MCP_AUTH_TOKEN` dans `.env`.

### 3. Lancement
L'image est construite et publiée automatiquement par GitHub Actions sur
`ghcr.io/jhenninot/mealie-to-ai` (amd64 et arm64) :

| Tag | Contenu |
|---|---|
| `latest` | dernier commit sur `main` (après succès des tests) |
| `sha-<commit>` | un commit précis, pour figer ou revenir en arrière |
| `1.2.3` | une version, en poussant un tag git `v1.2.3` |

Copier [docker-compose.yml](docker-compose.yml) et `.env` sur le serveur, puis :
```bash
docker compose up -d
curl http://localhost:8000/health   # {"status":"ok"}
```
Pour passer au dernier build : `docker compose pull && docker compose up -d`
(ou automatiquement avec [Watchtower](https://containrrr.dev/watchtower/)).

> Déploiement via **Dockge** et **Nginx Proxy Manager**, pas à pas :
> voir [INSTALL-DOCKGE.md](INSTALL-DOCKGE.md).

### 4. Exposition en HTTPS
claude.ai se connecte au serveur depuis Internet : il faut l'exposer en HTTPS (reverse proxy
Caddy/Traefik/Nginx, Cloudflare Tunnel, Tailscale Funnel…). Seul le chemin `/mcp/…` a besoin d'être
publié.

### 5. Ajout dans Claude
**claude.ai** : Paramètres > Connecteurs > Ajouter un connecteur personnalisé, avec l'URL :
```
https://mealie-mcp.exemple.fr/mcp/<MCP_AUTH_TOKEN>
```

**Claude Code** :
```bash
claude mcp add --transport http mealie https://mealie-mcp.exemple.fr/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

## Sécurité
- Le jeton est accepté soit dans l'URL (`/mcp/<jeton>`, car les connecteurs claude.ai n'acceptent
  pas d'en-tête personnalisé), soit en en-tête `Authorization: Bearer`. Toute autre requête reçoit
  une 401. **L'URL du connecteur est donc un secret** : ne la partagez pas.
- Pour changer de jeton, modifiez `MCP_AUTH_TOKEN`, redémarrez, puis mettez à jour l'URL dans Claude.
- Les outils de suppression sont annotés comme destructifs : Claude demande une confirmation avant
  de les utiliser.

## Développement
```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest

# Construire l'image localement
docker build -t mealie-mcp .

# Lancer en local contre un Mealie
MEALIE_URL=http://localhost:9000 MEALIE_API_TOKEN=… MCP_AUTH_TOKEN=… .venv/bin/mealie-mcp
```

Structure :
- `src/mealie_mcp/mealie.py` : client HTTP de l'API Mealie
- `src/mealie_mcp/server.py` : définition des outils MCP
- `src/mealie_mcp/app.py` : application HTTP et authentification
