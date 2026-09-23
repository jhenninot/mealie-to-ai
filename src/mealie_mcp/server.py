"""Définition du serveur MCP et de ses outils."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, NotRequired, TypedDict

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from . import __version__
from .mealie import MealieClient, MealieError, OrganizerKind

MealType = Literal["breakfast", "lunch", "dinner", "side", "snack", "drink", "dessert"]


class Step(TypedDict):
    """Étape de recette.

    - `summary` : le nom de l'étape (ex. « Blanchir la viande »), affiché en tête de l'étape
      à la place de « Étape N ». C'est le champ à utiliser pour nommer une étape.
    - `title` : un titre de *section*, affiché comme un bandeau qui regroupe cette étape et
      les suivantes (ex. « Pour la garniture »). À n'utiliser que pour découper la recette.
    """

    text: str
    summary: NotRequired[str]
    title: NotRequired[str]

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

INSTRUCTIONS = """\
Accès à l'instance Mealie du foyer (gestionnaire de recettes).
- Les recettes sont identifiées par leur `slug` (obtenu via search_recipes).
- Le planning des repas utilise des dates ISO (AAAA-MM-JJ) et des types de repas
  (breakfast, lunch, dinner, side, snack, drink, dessert).
- Les listes de courses peuvent être désignées par leur nom ou leur id ; si le foyer
  n'a qu'une seule liste, elle est utilisée par défaut.
