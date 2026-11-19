"""Hardware shops, stage H1: settings, categories, products with alternate units, scanner lookup, search and CSV import."""

import asyncio
import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models.hardware import HardwareProduct, HardwareProductUnit
from app.modules.hardware.router import router as hardware_router
from tests.factories import auth_headers
from tests.modules.hardware_helpers import H, cable, category, make_shop, opt, product, set_category
from tests.modules.test_rls import count, probe  # noqa: F401  (fixture)

D = Decimal


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


# --- the category guard, roles and tenants -----------------------------------------------------------------------------------


def _routes():
    for route in hardware_router.routes:
        path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)
        for method in route.methods:
            yield method, f"/api/v1{path}"


@pytest.mark.parametrize("category_key", ["apparel", "restaurant", "grocery"])
async def test_other_categories_get_404_on_every_route(client, db, redis_client, shop, category_key):
    await set_category(db, redis_client, shop["org"].id, category_key)
    for method, url in _routes():
        r = await client.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None,
                                 params={"code": "x"}, headers=auth_headers(shop["owner"]))
        assert r.status_code == 404, (method, url, r.text)
        assert r.json()["error"]["code"] == "wrong_business_category"


async def test_cashiers_read_but_do_not_manage(client, shop):
    cat = await category(client, shop["owner"])
    made = await cable(client, shop["owner"], cat["id"])
    cashier = auth_headers(shop["cashier"])
    for url, params in ((f"{H}/products", {}), (f"{H}/products/{made['id']}", {}), (f"{H}/catalog/search", {"q": "wire"}),
                        (f"{H}/settings", {}), (f"{H}/categories", {}), (f"{H}/products/lookup", {"code": "8901000000901"})):
        assert (await client.get(url, params=params, headers=cashier)).status_code == 200, url
    writes = [
        ("PUT", f"{H}/settings", {"round_to_rupee": False}),
        ("POST", f"{H}/categories", {"name": "Pipes"}),
        ("PATCH", f"{H}/categories/{cat['id']}", {"name": "Wires"}),
        ("POST", f"{H}/products", {"category_id": cat["id"], "name": "X", "price": "1"}),
        ("PATCH", f"{H}/products/{made['id']}", {"name": "Y"}),
        ("PUT", f"{H}/products/{made['id']}/units", {"units": []}),
        ("POST", f"{H}/import", {"csv": "name,category,price\nA,B,10"}),
    ]
    for method, url, body in writes:
        r = await client.request(method, url, json=body, headers=cashier)
        assert r.status_code == 403, (method, url, r.text)


async def test_another_business_cannot_see_or_change_the_catalogue(client, shop, other):
    cat = await category(client, shop["owner"])
    made = await cable(client, shop["owner"], cat["id"], sku="CW-25")
    them = auth_headers(other["owner"])
    assert (await client.get(f"{H}/products/{made['id']}", headers=them)).status_code == 404
    assert (await client.patch(f"{H}/products/{made['id']}", json={"name": "x"}, headers=them)).status_code == 404
    assert (await client.put(f"{H}/products/{made['id']}/units", json={"units": []}, headers=them)).status_code == 404
    assert (await client.patch(f"{H}/categories/{cat['id']}", json={"name": "x"}, headers=them)).status_code == 404
    r = await client.post(f"{H}/products", json={"category_id": cat["id"], "name": "x", "price": "1"}, headers=them)
    assert r.status_code == 404 and r.json()["error"]["code"] == "category_not_found"
    for code in ("8901000000901", "CW-25"):
        r = await client.get(f"{H}/products/lookup", params={"code": code}, headers=them)
        assert r.status_code == 404 and r.json()["error"]["code"] == "barcode_not_found"
    assert (await client.get(f"{H}/products", headers=them)).json()["total"] == 0
    assert (await client.get(f"{H}/catalog/search", params={"q": "wire"}, headers=them)).json() == []
    # Codes are unique per business, not across businesses.
    their_cat = await category(client, other["owner"])
    await cable(client, other["owner"], their_cat["id"], sku="CW-25")


