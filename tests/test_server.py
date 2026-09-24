import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import Client

from mealie_mcp.app import create_app
from mealie_mcp.config import ConfigError, Settings
from mealie_mcp.mealie import MealieClient
from mealie_mcp.server import build_server

TOKEN = "test-secret-token-0123456789"


class FakeMealie:
    """Simule le strict nécessaire de l'API Mealie."""

    def __init__(self):
        self.tags = [{"id": "t1", "name": "Dessert", "slug": "dessert"}]
        self.lists = [{"id": "l1", "name": "Courses"}]
        self.recipes: dict[str, dict] = {}
        self.units = [{"id": "u1", "name": "gramme", "abbreviation": "g"}]
        self.foods = [{"id": "f1", "name": "tomme fraîche", "aliases": [{"name": "tome fraîche"}]}]
        self.recipe = {
            "slug": "truffade",
            "name": "Truffade",
            "recipeIngredient": [
                {
                    "referenceId": "r1",
                    "quantity": 400,
                    "unit": self.units[0],
                    "food": self.foods[0],
                    "note": "en fines lamelles",
                    "display": "400 g tomme fraîche en fines lamelles",
                    "originalText": "400 g de tomme fraîche, en fines lamelles",
                },
                {"referenceId": "r2", "quantity": 1, "unit": None, "food": None, "note": "Sel, poivre", "display": "Sel, poivre"},
            ],
        }
        self.requests: list[tuple[str, str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.removeprefix("/api")
        self.requests.append((request.method, path, body))
        match request.method, path:
            case "GET", "/organizers/tags":
                return httpx.Response(200, json={"items": self.tags})
            case "POST", "/organizers/tags":
                tag = {"id": f"t{len(self.tags) + 1}", "name": body["name"], "slug": body["name"].lower()}
                self.tags.append(tag)
                return httpx.Response(201, json=tag)
            case "POST", "/recipes":
                return httpx.Response(201, json="tarte")
            case "GET", path if path.removeprefix("/recipes/") in self.recipes:
                return httpx.Response(200, json=self.recipes[path.removeprefix("/recipes/")])
            case "PATCH", "/recipes/tarte":
                return httpx.Response(200, json={"slug": "tarte", "name": "Tarte", **body})
            case "GET", "/units":
                return httpx.Response(200, json={"items": self.units})
            case "POST", "/units":
                unit = {"id": f"u{len(self.units) + 1}", "name": body["name"]}
                self.units.append(unit)
                return httpx.Response(201, json=unit)
            case "GET", "/foods":
                return httpx.Response(200, json={"items": self.foods})
            case "POST", "/foods":
                food = {"id": f"f{len(self.foods) + 1}", "name": body["name"]}
                self.foods.append(food)
                return httpx.Response(201, json=food)
            case "GET", "/recipes/truffade":
                return httpx.Response(200, json=self.recipe)
            case "PATCH", "/recipes/truffade":
                return httpx.Response(200, json=self.recipe | body)
            case "GET", "/households/shopping/lists":
                return httpx.Response(200, json={"items": self.lists})
            case "POST", "/households/shopping/items/create-bulk":
                return httpx.Response(201, json={})
        return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture
def fake():
    return FakeMealie()


@pytest.fixture
def mealie(fake):
    return MealieClient("http://mealie", "k", transport=httpx.MockTransport(fake))


async def test_create_recipe_reuses_and_creates_tags(fake, mealie):
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool(
            "create_recipe",
            {"name": "Tarte", "ingredients": ["3 pommes"], "instructions": ["Cuire"], "tags": ["dessert", "Automne"]},
        )
    assert not result.is_error
    assert result.structured_content["tags"] == ["Dessert", "Automne"]
    assert result.structured_content["ingredients"] == [{"note": "3 pommes"}]
    patch = next(b for m, p, b in fake.requests if m == "PATCH")
    assert [t["id"] for t in patch["tags"]] == ["t1", "t2"]
    assert patch["recipeInstructions"] == [{"text": "Cuire", "ingredientReferences": []}]


async def test_step_names_and_sections_survive_a_read_write_round_trip(fake, mealie):
    fake.recipes["tarte"] = {
        "slug": "tarte",
        "name": "Tarte",
        "recipeInstructions": [
            {"id": "s1", "title": "Pour la pâte", "summary": "Pétrir", "text": "Mélanger la farine"},
            {"id": "s2", "title": "", "summary": "", "text": "Étaler"},
        ],
    }
    async with Client(build_server(mealie)) as client:
        read = await client.call_tool("get_recipe", {"slug": "tarte"})
        steps = read.structured_content["instructions"]
        # Les étapes relues sont réémises telles quelles, avec une étape nommée ajoutée.
        await client.call_tool(
            "update_recipe",
            {"slug": "tarte", "instructions": [*steps, {"summary": "Cuisson", "text": "Enfourner"}]},
        )

    assert steps == [
        {"text": "Mélanger la farine", "summary": "Pétrir", "title": "Pour la pâte"},
        {"text": "Étaler"},
    ]
    patch = next(b for m, p, b in fake.requests if m == "PATCH")
    assert patch["recipeInstructions"] == [
        {"text": "Mélanger la farine", "ingredientReferences": [], "summary": "Pétrir", "title": "Pour la pâte"},
        {"text": "Étaler", "ingredientReferences": []},
        {"text": "Enfourner", "ingredientReferences": [], "summary": "Cuisson"},
    ]


async def test_servings_are_sent_as_number(fake, mealie):
    async with Client(build_server(mealie)) as client:
        created = await client.call_tool(
            "create_recipe", {"name": "Tarte", "ingredients": ["3 pommes"], "instructions": ["Cuire"], "servings": 4}
        )
        updated = await client.call_tool("update_recipe", {"slug": "tarte", "servings": 6})
    assert not created.is_error and not updated.is_error, (created.content, updated.content)
    assert created.structured_content["servings"] == 4
    patches = [b for m, p, b in fake.requests if m == "PATCH"]
    assert patches[0]["recipeServings"] == 4 and "recipeYield" not in patches[0]
    assert patches[1] == {"recipeServings": 6}


async def test_get_recipe_exposes_structured_ingredients(mealie):
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool("get_recipe", {"slug": "truffade"})
    assert result.structured_content["ingredients"] == [
        {
            "reference_id": "r1",
            "quantity": 400,
            "unit": "gramme",
            "food": "tomme fraîche",
            "note": "en fines lamelles",
            "display": "400 g tomme fraîche en fines lamelles",
        },
        # Ligne non analysée : pas de quantité exposée.
        {"reference_id": "r2", "note": "Sel, poivre", "display": "Sel, poivre"},
    ]


async def test_create_recipe_with_structured_ingredients(fake, mealie):
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool(
            "create_recipe",
            {
                "name": "Tarte",
                "ingredients": [
                    {"quantity": 250, "unit": "G", "food": "Tome fraîche", "note": "râpée"},
                    {"quantity": 3, "food": "pomme", "title": "Garniture"},
                    "Sel",
                ],
                "instructions": ["Cuire"],
            },
        )
    assert not result.is_error, result.content
    patch = next(b for m, p, b in fake.requests if m == "PATCH")
    first, second, third = patch["recipeIngredient"]
    assert first["unit"]["id"] == "u1"  # retrouvée par abréviation
    assert first["food"]["id"] == "f1"  # retrouvé par alias
    assert (first["quantity"], first["note"]) == (250, "râpée")
    assert second["food"] == {"id": "f2", "name": "pomme"}  # créé
    assert second["unit"] is None and second["title"] == "Garniture"
    assert third == {"note": "Sel"}
    assert not any(m == "POST" and p == "/units" for m, p, _ in fake.requests)


async def test_update_ingredients_changes_only_targeted_fields(fake, mealie):
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool(
            "update_ingredients", {"slug": "truffade", "changes": [{"reference_id": "r1", "quantity": 500}]}
        )
    assert not result.is_error, result.content
    patch = next(b for m, p, b in fake.requests if m == "PATCH")
    first, second = patch["recipeIngredient"]
    assert first["quantity"] == 500
    assert first["referenceId"] == "r1" and first["unit"]["id"] == "u1" and first["food"]["id"] == "f1"
    assert first["note"] == "en fines lamelles" and "display" not in first
    assert second == fake.recipe["recipeIngredient"][1]
    assert not any(p in ("/units", "/foods") for _, p, _ in fake.requests)


async def test_update_ingredients_unknown_reference(mealie):
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool(
            "update_ingredients", {"slug": "truffade", "changes": [{"reference_id": "zz", "quantity": 1}]}
        )
    assert result.is_error and "zz" in result.content[0].text


async def test_shopping_list_defaults_to_single_list(fake, mealie):
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool("add_shopping_items", {"items": ["Lait", " "]})
    assert result.structured_content == {"list": "Courses", "added": ["Lait"]}
    bulk = next(b for m, p, b in fake.requests if p.endswith("create-bulk"))
    assert bulk == [{"shoppingListId": "l1", "note": "Lait", "quantity": 0}]


async def test_shopping_list_ambiguous_when_several_lists(fake, mealie):
    fake.lists.append({"id": "l2", "name": "Bricolage"})
    async with Client(build_server(mealie)) as client:
        result = await client.call_tool("add_shopping_items", {"items": ["Lait"]})
    assert result.is_error
    assert "Plusieurs listes" in result.content[0].text


async def test_invalid_date_and_mealie_errors_are_tool_errors(mealie):
    async with Client(build_server(mealie)) as client:
        bad_date = await client.call_tool("add_meal_plan_entry", {"date": "demain", "title": "x"})
        missing = await client.call_tool("get_recipe", {"slug": "inconnue"})
    assert bad_date.is_error and "AAAA-MM-JJ" in bad_date.content[0].text
    assert missing.is_error and "HTTP 404" in missing.content[0].text


# --- Authentification HTTP ------------------------------------------------------------


@asynccontextmanager
async def http_client(mealie):
    settings = Settings(mealie_url="http://mealie", mealie_api_token="k", mcp_auth_token=TOKEN)
    app = create_app(settings, mealie)
    # Le transport ASGI de httpx n'exécute pas le lifespan ; on le déclenche via l'app Starlette.
    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
}
HEADERS = {"Accept": "application/json, text/event-stream"}


async def test_health_is_public(mealie):
    async with http_client(mealie) as http:
        assert (await http.get("/health")).status_code == 200


@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/mauvais-jeton", f"/mcp/{TOKEN}x"])
async def test_mcp_requires_token(mealie, path):
    async with http_client(mealie) as http:
        assert (await http.post(path, json=INIT, headers=HEADERS)).status_code == 401


async def test_unknown_path_is_404(mealie):
    async with http_client(mealie) as http:
        assert (await http.get("/admin")).status_code == 404


async def test_token_in_path(mealie):
    async with http_client(mealie) as http:
        resp = await http.post(f"/mcp/{TOKEN}", json=INIT, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "mealie"


async def test_token_in_bearer_header(mealie):
    async with http_client(mealie) as http:
        resp = await http.post("/mcp", json=INIT, headers=HEADERS | {"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200


def test_settings_require_auth_token(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "http://mealie")
    monkeypatch.setenv("MEALIE_API_TOKEN", "k")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_ALLOW_NO_AUTH", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("MCP_AUTH_TOKEN", "trop-court")
    with pytest.raises(ConfigError):
        Settings.from_env()
