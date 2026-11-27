"""Dress shops, stage A1: settings, categories, styles in sizes and colours, in-store barcodes, scanner lookup, search, CSV import."""

import asyncio
import re
import uuid

import pytest
from sqlalchemy import select, text

from app.models.apparel import ApparelProduct, ApparelVariant
from app.modules.apparel.router import router as apparel_router
from app.modules.retailkit.barcode import is_ean13
from tests.factories import auth_headers
from tests.modules.apparel_helpers import A, category, make_shop, product, set_category
from tests.modules.test_rls import count, probe  # noqa: F401  (fixture)


@pytest.fixture
async def shop(db, redis_client):
    return await make_shop(db, redis_client)


@pytest.fixture
async def other(db, redis_client):
    return await make_shop(db, redis_client, slug="globex", code="GX1")


# --- the category guard, roles and tenants -----------------------------------------------------------------------------------


def _routes():
    for route in apparel_router.routes:
        path = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)
        for method in route.methods:
            yield method, f"/api/v1{path}"


@pytest.mark.parametrize("category_key", ["restaurant", "hardware", "grocery"])
async def test_other_categories_get_404_on_every_route(client, db, redis_client, shop, category_key):
    await set_category(db, redis_client, shop["org"].id, category_key)
    for method, url in _routes():
        r = await client.request(method, url, json={} if method in ("POST", "PUT", "PATCH") else None,
                                 params={"code": "x"}, headers=auth_headers(shop["owner"]))
        assert r.status_code == 404, (method, url, r.text)
        assert r.json()["error"]["code"] == "wrong_business_category"


async def test_cashiers_read_but_do_not_manage(client, shop):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"])
    cashier = auth_headers(shop["cashier"])
    assert (await client.get(f"{A}/products", headers=cashier)).status_code == 200
    assert (await client.get(f"{A}/catalog/search", params={"q": "oxford"}, headers=cashier)).status_code == 200
    assert (await client.get(f"{A}/settings", headers=cashier)).status_code == 200
    writes = [
        ("PUT", f"{A}/settings", {"return_days": 3}),
        ("POST", f"{A}/categories", {"name": "Kurtas"}),
        ("PATCH", f"{A}/categories/{cat['id']}", {"name": "Tops"}),
        ("POST", f"{A}/products", {"category_id": cat["id"], "name": "X", "sizes": ["M"], "price": "10.00"}),
        ("PATCH", f"{A}/products/{made['id']}", {"name": "Y"}),
        ("POST", f"{A}/products/{made['id']}/variants", {"sizes": ["XL"], "price": "10.00"}),
        ("PATCH", f"{A}/variants/{made['variants'][0]['id']}", {"price": "1.00"}),
        ("POST", f"{A}/import", {"csv": "name,category,size,price\nA,B,M,10"}),
    ]
    for method, url, body in writes:
        r = await client.request(method, url, json=body, headers=cashier)
        assert r.status_code == 403, (method, url, r.text)


async def test_another_business_cannot_see_or_change_the_catalogue(client, shop, other):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"])
    variant = made["variants"][0]
    them = auth_headers(other["owner"])
    assert (await client.get(f"{A}/products/{made['id']}", headers=them)).status_code == 404
    assert (await client.patch(f"{A}/products/{made['id']}", json={"name": "x"}, headers=them)).status_code == 404
    assert (await client.patch(f"{A}/variants/{variant['id']}", json={"price": "1.00"}, headers=them)).status_code == 404
    assert (await client.post(f"{A}/products/{made['id']}/variants", json={"sizes": ["XL"], "price": "1.00"},
                              headers=them)).status_code == 404
    assert (await client.patch(f"{A}/categories/{cat['id']}", json={"name": "x"}, headers=them)).status_code == 404
    r = await client.post(f"{A}/products", json={"category_id": cat["id"], "name": "x", "sizes": ["M"], "price": "1.00"},
                          headers=them)
    assert r.status_code == 404
    r = await client.get(f"{A}/variants/lookup", params={"code": variant["barcode"]}, headers=them)
    assert r.status_code == 404 and r.json()["error"]["code"] == "barcode_not_found"
    assert (await client.get(f"{A}/products", headers=them)).json()["total"] == 0
    assert (await client.get(f"{A}/catalog/search", params={"q": "Oxford"}, headers=them)).json() == []
    # Another branch id is refused as a stock source.
    r = await client.get(f"{A}/products/{made['id']}", params={"branch_id": str(other["branch"].id)},
                         headers=auth_headers(shop["owner"]))
    assert r.status_code == 404