async def test_rls_covers_every_catalogue_table(client, db, probe, shop, other):  # noqa: F811
    for s in (shop, other):
        cat = await category(client, s["owner"])
        await cable(client, s["owner"], cat["id"])
        assert (await client.get(f"{H}/settings", headers=auth_headers(s["owner"]))).status_code == 200
    async with probe() as session:
        for table in ("hardware_settings", "hardware_categories", "hardware_products", "hardware_product_units"):
            everything = await count(session, table)
            await session.execute(text("RESET app.org_scope"))
            mine = await count(session, table, shop["org"].id)
            theirs = await count(session, table, other["org"].id)
            assert 0 < mine < everything and 0 < theirs < everything and mine + theirs == everything, table
            await session.execute(text("RESET app.org_scope"))


# --- settings and categories -------------------------------------------------------------------------------------------------


async def test_settings_default_and_update(client, shop):
    owner = auth_headers(shop["owner"])
    r = await client.get(f"{H}/settings", headers=owner)
    assert r.json() == {"prices_include_tax": True, "round_to_rupee": True, "default_tax_rate": "18.00",
                        "estimate_valid_days": 15, "state_code": None}
    r = await client.put(f"{H}/settings", json={"state_code": "29", "estimate_valid_days": 7, "prices_include_tax": False},
                         headers=owner)
    assert r.status_code == 200 and r.json()["state_code"] == "29" and r.json()["estimate_valid_days"] == 7
    assert r.json()["prices_include_tax"] is False and r.json()["round_to_rupee"] is True
    r = await client.put(f"{H}/settings", json={"state_code": None}, headers=owner)
    assert r.status_code == 200 and r.json()["state_code"] is None
    r = await client.put(f"{H}/settings", json={"round_to_rupee": None}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_setting"
    for bad in ({"state_code": "KA"}, {"state_code": "290"}, {"default_tax_rate": "41"}, {"estimate_valid_days": 0}):
        assert (await client.put(f"{H}/settings", json=bad, headers=owner)).status_code == 422, bad


async def test_categories(client, shop):
    owner = auth_headers(shop["owner"])
    pipes = await category(client, shop["owner"], "Pipes")
    await category(client, shop["owner"], "Cables")
    r = await client.post(f"{H}/categories", json={"name": "pipes"}, headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_category"
    r = await client.patch(f"{H}/categories/{pipes['id']}", json={"is_active": False, "sort_order": 3}, headers=owner)
    assert r.status_code == 200 and r.json()["is_active"] is False
    names = [c["name"] for c in (await client.get(f"{H}/categories", headers=owner)).json()]
    assert names == ["Cables"]
    everything = (await client.get(f"{H}/categories", params={"include_inactive": True}, headers=owner)).json()
    assert len(everything) == 2
    r = await client.post(f"{H}/products", json={"category_id": pipes["id"], "name": "x", "price": "1"}, headers=owner)
    assert r.status_code == 404  # an inactive category takes no new products


# --- products and units ------------------------------------------------------------------------------------------------------


async def test_a_product_with_a_base_unit_and_two_alternate_units_reads_back(client, shop):
    cat = await category(client, shop["owner"])
    made = await cable(client, shop["owner"], cat["id"], hsn_code="8544", brand="Finolex", wholesale_price="40.5",
                       min_price="39.0000", mrp="48")
    assert made["base_unit"] == "m" and made["allow_fraction"] is True  # metres are cut: fractions by default
    assert D(made["tax_rate"]) == D("18") and D(made["price"]) == D("42") and made["category_name"] == "Cables"
    assert [(u["unit"], D(u["factor"])) for u in made["units"]] == [("bundle", D("10")), ("coil", D("90"))]
    again = (await client.get(f"{H}/products/{made['id']}", headers=auth_headers(shop["cashier"]))).json()
    assert again == made

    assert [o["unit"] for o in made["options"]] == ["m", "bundle", "coil"]
    metre, bundle, coil = opt(made, "m"), opt(made, "bundle"), opt(made, "coil")
    assert metre["is_base"] and D(metre["factor"]) == 1 and metre["barcode"] is None
    assert (D(metre["retail_price"]), D(metre["wholesale_price"]), D(metre["retail_floor"]), D(metre["mrp"])) == \
        (D("42.00"), D("40.50"), D("39.00"), D("48.00"))
    assert (D(bundle["retail_price"]), D(bundle["wholesale_price"]), D(bundle["retail_floor"])) == \
        (D("420.00"), D("405.00"), D("390.00"))
    # The coil has its own price, lower than 90 m; wholesale (40.50 x 90 = 3,645) and the floor are capped at it.
    assert (D(coil["retail_price"]), D(coil["wholesale_price"]), D(coil["retail_floor"]), D(coil["mrp"])) == \
        (D("3500.00"), D("3500.00"), D("3500.00"), D("4320.00"))
    assert coil["barcode"] == "8901000000901"


async def test_defaults_come_from_settings_and_the_unit(client, shop):
    owner = auth_headers(shop["owner"])
    await client.put(f"{H}/settings", json={"default_tax_rate": "12"}, headers=owner)
    cat = await category(client, shop["owner"], "Fasteners")
    screws = await product(client, shop["owner"], cat["id"], name="Wood Screw", size_spec="8 x 1 inch", price="1.5",
                           units=[{"unit": "box", "factor": "100", "barcode": "BOX-WS-8"}])
    assert screws["base_unit"] == "pcs" and screws["allow_fraction"] is False and D(screws["tax_rate"]) == D("12")
    assert D(opt(screws, "box")["retail_price"]) == D("150.00") and opt(screws, "box")["retail_floor"] is None
    tiny = await product(client, shop["owner"], cat["id"], name="Washer", price="0.0450", allow_fraction=True, tax_rate="18")
    assert D(tiny["price"]) == D("0.0450") and D(opt(tiny, "pcs")["retail_price"]) == D("0.05")
    assert tiny["allow_fraction"] is True and D(tiny["tax_rate"]) == D("18")


async def test_product_validation(client, shop):
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])
    base = {"category_id": cat["id"], "name": "Wire", "price": "42", "base_unit": "m"}
    bad = [
        {**base, "units": [{"unit": "m", "factor": "1"}]},  # an alternate unit cannot be the base unit
        {**base, "units": [{"unit": "coil", "factor": "90"}, {"unit": "coil", "factor": "100"}]},
        {**base, "units": [{"unit": "coil", "factor": "0"}]},
        {**base, "units": [{"unit": "crate", "factor": "2"}]},
        {**base, "price": "1.00001"},  # 4 decimals at most per base unit
        {**base, "units": [{"unit": "coil", "factor": "90", "price": "3500.001"}]},  # unit prices are money
        {**base, "tax_rate": "41"},
        {**base, "hsn_code": "85"},
        {**base, "sku": "has space"},
        {**base, "price": "-1"},
        {**base, "base_unit": "furlong"},
    ]
    for body in bad:
        assert (await client.post(f"{H}/products", json=body, headers=owner)).status_code == 422, body
    r = await client.post(f"{H}/products", json={**base, "category_id": str(uuid.uuid4())}, headers=owner)
    assert r.status_code == 404 and r.json()["error"]["code"] == "category_not_found"


