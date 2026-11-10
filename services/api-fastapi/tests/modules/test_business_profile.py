"""The business's own name and logo on its bills: who may set them, what is accepted, and what the bill prints."""

import base64

from tests import factories
from tests.factories import auth_headers
from tests.flows import API, bill, create_order, fire, line

PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
URL = f"{API}/business"


async def test_owner_and_manager_set_the_bill_name_and_logo(client, branch, owner, manager, cashier):
    start = (await client.get(f"{URL}/profile", headers=auth_headers(cashier))).json()
    assert start["name"] == start["registered_name"] and start["display_name"] is None and start["logo_data_url"] is None

    r = await client.patch(f"{URL}/profile", json={"display_name": "  Spice Route Kitchen  "}, headers=auth_headers(manager))
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Spice Route Kitchen" and r.json()["registered_name"] == start["registered_name"]

    r = await client.put(f"{URL}/logo", json={"image_base64": f"data:image/png;base64,{PNG_1PX}"}, headers=auth_headers(owner))
    assert r.status_code == 200, r.text
    assert r.json()["logo_data_url"] == f"data:image/png;base64,{PNG_1PX}"

    # Everyone can read it (the counter prints it); only owners and managers change it.
    assert (await client.get(f"{URL}/profile", headers=auth_headers(cashier))).json()["logo_data_url"]
    assert (await client.patch(f"{URL}/profile", json={"display_name": "Nope"}, headers=auth_headers(cashier))).status_code == 403
    assert (await client.delete(f"{URL}/logo", headers=auth_headers(cashier))).status_code == 403

    r = await client.delete(f"{URL}/logo", headers=auth_headers(manager))
    assert r.json()["logo_data_url"] is None
    r = await client.patch(f"{URL}/profile", json={"display_name": None}, headers=auth_headers(manager))
    assert r.json()["name"] == start["registered_name"]


async def test_only_small_png_or_jpeg_logos_are_accepted(client, branch, owner):
    gif = base64.b64encode(b"GIF89a" + b"\x00" * 20).decode()
    r = await client.put(f"{URL}/logo", json={"image_base64": gif}, headers=auth_headers(owner))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_logo"
    r = await client.put(f"{URL}/logo", json={"image_base64": "not base64 at all!!"}, headers=auth_headers(owner))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_logo"
    big = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * (510 * 1024)).decode()
    r = await client.put(f"{URL}/logo", json={"image_base64": big}, headers=auth_headers(owner))
    assert r.status_code == 422  # over the request limit or the logo limit


async def test_the_bill_prints_the_business_name_branch_and_logo(client, db, branch, owner, waiter, cashier, table):
    await client.patch(f"{URL}/profile", json={"display_name": "Spice Route"}, headers=auth_headers(owner))
    await client.put(f"{URL}/logo", json={"image_base64": PNG_1PX}, headers=auth_headers(owner))
    item = await factories.make_menu_item(db, name="Dal", price="200.00")
    order = await create_order(client, waiter, table, items=[line(item)])
    await fire(client, waiter, order["id"])
    b = await bill(client, cashier, order["id"])

    receipt = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", headers=auth_headers(cashier))).json()
    texts = [r["text"] for r in receipt["rows"]]
    assert texts[0] == "SPICE ROUTE" and texts[1] == branch.name
    assert receipt["logo_data_url"].startswith("data:image/png;base64,")
    html = (await client.get(f"{API}/billing/bills/{b['id']}/receipt", params={"format": "html"}, headers=auth_headers(cashier))).text
    assert '<img class="logo" src="data:image/png;base64,' in html