async def test_rls_covers_every_catalogue_table(client, db, probe, shop, other):  # noqa: F811
    for s in (shop, other):
        cat = await category(client, s["owner"])
        await product(client, s["owner"], cat["id"])
        assert (await client.get(f"{A}/settings", headers=auth_headers(s["owner"]))).status_code == 200
    async with probe() as session:
        for table in ("apparel_settings", "apparel_categories", "apparel_products", "apparel_variants"):
            everything = await count(session, table)
            await session.execute(text("RESET app.org_scope"))
            mine = await count(session, table, shop["org"].id)
            theirs = await count(session, table, other["org"].id)
            assert 0 < mine < everything and 0 < theirs < everything and mine + theirs == everything, table
            await session.execute(text("RESET app.org_scope"))


# --- settings and categories -------------------------------------------------------------------------------------------------


async def test_settings_default_and_update(client, shop):
    owner = auth_headers(shop["owner"])
    r = await client.get(f"{A}/settings", headers=owner)
    assert r.status_code == 200
    assert r.json() == {"prices_include_tax": True, "round_to_rupee": True, "low_rate": "5.00", "slab_price": "2500.00",
                        "high_rate": "18.00", "return_days": 7}
    r = await client.put(f"{A}/settings", json={"slab_price": "1000.00", "high_rate": "12.00"}, headers=owner)
    assert r.status_code == 200 and r.json()["slab_price"] == "1000.00" and r.json()["high_rate"] == "12.00"
    assert r.json()["low_rate"] == "5.00"
    assert (await client.put(f"{A}/settings", json={"high_rate": "41"}, headers=owner)).status_code == 422
    assert (await client.put(f"{A}/settings", json={"low_rate": None}, headers=owner)).status_code == 422


async def test_categories(client, shop):
    owner = auth_headers(shop["owner"])
    shirts = await category(client, shop["owner"], "Shirts")
    r = await client.post(f"{A}/categories", json={"name": "  shirts "}, headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_category"
    r = await client.patch(f"{A}/categories/{shirts['id']}", json={"is_active": False}, headers=owner)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.get(f"{A}/categories", headers=owner)).json() == []
    assert len((await client.get(f"{A}/categories", params={"include_inactive": True}, headers=owner)).json()) == 1
    # A product cannot go into an inactive category.
    r = await client.post(f"{A}/products", json={"category_id": shirts["id"], "name": "X", "sizes": ["M"], "price": "1.00"},
                          headers=owner)
    assert r.status_code == 404 and r.json()["error"]["code"] == "category_not_found"


# --- products, variants and barcodes -----------------------------------------------------------------------------------------


async def test_four_sizes_by_three_colours_make_twelve_variants_with_unique_barcodes(client, db, shop):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"], sizes=["S", "M", "L", "XL"], colours=["Blue", "White", "Black"],
                         brand="Arrow", style_code="OX-01", hsn_code="6205")
    variants = made["variants"]
    assert len(variants) == 12
    codes = [v["barcode"] for v in variants]
    assert len(set(codes)) == 12 and all(c.startswith("29") and is_ean13(c) for c in codes)
    assert sorted(codes)[0] == "2900000000018"  # "29" + 0000000001; check: 2x1 + 9x3 + 1x3 = 32 -> 8
    # Sizes keep the order typed: every colour lists S, M, L, XL.
    blue = [v["size"] for v in variants if v["colour"] == "Blue"]
    assert blue == ["S", "M", "L", "XL"]
    # ...and colours keep theirs: the variants list colour by colour, each in size order.
    assert [(v["colour"], v["size"]) for v in variants][:5] == [("Blue", "S"), ("Blue", "M"), ("Blue", "L"), ("Blue", "XL"),
                                                                 ("White", "S")]
    assert list(dict.fromkeys(v["colour"] for v in variants)) == ["Blue", "White", "Black"]
    assert all(v["price"] == "999.00" and v["mrp"] == "1299.00" for v in variants)
    assert made["tax_mode"] == "slab" and made["tax_rate"] is None and made["category_name"] == "Shirts"

    # The next product continues the series.
    second = await product(client, shop["owner"], cat["id"], name="Polo", sizes=["M"])
    assert second["variants"][0]["barcode"] == "2900000000131"  # 2 + 27 + 1 + 3x3 = 39 -> 1