Avant de supprimer quoi que ce soit, confirmer avec l'utilisateur.
"""


# --- Mise en forme compacte des objets Mealie -------------------------------------


def _names(items: list[dict[str, Any]] | None) -> list[str]:
    return [i["name"] for i in items or []]


def _step(s: dict[str, Any]) -> Step:
    """Étape Mealie -> forme acceptée en écriture, pour un aller-retour sans perte."""
    step: Step = {"text": s.get("text") or ""}
    for field in ("summary", "title"):
        if s.get(field):
            step[field] = s[field]
    return step


def _instruction_payload(step: str | Step) -> dict[str, Any]:
    """Étape fournie par l'appelant -> objet attendu par l'API Mealie."""
    if isinstance(step, str):
        step = {"text": step}
    # ingredientReferences explicite : sans lui, Mealie < 3.20 plante en HTTP 500
    # (TypeError sur RecipeInstruction.__init__), cf. mealie-recipes/mealie#7732.
    payload: dict[str, Any] = {"text": step.get("text") or "", "ingredientReferences": []}
    for field in ("summary", "title"):
        if step.get(field):
            payload[field] = step[field]
    return payload


def _recipe_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": r.get("slug"),
        "name": r.get("name"),
        "description": r.get("description") or None,
        "total_time": r.get("totalTime"),
        "tags": _names(r.get("tags")),
        "categories": _names(r.get("recipeCategory")),
        "rating": r.get("rating"),
    }


def _recipe_detail(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id"),
        "slug": r.get("slug"),
        "name": r.get("name"),
        "description": r.get("description") or None,
        "yield": r.get("recipeYield") or None,
        "servings": r.get("recipeServings") or None,
        "prep_time": r.get("prepTime"),
        "cook_time": r.get("performTime"),
        "total_time": r.get("totalTime"),
        "ingredients": [
            i.get("display") or i.get("note") or i.get("originalText") or ""
            for i in r.get("recipeIngredient") or []
        ],
        "instructions": [_step(s) for s in r.get("recipeInstructions") or []],
        "tags": _names(r.get("tags")),
        "categories": _names(r.get("recipeCategory")),
        "tools": _names(r.get("tools")),
        "notes": [f"{n.get('title', '')}: {n.get('text', '')}".strip(": ") for n in r.get("notes") or []],
        "source_url": r.get("orgURL"),
        "rating": r.get("rating"),
    }


def _mealplan_entry(e: dict[str, Any]) -> dict[str, Any]:
    recipe = e.get("recipe")
    return {
        "id": e.get("id"),
        "date": e.get("date"),
        "meal_type": e.get("entryType"),
        "title": e.get("title") or None,
        "note": e.get("text") or None,
        "recipe": {"slug": recipe["slug"], "name": recipe["name"]} if recipe else None,
    }


def _shopping_item(i: dict[str, Any]) -> dict[str, Any]:
    label = i.get("label")
    return {
        "id": i.get("id"),
        "text": i.get("display") or i.get("note") or "",
        "checked": i.get("checked", False),
        "label": label["name"] if label else None,
    }


def _validate_date(value: str, field: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ToolError(f"{field} doit être une date AAAA-MM-JJ (reçu : {value!r}).") from exc


# --- Construction du serveur ------------------------------------------------------


def build_server(mealie: MealieClient) -> MCPServer:
    mcp = MCPServer(
        name="mealie",
        title="Mealie",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    async def call(coro):
        """Convertit les erreurs Mealie en erreurs d'outil lisibles par le modèle."""
        try:
            return await coro
        except MealieError as exc:
            raise ToolError(str(exc)) from exc

    async def resolve_organizers(kind: OrganizerKind, names: list[str]) -> list[dict[str, Any]]:
        """Retrouve les tags/catégories/ustensiles par nom ou slug, et crée ceux qui manquent."""
        existing = await call(mealie.list_organizers(kind))
        index = {o["name"].casefold(): o for o in existing} | {o["slug"]: o for o in existing}
        result = []
        for name in names:
            name = name.strip()
            if not name:
                continue
            found = index.get(name.casefold()) or index.get(name)
            if found is None:
                found = await call(mealie.create_organizer(kind, name))
                index[name.casefold()] = found
            result.append(found)
        return result

    async def resolve_list(list_ref: str | None) -> dict[str, Any]:
        lists = await call(mealie.list_shopping_lists())
        if not lists:
            raise ToolError("Aucune liste de courses n'existe dans Mealie.")
        if list_ref is None:
            if len(lists) == 1:
                return lists[0]
            raise ToolError(
                "Plusieurs listes existent, préciser laquelle : " + ", ".join(f"{l['name']} ({l['id']})" for l in lists)
            )
        for l in lists:
            if l["id"] == list_ref or l["name"].casefold() == list_ref.casefold():
                return l
        raise ToolError(f"Liste {list_ref!r} introuvable. Listes disponibles : " + ", ".join(l["name"] for l in lists))

    async def build_recipe_patch(
        *,
        name: str | None = None,
        description: str | None = None,
        ingredients: list[str] | None = None,
        instructions: list[str | Step] | None = None,
        recipe_yield: str | None = None,
        prep_time: str | None = None,
        cook_time: str | None = None,
        total_time: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for key, value in {
            "name": name,
            "description": description,
            "recipeYield": recipe_yield,
            "prepTime": prep_time,
            "performTime": cook_time,
            "totalTime": total_time,
            "orgURL": source_url,
        }.items():
            if value is not None:
                patch[key] = value
        if ingredients is not None:
            patch["recipeIngredient"] = [{"note": i} for i in ingredients]
        if instructions is not None:
            patch["recipeInstructions"] = [_instruction_payload(s) for s in instructions]
        if tags is not None:
            patch["tags"] = await resolve_organizers("tags", tags)
        if categories is not None:
            patch["recipeCategory"] = await resolve_organizers("categories", categories)
        if tools is not None:
            patch["tools"] = await resolve_organizers("tools", tools)
        return patch

    # --- Recettes -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def search_recipes(
        query: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Recherche des recettes par texte libre et/ou par tags, catégories ou ustensiles (slugs).

        Sans critère, liste toutes les recettes (paginé). Renvoie un résumé de chaque recette ;
        utiliser get_recipe(slug) pour les ingrédients et les étapes.
        """
        data = await call(
            mealie.search_recipes(
                query, tags=tags, categories=categories, tools=tools, page=page, per_page=min(max(per_page, 1), 100)
            )
        )
        return {
            "total": data.get("total"),
            "page": data.get("page"),
            "total_pages": data.get("total_pages") or data.get("totalPages"),
            "recipes": [_recipe_summary(r) for r in data.get("items", [])],
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_recipe(slug: str) -> dict[str, Any]:
        """Renvoie le détail complet d'une recette : ingrédients, étapes, temps, tags, notes."""
        return _recipe_detail(await call(mealie.get_recipe(slug)))

    @mcp.tool(annotations=WRITE)
    async def create_recipe(
        name: str,
        ingredients: list[str],
        instructions: list[str | Step],
        description: str | None = None,
        recipe_yield: str | None = None,
        prep_time: str | None = None,
        cook_time: str | None = None,
        total_time: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Crée une recette dans Mealie.

        - ingredients : une ligne par ingrédient, quantité incluse (ex. "250 g de farine").
        - instructions : une entrée par étape, soit le texte de l'étape, soit
          {"text": ..., "summary": ...} où summary nomme l'étape (ex. "Blanchir la viande").
          Ajouter "title" uniquement pour ouvrir une nouvelle section (ex. "Pour la garniture").
        - recipe_yield : ex. "4 personnes". Les temps sont du texte libre (ex. "20 minutes").
        - tags / categories / tools : noms ; ceux qui n'existent pas sont créés.
        """
        patch = await build_recipe_patch(
            description=description,
            ingredients=ingredients,
            instructions=instructions,
            recipe_yield=recipe_yield,
            prep_time=prep_time,
            cook_time=cook_time,
            total_time=total_time,
            tags=tags,
            categories=categories,
            tools=tools,
            source_url=source_url,
        )
        slug = await call(mealie.create_recipe(name))
        return _recipe_detail(await call(mealie.patch_recipe(slug, patch)))

    @mcp.tool(annotations=WRITE)
    async def update_recipe(
        slug: str,
        name: str | None = None,
        description: str | None = None,
        ingredients: list[str] | None = None,
        instructions: list[str | Step] | None = None,
        recipe_yield: str | None = None,
        prep_time: str | None = None,
        cook_time: str | None = None,
        total_time: str | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Modifie une recette existante. Seuls les champs fournis sont modifiés.

        Attention : ingredients, instructions, tags, categories et tools REMPLACENT la liste
        existante — relire la recette avec get_recipe et renvoyer la liste complète.
        get_recipe renvoie les étapes sous la forme attendue ici : les réémettre telles quelles
        conserve leur nom (summary) et leur section (title), les omettre les efface.
        """
        patch = await build_recipe_patch(
            name=name,
            description=description,
            ingredients=ingredients,
            instructions=instructions,
            recipe_yield=recipe_yield,
            prep_time=prep_time,
            cook_time=cook_time,
            total_time=total_time,
            tags=tags,
            categories=categories,
            tools=tools,
            source_url=source_url,
        )
        if not patch:
            raise ToolError("Aucun champ à modifier.")
        return _recipe_detail(await call(mealie.patch_recipe(slug, patch)))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def import_recipe_from_url(url: str, include_tags: bool = True) -> dict[str, Any]:
        """Importe une recette depuis une page web (Mealie extrait automatiquement la recette)."""
        slug = await call(mealie.import_recipe_from_url(url, include_tags))
        return _recipe_detail(await call(mealie.get_recipe(slug)))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_recipe(slug: str) -> str:
        """Supprime définitivement une recette. Demander confirmation à l'utilisateur avant."""
        await call(mealie.delete_recipe(slug))
        return f"Recette {slug!r} supprimée."

    # --- Tags / catégories / ustensiles -------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def list_organizers(kind: OrganizerKind) -> list[dict[str, Any]]:
        """Liste les tags, catégories ou ustensiles ("tags", "categories", "tools") avec leur slug."""
        return [{"name": o["name"], "slug": o["slug"], "id": o["id"]} for o in await call(mealie.list_organizers(kind))]

    @mcp.tool(annotations=WRITE)
    async def create_organizer(kind: OrganizerKind, name: str) -> dict[str, Any]:
        """Crée un tag, une catégorie ou un ustensile ("tags", "categories", "tools")."""
        o = await call(mealie.create_organizer(kind, name))
        return {"name": o["name"], "slug": o["slug"], "id": o["id"]}

    # --- Planning des repas -------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def get_meal_plan(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
        """Renvoie le planning des repas entre deux dates incluses (AAAA-MM-JJ).

        Par défaut : aujourd'hui et les 6 jours suivants.
        """
        start = _validate_date(start_date, "start_date") if start_date else dt.date.today().isoformat()
        end = (
            _validate_date(end_date, "end_date")
            if end_date
            else (dt.date.fromisoformat(start) + dt.timedelta(days=6)).isoformat()
        )
        return [_mealplan_entry(e) for e in await call(mealie.list_mealplans(start, end))]

    @mcp.tool(annotations=WRITE)
    async def add_meal_plan_entry(
        date: str,
        meal_type: MealType = "dinner",
        recipe_slug: str | None = None,
        title: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Ajoute un repas au planning.

        Fournir soit recipe_slug (recette Mealie), soit un title libre (ex. "Restes", "Pizza maison").
        """
        if not recipe_slug and not title:
            raise ToolError("Fournir recipe_slug ou title.")
        data: dict[str, Any] = {"date": _validate_date(date, "date"), "entryType": meal_type}
        if recipe_slug:
            data["recipeId"] = (await call(mealie.get_recipe(recipe_slug)))["id"]
        if title:
            data["title"] = title
        if note:
            data["text"] = note
        return _mealplan_entry(await call(mealie.create_mealplan(data)))

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_meal_plan_entry(entry_id: int) -> str:
        """Retire un repas du planning (id obtenu via get_meal_plan)."""
        await call(mealie.delete_mealplan(entry_id))
        return f"Entrée {entry_id} retirée du planning."

    # --- Listes de courses --------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def list_shopping_lists() -> list[dict[str, Any]]:
        """Liste les listes de courses du foyer."""
        return [{"id": l["id"], "name": l["name"]} for l in await call(mealie.list_shopping_lists())]

    @mcp.tool(annotations=READ_ONLY)
    async def get_shopping_list(shopping_list: str | None = None, include_checked: bool = False) -> dict[str, Any]:
        """Renvoie les articles d'une liste de courses (nom ou id ; optionnel s'il n'y a qu'une liste).

        Par défaut, seuls les articles non cochés sont renvoyés.
        """
        ref = await resolve_list(shopping_list)
        data = await call(mealie.get_shopping_list(ref["id"]))
        items = [_shopping_item(i) for i in data.get("listItems", [])]
        if not include_checked:
            items = [i for i in items if not i["checked"]]
        return {"id": data["id"], "name": data["name"], "items": items}

    @mcp.tool(annotations=WRITE)
    async def add_shopping_items(items: list[str], shopping_list: str | None = None) -> dict[str, Any]:
        """Ajoute des articles à une liste de courses, un texte par article (ex. "2 L de lait")."""
        ref = await resolve_list(shopping_list)
        payload = [{"shoppingListId": ref["id"], "note": text, "quantity": 0} for text in items if text.strip()]
        if not payload:
            raise ToolError("Aucun article à ajouter.")
        await call(mealie.create_shopping_items(payload))
        return {"list": ref["name"], "added": [p["note"] for p in payload]}

    @mcp.tool(annotations=WRITE)
    async def set_shopping_item_checked(item_id: str, checked: bool = True) -> dict[str, Any]:
        """Coche (ou décoche avec checked=false) un article de liste de courses."""
        item = await call(mealie.get_shopping_item(item_id))
        item["checked"] = checked
        await call(mealie.update_shopping_item(item_id, item))
        return _shopping_item(item)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def delete_shopping_item(item_id: str) -> str:
        """Supprime un article d'une liste de courses."""
        await call(mealie.delete_shopping_item(item_id))
        return f"Article {item_id} supprimé."

    @mcp.tool(annotations=WRITE)
    async def add_recipe_to_shopping_list(recipe_slug: str, shopping_list: str | None = None, scale: float = 1.0) -> dict[str, Any]:
        """Ajoute tous les ingrédients d'une recette à une liste de courses.

        scale multiplie les quantités (ex. 2 pour doubler la recette).
        """
        ref = await resolve_list(shopping_list)
        recipe = await call(mealie.get_recipe(recipe_slug))
        await call(mealie.add_recipe_to_shopping_list(ref["id"], recipe["id"], scale))
        return {"list": ref["name"], "recipe": recipe["name"], "scale": scale}

    return mcp