async def test_codes_are_unique_across_products_and_units(client, shop):
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])
    wire = await cable(client, shop["owner"], cat["id"], sku="CW-25", barcode="8901000000025")
    base = {"category_id": cat["id"], "name": "Elbow", "price": "12"}
    for body, code in (
        ({**base, "barcode": "8901000000901"}, "duplicate_barcode"),  # the wire's coil barcode
        ({**base, "units": [{"unit": "box", "factor": "10", "barcode": "8901000000025"}]}, "duplicate_barcode"),
        ({**base, "barcode": "X1", "units": [{"unit": "box", "factor": "10", "barcode": "X1"}]}, "duplicate_barcode"),
        ({**base, "units": [{"unit": "box", "factor": "10", "barcode": "X1"}, {"unit": "pkt", "factor": "5", "barcode": "X1"}]},
         "duplicate_barcode"),
        ({**base, "sku": "CW-25"}, "duplicate_sku"),
    ):
        r = await client.post(f"{H}/products", json=body, headers=owner)
        assert r.status_code == 409 and r.json()["error"]["code"] == code, (body, r.text)
    elbow = await product(client, shop["owner"], cat["id"], name="Elbow", price="12", barcode="EL-1")
    r = await client.patch(f"{H}/products/{elbow['id']}", json={"barcode": "8901000000901"}, headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_barcode"
    r = await client.put(f"{H}/products/{elbow['id']}/units", json={"units": [{"unit": "box", "factor": "10",
                                                                              "barcode": "8901000000025"}]}, headers=owner)
    assert r.status_code == 409
    r = await client.put(f"{H}/products/{elbow['id']}/units", json={"units": [{"unit": "box", "factor": "10",
                                                                              "barcode": "EL-1"}]}, headers=owner)
    assert r.status_code == 409  # the product's own base-unit barcode
    # Saving a product with its own codes unchanged is fine.
    r = await client.patch(f"{H}/products/{wire['id']}", json={"barcode": "8901000000025", "sku": "CW-25"}, headers=owner)
    assert r.status_code == 200, r.text
    r = await client.put(f"{H}/products/{wire['id']}/units", json={"units": wire["units"]}, headers=owner)
    assert r.status_code == 200, r.text


async def test_concurrent_saves_never_share_a_barcode(client, shop):
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])

    async def make(i):
        body = {"category_id": cat["id"], "name": f"Item {i}", "price": "1",
                "barcode" if i % 2 else "units": "SAME-1" if i % 2 else [{"unit": "box", "factor": "10", "barcode": "SAME-1"}]}
        return await client.post(f"{H}/products", json=body, headers=owner)

    results = await asyncio.gather(*(make(i) for i in range(6)))
    assert sorted(r.status_code for r in results) == [201] + [409] * 5, [r.text for r in results]