async def test_explicit_variants_keep_manufacturer_codes(client, shop):
    cat = await category(client, shop["owner"], "Footwear")
    body = {"category_id": cat["id"], "name": "Runner", "variants": [
        {"size": "8", "colour": "Black", "price": "2499.00", "mrp": "2999.00", "barcode": "8901234567890", "sku": "RUN-8"},
        {"size": "9", "colour": "Black", "price": "2499.00"},
    ]}
    r = await client.post(f"{A}/products", json=body, headers=auth_headers(shop["owner"]))
    assert r.status_code == 201, r.text
    by_size = {v["size"]: v for v in r.json()["variants"]}
    assert by_size["8"]["barcode"] == "8901234567890" and by_size["8"]["sku"] == "RUN-8"
    assert by_size["9"]["barcode"].startswith("29") and by_size["9"]["colour"] == "Black"


async def test_product_validation(client, shop):
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])
    base = {"category_id": cat["id"], "name": "X"}
    bad = [
        {**base, "sizes": ["M"], "price": "100.00", "mrp": "99.00"},  # price above MRP
        {**base},  # no sizes
        {**base, "sizes": ["M"]},  # no price
        {**base, "sizes": ["M"], "price": "1.00", "tax_mode": "fixed"},  # fixed without a rate
        {**base, "sizes": ["M"], "price": "1.00", "hsn_code": "12"},
        {**base, "sizes": [""], "price": "1.00"},
        {**base, "sizes": ["M"], "price": "1.00", "variants": [{"size": "L", "price": "1.00"}]},
        {**base, "variants": [{"size": "L", "price": "5.00", "mrp": "4.00"}]},
    ]
    for body in bad:
        assert (await client.post(f"{A}/products", json=body, headers=owner)).status_code == 422, body

    belt = await product(client, shop["owner"], cat["id"], name="Belt", sizes=["Free"], tax_mode="fixed", tax_rate="18.00")
    assert belt["tax_mode"] == "fixed" and belt["tax_rate"] == "18.00"
    r = await client.post(f"{A}/products", json={**base, "style_code": "S1", "sizes": ["M"], "price": "1.00"}, headers=owner)
    assert r.status_code == 201
    r = await client.post(f"{A}/products", json={**base, "style_code": "S1", "sizes": ["M"], "price": "1.00"}, headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_style_code"


async def test_update_product_and_switch_tax_mode(client, shop):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"])
    owner = auth_headers(shop["owner"])
    r = await client.patch(f"{A}/products/{made['id']}", json={"tax_mode": "fixed"}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "tax_rate_required"
    r = await client.patch(f"{A}/products/{made['id']}", json={"tax_mode": "fixed", "tax_rate": "12.00", "brand": "Arrow"},
                           headers=owner)
    assert r.status_code == 200 and r.json()["tax_rate"] == "12.00" and r.json()["brand"] == "Arrow"
    r = await client.patch(f"{A}/products/{made['id']}", json={"tax_mode": "slab"}, headers=owner)
    assert r.json()["tax_rate"] is None
    r = await client.patch(f"{A}/products/{made['id']}", json={"is_active": False}, headers=owner)
    assert r.json()["is_active"] is False
    assert (await client.get(f"{A}/products", headers=owner)).json()["total"] == 0
    assert (await client.get(f"{A}/products", params={"include_inactive": True}, headers=owner)).json()["total"] == 1


async def test_adding_and_changing_variants(client, shop):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"], sizes=["S", "M"], colours=["Blue"])
    owner = auth_headers(shop["owner"])
    # A grid skips what the style has: only White S/M and Blue L, White L are new.
    r = await client.post(f"{A}/products/{made['id']}/variants", json={"sizes": ["S", "M", "L"], "colours": ["Blue", "White"],
                                                                       "price": "999.00"}, headers=owner)
    assert r.status_code == 201, r.text
    variants = r.json()["variants"]
    assert len(variants) == 6
    assert [v["size"] for v in variants if v["colour"] == "White"] == ["S", "M", "L"]
    # A list must be new.
    r = await client.post(f"{A}/products/{made['id']}/variants", json={"variants": [{"size": "s", "colour": "Blue", "price": "1.00"}]},
                          headers=owner)
    assert r.status_code in (201, 409)  # sizes are case-sensitive in storage; "s" is a new size
    r = await client.post(f"{A}/products/{made['id']}/variants", json={"variants": [{"size": "S", "colour": "Blue", "price": "1.00"}]},
                          headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_variant"

    first, second = variants[0], variants[1]
    r = await client.patch(f"{A}/variants/{first['id']}", json={"price": "899.00", "sku": "OX-S-B"}, headers=owner)
    assert r.status_code == 200
    changed = next(v for v in r.json()["variants"] if v["id"] == first["id"])
    assert changed["price"] == "899.00" and changed["sku"] == "OX-S-B"
    r = await client.patch(f"{A}/variants/{first['id']}", json={"price": "1500.00"}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_above_mrp"
    r = await client.patch(f"{A}/variants/{second['id']}", json={"barcode": first["barcode"]}, headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_barcode"
    r = await client.patch(f"{A}/variants/{second['id']}", json={"sku": "OX-S-B"}, headers=owner)
    assert r.status_code == 409 and r.json()["error"]["code"] == "duplicate_sku"


async def test_concurrent_creates_never_share_a_barcode(client, shop):
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])

    async def make(i):
        return await client.post(f"{A}/products", json={"category_id": cat["id"], "name": f"P{i}", "sizes": ["S", "M", "L"],
                                                         "price": "100.00"}, headers=owner)

    results = await asyncio.gather(*(make(i) for i in range(6)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    codes = [v["barcode"] for r in results for v in r.json()["variants"]]
    assert len(codes) == 18 == len(set(codes))


# --- the counter: lookup and search ------------------------------------------------------------------------------------------


async def test_lookup_by_barcode_then_sku(client, db, shop):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"], sizes=["M", "L"])
    m, large = made["variants"]
    owner = auth_headers(shop["owner"])
    await client.patch(f"{A}/variants/{large['id']}", json={"sku": m["barcode"]}, headers=owner)  # a SKU equal to M's barcode

    r = await client.get(f"{A}/variants/lookup", params={"code": m["barcode"]}, headers=auth_headers(shop["cashier"]))
    assert r.status_code == 200 and r.json()["variant"]["id"] == m["id"]  # the barcode match wins
    assert r.json()["product"]["id"] == made["id"] and len(r.json()["product"]["variants"]) == 2
    await client.patch(f"{A}/variants/{large['id']}", json={"sku": "OX-L"}, headers=owner)
    r = await client.get(f"{A}/variants/lookup", params={"code": " OX-L "}, headers=owner)
    assert r.status_code == 200 and r.json()["variant"]["id"] == large["id"]
    r = await client.get(f"{A}/variants/lookup", params={"code": m["barcode"], "branch_id": str(shop["branch"].id)},
                         headers=owner)
    assert r.json()["variant"]["on_hand"] == 0

    await client.patch(f"{A}/variants/{m['id']}", json={"is_active": False}, headers=owner)
    r = await client.get(f"{A}/variants/lookup", params={"code": m["barcode"]}, headers=owner)
    assert r.status_code == 404 and r.json()["error"]["code"] == "barcode_not_found"
    await client.patch(f"{A}/products/{made['id']}", json={"is_active": False}, headers=owner)
    assert (await client.get(f"{A}/variants/lookup", params={"code": "OX-L"}, headers=owner)).status_code == 404


async def test_search(client, shop):
    shirts = await category(client, shop["owner"], "Shirts")
    kurtas = await category(client, shop["owner"], "Kurtas")
    await product(client, shop["owner"], shirts["id"], name="Oxford Shirt", brand="Arrow", style_code="OX-1")
    await product(client, shop["owner"], kurtas["id"], name="Silk Kurta", brand="Fabindia", style_code="KU-9")
    owner = auth_headers(shop["cashier"])

    async def names(**params):
        r = await client.get(f"{A}/catalog/search", params=params, headers=owner)
        assert r.status_code == 200, r.text
        return [p["name"] for p in r.json()]

    assert await names(q="oxford") == ["Oxford Shirt"]
    assert await names(q="fabindia") == ["Silk Kurta"]
    assert await names(q="ku-9") == ["Silk Kurta"]
    assert await names(category_id=kurtas["id"]) == ["Silk Kurta"]
    assert await names() == ["Oxford Shirt", "Silk Kurta"]
    assert await names(q="%") == ["Oxford Shirt", "Silk Kurta"]  # wildcards are not special
    r = await client.get(f"{A}/catalog/search", params={"limit": 51}, headers=owner)
    assert r.status_code == 422
    r = await client.get(f"{A}/catalog/search", params={"q": "oxford", "branch_id": str(shop["branch"].id)}, headers=owner)
    assert all(v["on_hand"] == 0 for v in r.json()[0]["variants"])


async def test_product_list_is_paged(client, shop):
    cat = await category(client, shop["owner"])
    for i in range(5):
        await product(client, shop["owner"], cat["id"], name=f"Style {i}", sizes=["M"])
    r = await client.get(f"{A}/products", params={"limit": 2, "offset": 2}, headers=auth_headers(shop["owner"]))
    body = r.json()
    assert body["total"] == 5 and [p["name"] for p in body["items"]] == ["Style 2", "Style 3"]
    r = await client.get(f"{A}/products", params={"search": "style 4"}, headers=auth_headers(shop["owner"]))
    assert r.json()["total"] == 1


# --- CSV import --------------------------------------------------------------------------------------------------------------

HEADER = "style_code,name,category,brand,hsn,size,colour,mrp,price,barcode,sku,gst_rate,opening_stock"


async def test_import_groups_rows_into_styles(client, db, shop):
    csv = "\n".join([
        HEADER,
        "OX-1,Oxford Shirt,Shirts,Arrow,6205,S,Blue,1299,999,,OX-S-B,,",
        "OX-1,Oxford Shirt,Shirts,Arrow,6205,M,Blue,1299,999,,OX-M-B,,",
        "OX-1,Oxford Shirt,Shirts,Arrow,6205,M,White,1299,999,8901234500011,,,",
        ",Leather Belt,Accessories,,4203,Free,,,499,,,18,",
        ",Leather Belt,Accessories,,4203,Free,Brown,,499,,,18,",
    ])
    owner = auth_headers(shop["owner"])
    dry = await client.post(f"{A}/import", json={"csv": csv, "dry_run": True}, headers=owner)
    assert dry.status_code == 200, dry.text
    assert dry.json()["products_created"] == 2 and dry.json()["variants_created"] == 5 and dry.json()["dry_run"] is True
    assert (await db.scalar(select(ApparelProduct.id).limit(1))) is None  # nothing saved

    r = await client.post(f"{A}/import", json={"csv": csv}, headers=owner)
    body = r.json()
    assert body["errors"] == [] and body["products_created"] == 2 and body["variants_created"] == 5
    assert body["categories_created"] == 2
    products = {p["name"]: p for p in (await client.get(f"{A}/products", headers=owner)).json()["items"]}
    shirt, belt = products["Oxford Shirt"], products["Leather Belt"]
    assert shirt["style_code"] == "OX-1" and shirt["tax_mode"] == "slab" and shirt["hsn_code"] == "6205"
    assert belt["tax_mode"] == "fixed" and belt["tax_rate"] == "18.00"
    assert [(v["size"], v["colour"]) for v in belt["variants"]] == [("Free", "Default"), ("Free", "Brown")]
    white = next(v for v in shirt["variants"] if v["colour"] == "White")
    assert white["barcode"] == "8901234500011"
    assert all(v["barcode"] for v in shirt["variants"])

    # Importing again updates by SKU, then barcode, then (product, size, colour); nothing new is made.
    again = csv.replace("1299,999,,OX-S-B", "1399,1099,,OX-S-B")
    r = await client.post(f"{A}/import", json={"csv": again}, headers=owner)
    assert r.json()["variants_created"] == 0 and r.json()["variants_updated"] == 5 and r.json()["products_created"] == 0
    s = await db.scalar(select(ApparelVariant).where(ApparelVariant.sku == "OX-S-B"))
    assert s.price == 1099 and s.mrp == 1399


async def test_import_reports_bad_rows_and_saves_the_rest(client, shop):
    csv = "\n".join([
        HEADER,
        "OX-1,Oxford Shirt,Shirts,,,S,Blue,1299,999,,SKU1,,",
        "OX-1,Oxford Shirt,Shirts,,,S,Blue,1299,999,,,,",  # same size and colour as line 2
        ",No price,Shirts,,,M,,,,,,,",  # price missing
        ",Too dear,Shirts,,,M,,100,200,,,,",  # price above MRP
        ",Bad hsn,Shirts,,12,M,,,10,,,,",
        ",Dup sku,Shirts,,,M,,,10,,SKU1,,",
        "OX-1,Oxford Shirt,Shirts,,,L,Blue,1299,999,,,5,",  # gst differs from line 2 of the same style
        ",Stocked,Shirts,,,M,,,10,,,,4",  # opening stock arrives with stage A2
        ",Fine,Shirts,,,M,,,10,,,,",
    ])
    r = await client.post(f"{A}/import", json={"csv": csv}, headers=auth_headers(shop["owner"]))
    body = r.json()
    assert [e["row"] for e in body["errors"]] == [3, 4, 5, 6, 7, 8, 9]
    assert "also on line 2" in body["errors"][0]["errors"][0]
    assert body["products_created"] == 2 and body["variants_created"] == 2


async def test_codes_do_not_move_between_variants(client, shop):
    cat = await category(client, shop["owner"])
    made = await product(client, shop["owner"], cat["id"], name="Oxford Shirt", sizes=["M", "L"])
    m = made["variants"][0]
    csv = f"name,category,size,colour,price,barcode\nOxford Shirt,Shirts,L,Blue,999,{m['barcode']}"
    r = await client.post(f"{A}/import", json={"csv": csv}, headers=auth_headers(shop["owner"]))
    assert r.status_code == 200 and r.json()["errors"][0]["row"] == 2
    assert "already another variant" in r.json()["errors"][0]["errors"][0]


async def test_a_thousand_row_import(client, shop):
    lines = [HEADER]
    for i in range(1000):
        if i % 100 == 7:
            lines.append(f"ST-{i // 10},Style {i // 10},Cat {i % 5},,,{i % 10},Blue,500,abc,,,,")  # bad price
        else:
            lines.append(f"ST-{i // 10},Style {i // 10},Cat {i % 5},Brand,6109,{i % 10},Blue,500,450,,SKU-{i},,")
    r = await client.post(f"{A}/import", json={"csv": "\n".join(lines)}, headers=auth_headers(shop["owner"]),
                          timeout=120)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["errors"]) == 10 and all("price" in e["errors"][0] for e in body["errors"])
    assert body["variants_created"] == 990 and body["products_created"] == 100
    total = (await client.get(f"{A}/products", params={"limit": 1}, headers=auth_headers(shop["owner"]))).json()["total"]
    assert total == 100


async def test_import_limits(client, shop):
    owner = auth_headers(shop["owner"])
    r = await client.post(f"{A}/import", json={"csv": "name,category\nA,B"}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "import_columns_missing"
    big = "name,category,size,price\n" + "\n".join(f"P{i},C,M,1" for i in range(10_001))
    r = await client.post(f"{A}/import", json={"csv": big}, headers=owner)
    assert r.status_code == 422 and r.json()["error"]["code"] == "import_too_large"
    r = await client.post(f"{A}/import", json={"csv": "name,category,size,price\nA,B,M,1", "branch_id": str(uuid.uuid4())},
                          headers=owner)
    assert r.status_code == 403


async def test_a_blank_hsn_means_none_and_a_bad_one_names_the_field(client, shop):
    """The admin form sends "" for an empty HSN box: that is no HSN code, not an error."""
    cat = await category(client, shop["owner"])
    owner = auth_headers(shop["owner"])
    body = {"category_id": cat["id"], "name": "Plain kurta", "sizes": ["M"], "price": "499.00", "hsn_code": ""}
    r = await client.post(f"{A}/products", json=body, headers=owner)
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["hsn_code"] is None
    r = await client.patch(f"{A}/products/{made['id']}", json={"hsn_code": " 6211 "}, headers=owner)
    assert r.status_code == 200 and r.json()["hsn_code"] == "6211"
    r = await client.patch(f"{A}/products/{made['id']}", json={"hsn_code": ""}, headers=owner)  # cleared again
    assert r.status_code == 200 and r.json()["hsn_code"] is None
    r = await client.post(f"{A}/products", json={**body, "hsn_code": "62"}, headers=owner)
    assert r.status_code == 422
    error = r.json()["detail"][0]
    assert error["loc"][-1] == "hsn_code" and "HSN code must be 4 to 8 digits" in error["msg"]
