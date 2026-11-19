"""
The hardware shop module (business category `hardware`): products sold in several units, cut lengths and loose quantities,
retail and wholesale prices, estimates, customer accounts and GST invoices for business buyers.

Plan: docs/retail-modules/02-hardware.md. It never imports from `app.modules.apparel` and never writes to the shared orders,
bills, payments, menu or stock tables. Shared shop helpers come from `app.modules.retailkit`.
"""