async def test_replacing_units_adds_updates_and_removes(client, db, shop):
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])
    wire = await cable(client, shop["owner"], cat["id"])
    coil_id = next(u["id"] for u in wire["units"] if u["unit"] == "coil")
    body = {"units": [{"unit": "coil", "factor": "100", "price": "3900.00", "wholesale_price": "3700.00", "min_price": "3600",
                       "barcode": "8901000000901"},
                      {"unit": "roll", "factor": "45"}]}
    r = await client.put(f"{H}/products/{wire['id']}/units", json=body, headers=owner)
    assert r.status_code == 200, r.text
    units = {u["unit"]: u for u in r.json()["units"]}
    assert set(units) == {"coil", "roll"} and units["coil"]["id"] == coil_id  # updated in place, bundle removed
    assert D(units["coil"]["factor"]) == 100 and D(opt(r.json(), "coil")["wholesale_floor"]) == D("3600")
    assert await db.scalar(select(func.count()).select_from(HardwareProductUnit)) == 2
    r = await client.put(f"{H}/products/{wire['id']}/units", json={"units": [{"unit": "m", "factor": "1"}]}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unit_is_base"
    r = await client.put(f"{H}/products/{wire['id']}/units", json={"units": []}, headers=owner)
    assert r.status_code == 200 and r.json()["units"] == [] and [o["unit"] for o in r.json()["options"]] == ["m"]
    # A freed barcode can be used again.
    elbow = await product(client, shop["owner"], cat["id"], name="Elbow", price="1", barcode="8901000000901")
    assert elbow["barcode"] == "8901000000901"


async def test_update_product(client, shop):
    cat = await category(client, shop["owner"])
    pipes = await category(client, shop["owner"], "Pipes")
    owner = auth_headers(shop["owner"])
    wire = await cable(client, shop["owner"], cat["id"], brand="Finolex", mrp="50")
    r = await client.patch(f"{H}/products/{wire['id']}", json={"name": "Copper Wire FR", "category_id": pipes["id"],
                                                               "brand": None, "mrp": None, "price": "44.1250",
                                                               "allow_fraction": False}, headers=owner)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Copper Wire FR" and body["category_name"] == "Pipes" and body["brand"] is None
    assert body["mrp"] is None and D(opt(body, "m")["retail_price"]) == D("44.13") and body["allow_fraction"] is False
    assert D(opt(body, "bundle")["retail_price"]) == D("441.25")
    for bad in ({"name": None}, {"price": None}, {"base_unit": None}, {"tax_rate": None}):
        r = await client.patch(f"{H}/products/{wire['id']}", json=bad, headers=owner)
        assert r.status_code == 422, bad
    r = await client.patch(f"{H}/products/{wire['id']}", json={"base_unit": "coil"}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "unit_is_base"
    r = await client.patch(f"{H}/products/{wire['id']}", json={"base_unit": "ft"}, headers=owner)
    assert r.status_code == 200 and r.json()["base_unit"] == "ft"
    r = await client.patch(f"{H}/products/{wire['id']}", json={"category_id": str(uuid.uuid4())}, headers=owner)
    assert r.status_code == 404


async def test_product_list_is_paged_and_filtered(client, shop):
    cables = await category(client, shop["owner"])
    pipes = await category(client, shop["owner"], "Pipes")
    owner = auth_headers(shop["owner"])
    for i in range(5):
        await product(client, shop["owner"], cables["id"], name=f"Wire {i}", price="1")
    gone = await product(client, shop["owner"], pipes["id"], name="PVC Pipe", price="1")
    await client.patch(f"{H}/products/{gone['id']}", json={"is_active": False}, headers=owner)
    r = (await client.get(f"{H}/products", params={"limit": 2, "offset": 2}, headers=owner)).json()
    assert r["total"] == 5 and [p["name"] for p in r["items"]] == ["Wire 2", "Wire 3"]
    assert (await client.get(f"{H}/products", params={"include_inactive": True}, headers=owner)).json()["total"] == 6
    r = (await client.get(f"{H}/products", params={"category_id": pipes["id"], "include_inactive": True}, headers=owner)).json()
    assert [p["name"] for p in r["items"]] == ["PVC Pipe"]


# --- the counter: lookup and search --------------------------------------------------------------------------------------------


async def test_a_box_barcode_looks_up_the_box(client, shop):
    cat = await category(client, shop["owner"], "Fasteners")
    screws = await product(client, shop["owner"], cat["id"], name="Wood Screw", price="1.5", barcode="8902000000001",
                           sku="WS-8", units=[{"unit": "box", "factor": "100", "barcode": "8902000000100"},
                                              {"unit": "pkt", "factor": "10"}])
    cashier = auth_headers(shop["cashier"])

    async def look(code):
        return await client.get(f"{H}/products/lookup", params={"code": code}, headers=cashier)

    r = await look("8902000000100")
    assert r.status_code == 200 and r.json()["product"]["id"] == screws["id"]
    assert r.json()["unit"]["unit"] == "box" and D(r.json()["unit"]["factor"]) == 100 and not r.json()["unit"]["is_base"]
    assert D(r.json()["unit"]["retail_price"]) == D("150.00")
    r = await look("8902000000001")
    assert r.json()["unit"]["unit"] == "pcs" and r.json()["unit"]["is_base"]
    r = await look(" WS-8 ")
    assert r.status_code == 200 and r.json()["unit"]["unit"] == "pcs"
    r = await look("nothing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "barcode_not_found"
    await client.patch(f"{H}/products/{screws['id']}", json={"is_active": False}, headers=auth_headers(shop["owner"]))
    assert (await look("8902000000100")).status_code == 404


async def test_search(client, shop):
    cables = await category(client, shop["owner"])
    fittings = await category(client, shop["owner"], "Pipe Fittings")
    await cable(client, shop["owner"], cables["id"], brand="Finolex")
    await product(client, shop["owner"], cables["id"], name="Copper Wire", size_spec="1.5 sq mm", base_unit="m", price="30")
    await product(client, shop["owner"], fittings["id"], name="PVC Elbow", size_spec="1/2 inch", price="12", sku="PVC-E-12")
    await product(client, shop["owner"], fittings["id"], name="PVC Elbow", size_spec="3/4 inch", price="16",
                  units=[{"unit": "box", "factor": "25", "barcode": "ELB-BOX-34"}])
    cashier = auth_headers(shop["cashier"])

    async def find(**params):
        r = await client.get(f"{H}/catalog/search", params=params, headers=cashier)
        assert r.status_code == 200, r.text
        return [(p["name"], p["size_spec"]) for p in r.json()]

    assert await find(q="pvc elbow 1/2") == [("PVC Elbow", "1/2 inch")]
    assert await find(q="copper 2.5") == [("Copper Wire", "2.5 sq mm")]
    assert await find(q="finolex") == [("Copper Wire", "2.5 sq mm")]
    assert await find(q="pvc-e") == [("PVC Elbow", "1/2 inch")]
    assert await find(q="ELB-BOX-34") == [("PVC Elbow", "3/4 inch")]  # an alternate unit's barcode
    assert await find(q="8901000000901") == [("Copper Wire", "2.5 sq mm")]
    assert len(await find(category_id=fittings["id"])) == 2
    assert len(await find(limit=3)) == 3
    assert await find(q="nothing like it") == []
    assert (await client.get(f"{H}/catalog/search", params={"limit": 51}, headers=cashier)).status_code == 422


# --- CSV import --------------------------------------------------------------------------------------------------------------


HEADER = "name,category,brand,size,price,wholesale_price,min_price,mrp,gst_rate,hsn,unit,allow_fraction,barcode,sku," \
         "alt_unit,alt_factor,alt_price,alt_barcode"


async def _import(client, user, csv, **extra):
    r = await client.post(f"{H}/import", json={"csv": csv, **extra}, headers=auth_headers(user), timeout=120)
    assert r.status_code == 200, r.text
    return r.json()


async def test_import_creates_products_with_units(client, db, shop):
    csv = "\n".join([
        HEADER,
        "Copper Wire,Cables,Finolex,2.5 sq mm,42,40,39,48,18,8544,mtr,,,CW-25,coil,90,3500,8901000000901",
        "Wood Screw,Fasteners,,8 x 1 inch,1.5,,,,,,nos,,8902000000001,WS-8,box,100,,8902000000100",
        "Cement,Building,ACC,50 kg,0.1125,,,,28,2523,kg,no,,,bag,50,,",
        "PVC Elbow,Pipe Fittings,,1/2 inch,12.0000,,,,,,,,,,,,,",
    ])
    result = await _import(client, shop["owner"], csv)
    assert result == {"products_created": 4, "products_updated": 0, "units_created": 3, "units_updated": 0,
                      "categories_created": 4, "errors": [], "dry_run": False}
    owner = auth_headers(shop["owner"])
    wire = (await client.get(f"{H}/products/lookup", params={"code": "8901000000901"}, headers=owner)).json()
    assert wire["unit"]["unit"] == "coil" and D(wire["unit"]["retail_price"]) == D("3500")
    p = wire["product"]
    assert (p["base_unit"], p["allow_fraction"], p["brand"], p["size_spec"], p["hsn_code"]) == ("m", True, "Finolex", "2.5 sq mm",
                                                                                                "8544")
    assert (D(p["wholesale_price"]), D(p["min_price"]), D(p["mrp"]), D(p["tax_rate"])) == (D(40), D(39), D(48), D(18))
    screws = (await client.get(f"{H}/products/lookup", params={"code": "8902000000100"}, headers=owner)).json()
    assert screws["product"]["base_unit"] == "pcs" and screws["product"]["allow_fraction"] is False
    assert D(screws["unit"]["retail_price"]) == D("150.00")
    cement = (await client.get(f"{H}/catalog/search", params={"q": "cement"}, headers=owner)).json()[0]
    assert cement["allow_fraction"] is False and D(cement["tax_rate"]) == 28 and D(opt(cement, "bag")["retail_price"]) == D("5.63")
    elbow = (await client.get(f"{H}/catalog/search", params={"q": "elbow"}, headers=owner)).json()[0]
    assert elbow["base_unit"] == "pcs" and D(elbow["tax_rate"]) == D("18")  # the settings' default rate


async def test_import_matches_by_sku_then_barcode_then_name_and_size(client, shop):
    first = "\n".join([HEADER,
                       "Copper Wire,Cables,,2.5 sq mm,42,,,,,,m,,,CW-25,coil,90,3500,",
                       "PVC Elbow,Fittings,,1/2 inch,12,,,,,,,,ELB-12,,,,,",
                       "PVC Tee,Fittings,,1/2 inch,14,,,,12,,,,,,,,,"])
    await _import(client, shop["owner"], first)
    second = "\n".join([HEADER,
                        "Copper Wire FR,Cables,,2.5 sq mm,44,,,,,,,,,CW-25,coil,90,3600,COIL-25",  # by SKU, renamed
                        "PVC Elbow,Fittings,Supreme,1/2 inch,13,,,,,,,,ELB-12,,box,20,,",  # by barcode
                        "pvc tee,fittings,,1/2 INCH,15,,,,,,,,,,,,,",  # by category, name and size; gst kept
                        "PVC Tee,Fittings,,3/4 inch,18,,,,,,,,,,,,,"])  # another size: a new product
    result = await _import(client, shop["owner"], second)
    assert (result["products_created"], result["products_updated"], result["units_created"], result["units_updated"]) == (1, 3, 1, 1)
    assert result["errors"] == [] and result["categories_created"] == 0
    owner = auth_headers(shop["owner"])
    wire = (await client.get(f"{H}/products/lookup", params={"code": "COIL-25"}, headers=owner)).json()
    assert wire["product"]["name"] == "Copper Wire FR" and D(wire["unit"]["retail_price"]) == D("3600")
    assert wire["product"]["base_unit"] == "m"  # a blank unit keeps the product's
    tee = (await client.get(f"{H}/catalog/search", params={"q": "tee 1/2"}, headers=owner)).json()
    assert len(tee) == 1 and D(tee[0]["price"]) == D("15") and D(tee[0]["tax_rate"]) == D("12")
    total = (await client.get(f"{H}/products", headers=owner)).json()["total"]
    assert total == 4


async def test_import_reports_bad_rows_and_saves_the_rest(client, shop):
    cat = await category(client, shop["owner"])
    await cable(client, shop["owner"], cat["id"], sku="CW-25")
    csv = "\n".join([
        HEADER,
        "Good One,Misc,,,10,,,,,,,,,,,,,",
        ",Misc,,,10,,,,,,,,,,,,,",  # no name
        "Bad Price,Misc,,,ten,,,,,,,,,,,,,",
        "Too Precise,Misc,,,1.00001,,,,,,,,,,,,,",
        "Bad Unit,Misc,,,1,,,,,,crate,,,,,,,",
        "No Factor,Misc,,,1,,,,,,,,,,box,,,",
        "Orphan Factor,Misc,,,1,,,,,,,,,,,10,,",
        "Same Unit,Misc,,,1,,,,,,m,,,,mtr,10,,",
        "Taken Code,Misc,,,1,,,,,,,,8901000000901,,,,,",  # the cable's coil barcode
        "Taken Sku,Misc,,,1,,,,,,,,,CW-25,,,,",  # matches the cable by SKU: allowed, it updates it
        "Twice,Misc,,,1,,,,,,,,DUP-1,,,,,",
        "Twice Again,Misc,,,1,,,,,,,,DUP-1,,,,,",
        "Rate,Misc,,,1,,,,45,,,,,,,,,",
        "Flag,Misc,,,1,,,,,,,maybe,,,,,,",
    ])
    result = await _import(client, shop["owner"], csv)
    by_row = {e["row"]: e["errors"] for e in result["errors"]}
    assert sorted(by_row) == [3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15], by_row
    assert "name is missing" in by_row[3][0] and "price" in by_row[4][0] and "price" in by_row[5][0]
    assert "unit" in by_row[6][0] and "alt_factor" in by_row[7][0] and "alt_unit" in by_row[8][0]
    assert "same as the base unit" in by_row[9][0] and "already belongs" in by_row[10][0]
    assert "also on line 12" in by_row[13][0] and "gst_rate" in by_row[14][0] and "allow_fraction" in by_row[15][0]
    assert result["products_created"] == 2 and result["products_updated"] == 1  # Good One, Twice; the cable renamed
    names = {p["name"] for p in (await client.get(f"{H}/products", headers=auth_headers(shop["owner"]))).json()["items"]}
    assert names == {"Good One", "Twice", "Taken Sku"}


async def test_dry_run_saves_nothing(client, db, shop):
    csv = "\n".join([HEADER, "Copper Wire,Cables,,2.5 sq mm,42,,,,,,m,,,,coil,90,3500,"])
    result = await _import(client, shop["owner"], csv, dry_run=True)
    assert result["dry_run"] is True and result["products_created"] == 1 and result["units_created"] == 1
    assert await db.scalar(select(func.count()).select_from(HardwareProduct)) == 0
    assert (await client.get(f"{H}/categories", headers=auth_headers(shop["owner"]))).json() == []


async def test_a_thousand_row_import(client, db, shop):
    lines = ["name,category,size,price,unit,sku,alt_unit,alt_factor"]
    for i in range(1000):
        price = "abc" if i % 100 == 7 else f"{10 + i % 50}.25"
        lines.append(f"Item {i // 10},Cat {i % 5},Size {i % 10},{price},nos,SKU-{i},box,{10 + i % 3}")
    result = await _import(client, shop["owner"], "\n".join(lines))
    assert len(result["errors"]) == 10 and all("price" in e["errors"][0] for e in result["errors"])
    assert result["products_created"] == 990 and result["units_created"] == 990 and result["categories_created"] == 5
    assert await db.scalar(select(func.count()).select_from(HardwareProductUnit)) == 990


async def test_import_limits(client, shop):
    owner = auth_headers(shop["owner"])
    r = await client.post(f"{H}/import", json={"csv": "name,category\nA,B"}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "import_columns_missing"
    big = "name,category,price\n" + "\n".join(f"P{i},C,1" for i in range(10_001))
    r = await client.post(f"{H}/import", json={"csv": big}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "import_too_large"
