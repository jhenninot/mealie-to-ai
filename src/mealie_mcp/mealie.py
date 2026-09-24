"""Client asynchrone minimal pour l'API REST de Mealie (v2/v3)."""

from __future__ import annotations

from typing import Any, Literal

import httpx

OrganizerKind = Literal["tags", "categories", "tools"]


class MealieError(RuntimeError):
    """Erreur renvoyée par l'API Mealie, avec un message lisible."""


class MealieClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/api",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise MealieError(f"Mealie injoignable ({exc.__class__.__name__}): {exc}") from exc
        if resp.status_code >= 400:
            detail: Any = resp.text
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                pass
            raise MealieError(f"Mealie {method} {path} -> HTTP {resp.status_code}: {detail}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # --- Recettes -----------------------------------------------------------------

    async def search_recipes(
        self,
        search: str | None = None,
        *,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        tools: list[str] | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "perPage": per_page, "orderBy": "name", "orderDirection": "asc"}
        if search:
            params["search"] = search
        if tags:
            params["tags"] = tags
        if categories:
            params["categories"] = categories
        if tools:
            params["tools"] = tools
        return await self._request("GET", "/recipes", params=params)

    async def get_recipe(self, slug: str) -> dict[str, Any]:
        return await self._request("GET", f"/recipes/{slug}")

    async def create_recipe(self, name: str) -> str:
        slug = await self._request("POST", "/recipes", json={"name": name})
        return str(slug).strip('"')

    async def patch_recipe(self, slug: str, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PATCH", f"/recipes/{slug}", json=data)

    async def delete_recipe(self, slug: str) -> None:
        await self._request("DELETE", f"/recipes/{slug}")

    async def import_recipe_from_url(self, url: str, include_tags: bool = True) -> str:
        slug = await self._request("POST", "/recipes/create/url", json={"url": url, "includeTags": include_tags})
        return str(slug).strip('"')

    # --- Organisation (tags / catégories / ustensiles) ----------------------------

    async def list_organizers(self, kind: OrganizerKind) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/organizers/{kind}", params={"perPage": -1, "orderBy": "name", "orderDirection": "asc"})
        return data["items"]

    async def create_organizer(self, kind: OrganizerKind, name: str) -> dict[str, Any]:
        return await self._request("POST", f"/organizers/{kind}", json={"name": name})

    # --- Unités et aliments (ingrédients structurés) -------------------------------

    async def list_units(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/units", params={"perPage": -1, "orderBy": "name", "orderDirection": "asc"})
        return data["items"]

    async def create_unit(self, name: str) -> dict[str, Any]:
        return await self._request("POST", "/units", json={"name": name})

    async def list_foods(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/foods", params={"perPage": -1, "orderBy": "name", "orderDirection": "asc"})
        return data["items"]

    async def create_food(self, name: str) -> dict[str, Any]:
        return await self._request("POST", "/foods", json={"name": name})

    # --- Planning des repas -------------------------------------------------------

    async def list_mealplans(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/households/mealplans",
            params={"start_date": start_date, "end_date": end_date, "perPage": -1, "orderBy": "date", "orderDirection": "asc"},
        )
        return data["items"]

    async def create_mealplan(self, data: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/households/mealplans", json=data)

    async def delete_mealplan(self, entry_id: int | str) -> None:
        await self._request("DELETE", f"/households/mealplans/{entry_id}")

    # --- Listes de courses --------------------------------------------------------

    async def list_shopping_lists(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/households/shopping/lists", params={"perPage": -1, "orderBy": "name", "orderDirection": "asc"})
        return data["items"]

    async def get_shopping_list(self, list_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/households/shopping/lists/{list_id}")

    async def create_shopping_items(self, items: list[dict[str, Any]]) -> Any:
        return await self._request("POST", "/households/shopping/items/create-bulk", json=items)

    async def get_shopping_item(self, item_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/households/shopping/items/{item_id}")

    async def update_shopping_item(self, item_id: str, data: dict[str, Any]) -> Any:
        return await self._request("PUT", f"/households/shopping/items/{item_id}", json=data)

    async def delete_shopping_item(self, item_id: str) -> None:
        await self._request("DELETE", f"/households/shopping/items/{item_id}")

    async def add_recipe_to_shopping_list(self, list_id: str, recipe_id: str, scale: float = 1.0) -> Any:
        return await self._request(
            "POST",
            f"/households/shopping/lists/{list_id}/recipe",
            json=[{"recipeId": recipe_id, "recipeIncrementQuantity": scale}],
        )
