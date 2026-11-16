"""
Populate a local preview database with a demo dress shop, through the real API where the module has endpoints.

    python -m scripts.seed_apparel

Requires the API running at API_URL (default http://127.0.0.1:8010, the `api` entry in .claude/launch.json) against the same
database. Only for local preview/QA databases — never run against production.

Preview sign-in: dressowner / preview-dress-2026 (POS PIN 6142). The business is *Kavya Fashions* (apparel), branch KF1.
Stage A1 loads about 30 styles by CSV import (re-running updates them). Stock and sales are added by later stages.

A second dress shop made through the platform console when stage F was checked (local preview database only): *Meera Boutique*,
branch MB1, meeraowner / preview-boutique-2026 (PIN 6390). Console operator: scripts/seed_platform_admin.py.
"""

import asyncio
import os

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select

from app.cli import create_owner
from app.core.config import settings
from app.core.database import AsyncSessionLocal, dispose_engine
from app.core.entitlements import invalidate_entitlements
from app.models import Organization, StaffUser

API = os.environ.get("API_URL", "http://127.0.0.1:8010") + "/api/v1"
OWNER = {"username": "dressowner", "password": "preview-dress-2026", "pin": "6142"}
SLUG, NAME = "kavya-fashions", "Kavya Fashions"

# About 30 styles across the categories a dress shop keeps; a few cost more than the 2,500 slab (18% GST) and the
# accessories have fixed rates.
CATALOGUE = """style_code,name,category,brand,hsn,size,colour,mrp,price,barcode,sku,gst_rate,opening_stock
OX-01,Oxford Shirt,Shirts,Arrow,6205,S,Blue,1299,999,,OX-01-S-BLU,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,M,Blue,1299,999,,OX-01-M-BLU,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,L,Blue,1299,999,,OX-01-L-BLU,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,XL,Blue,1299,999,,OX-01-XL-BLU,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,S,White,1299,999,,OX-01-S-WHI,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,M,White,1299,999,,OX-01-M-WHI,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,L,White,1299,999,,OX-01-L-WHI,,
OX-01,Oxford Shirt,Shirts,Arrow,6205,XL,White,1299,999,,OX-01-XL-WHI,,
LN-02,Linen Shirt,Shirts,Linen Club,6205,M,Beige,1899,1599,,LN-02-M-BEI,,
LN-02,Linen Shirt,Shirts,Linen Club,6205,L,Beige,1899,1599,,LN-02-L-BEI,,
LN-02,Linen Shirt,Shirts,Linen Club,6205,XL,Beige,1899,1599,,LN-02-XL-BEI,,
LN-02,Linen Shirt,Shirts,Linen Club,6205,M,Olive,1899,1599,,LN-02-M-OLI,,
LN-02,Linen Shirt,Shirts,Linen Club,6205,L,Olive,1899,1599,,LN-02-L-OLI,,
LN-02,Linen Shirt,Shirts,Linen Club,6205,XL,Olive,1899,1599,,LN-02-XL-OLI,,
CH-03,Check Shirt,Shirts,Peter England,6205,S,Red,1199,899,,CH-03-S-RED,,
CH-03,Check Shirt,Shirts,Peter England,6205,M,Red,1199,899,,CH-03-M-RED,,
CH-03,Check Shirt,Shirts,Peter England,6205,L,Red,1199,899,,CH-03-L-RED,,
CH-03,Check Shirt,Shirts,Peter England,6205,XL,Red,1199,899,,CH-03-XL-RED,,
CH-03,Check Shirt,Shirts,Peter England,6205,XXL,Red,1199,899,,CH-03-XXL-RED,,
CH-03,Check Shirt,Shirts,Peter England,6205,S,Green,1199,899,,CH-03-S-GRE,,
CH-03,Check Shirt,Shirts,Peter England,6205,M,Green,1199,899,,CH-03-M-GRE,,
CH-03,Check Shirt,Shirts,Peter England,6205,L,Green,1199,899,,CH-03-L-GRE,,
CH-03,Check Shirt,Shirts,Peter England,6205,XL,Green,1199,899,,CH-03-XL-GRE,,
CH-03,Check Shirt,Shirts,Peter England,6205,XXL,Green,1199,899,,CH-03-XXL-GRE,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,38,White,1999,1699,,FM-04-38-WHI,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,40,White,1999,1699,,FM-04-40-WHI,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,42,White,1999,1699,,FM-04-42-WHI,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,44,White,1999,1699,,FM-04-44-WHI,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,38,Sky,1999,1699,,FM-04-38-SKY,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,40,Sky,1999,1699,,FM-04-40-SKY,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,42,Sky,1999,1699,,FM-04-42-SKY,,
FM-04,Formal Shirt Slim,Shirts,Van Heusen,6205,44,Sky,1999,1699,,FM-04-44-SKY,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,S,Black,599,499,,TS-05-S-BLA,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,M,Black,599,499,,TS-05-M-BLA,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,L,Black,599,499,,TS-05-L-BLA,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,XL,Black,599,499,,TS-05-XL-BLA,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,S,Navy,599,499,,TS-05-S-NAV,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,M,Navy,599,499,,TS-05-M-NAV,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,L,Navy,599,499,,TS-05-L-NAV,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,XL,Navy,599,499,,TS-05-XL-NAV,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,S,Grey,599,499,,TS-05-S-GRE,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,M,Grey,599,499,,TS-05-M-GRE,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,L,Grey,599,499,,TS-05-L-GRE,,
TS-05,Crew T-shirt,T-shirts,Jockey,6109,XL,Grey,599,499,,TS-05-XL-GRE,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,S,Navy,1499,1199,,TS-06-S-NAV,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,M,Navy,1499,1199,,TS-06-M-NAV,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,L,Navy,1499,1199,,TS-06-L-NAV,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,XL,Navy,1499,1199,,TS-06-XL-NAV,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,S,Maroon,1499,1199,,TS-06-S-MAR,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,M,Maroon,1499,1199,,TS-06-M-MAR,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,L,Maroon,1499,1199,,TS-06-L-MAR,,
TS-06,Polo T-shirt,T-shirts,US Polo,6105,XL,Maroon,1499,1199,,TS-06-XL-MAR,,
TS-07,Graphic Tee,T-shirts,Bewakoof,6109,S,White,799,549,,TS-07-S-WHI,,
TS-07,Graphic Tee,T-shirts,Bewakoof,6109,M,White,799,549,,TS-07-M-WHI,,
TS-07,Graphic Tee,T-shirts,Bewakoof,6109,L,White,799,549,,TS-07-L-WHI,,
TS-07,Graphic Tee,T-shirts,Bewakoof,6109,S,Black,799,549,,TS-07-S-BLA,,
TS-07,Graphic Tee,T-shirts,Bewakoof,6109,M,Black,799,549,,TS-07-M-BLA,,
TS-07,Graphic Tee,T-shirts,Bewakoof,6109,L,Black,799,549,,TS-07-L-BLA,,
JN-08,Slim Jeans,Jeans,Levi's,6203,30,Indigo,3299,2799,,JN-08-30-IND,,
JN-08,Slim Jeans,Jeans,Levi's,6203,32,Indigo,3299,2799,,JN-08-32-IND,,
JN-08,Slim Jeans,Jeans,Levi's,6203,34,Indigo,3299,2799,,JN-08-34-IND,,
JN-08,Slim Jeans,Jeans,Levi's,6203,36,Indigo,3299,2799,,JN-08-36-IND,,
JN-08,Slim Jeans,Jeans,Levi's,6203,30,Black,3299,2799,,JN-08-30-BLA,,
JN-08,Slim Jeans,Jeans,Levi's,6203,32,Black,3299,2799,,JN-08-32-BLA,,
JN-08,Slim Jeans,Jeans,Levi's,6203,34,Black,3299,2799,,JN-08-34-BLA,,
JN-08,Slim Jeans,Jeans,Levi's,6203,36,Black,3299,2799,,JN-08-36-BLA,,
JN-09,Straight Jeans,Jeans,Wrangler,6203,30,Indigo,2599,2199,,JN-09-30-IND,,
JN-09,Straight Jeans,Jeans,Wrangler,6203,32,Indigo,2599,2199,,JN-09-32-IND,,
JN-09,Straight Jeans,Jeans,Wrangler,6203,34,Indigo,2599,2199,,JN-09-34-IND,,
JN-09,Straight Jeans,Jeans,Wrangler,6203,36,Indigo,2599,2199,,JN-09-36-IND,,
JN-09,Straight Jeans,Jeans,Wrangler,6203,38,Indigo,2599,2199,,JN-09-38-IND,,
JN-10,Chinos,Trousers,Allen Solly,6203,30,Khaki,2299,1899,,JN-10-30-KHA,,
JN-10,Chinos,Trousers,Allen Solly,6203,32,Khaki,2299,1899,,JN-10-32-KHA,,
JN-10,Chinos,Trousers,Allen Solly,6203,34,Khaki,2299,1899,,JN-10-34-KHA,,
JN-10,Chinos,Trousers,Allen Solly,6203,36,Khaki,2299,1899,,JN-10-36-KHA,,
JN-10,Chinos,Trousers,Allen Solly,6203,30,Navy,2299,1899,,JN-10-30-NAV,,
JN-10,Chinos,Trousers,Allen Solly,6203,32,Navy,2299,1899,,JN-10-32-NAV,,
JN-10,Chinos,Trousers,Allen Solly,6203,34,Navy,2299,1899,,JN-10-34-NAV,,
JN-10,Chinos,Trousers,Allen Solly,6203,36,Navy,2299,1899,,JN-10-36-NAV,,
TR-11,Formal Trousers,Trousers,Raymond,6203,30,Grey,2999,2699,,TR-11-30-GRE,,
TR-11,Formal Trousers,Trousers,Raymond,6203,32,Grey,2999,2699,,TR-11-32-GRE,,
TR-11,Formal Trousers,Trousers,Raymond,6203,34,Grey,2999,2699,,TR-11-34-GRE,,
TR-11,Formal Trousers,Trousers,Raymond,6203,36,Grey,2999,2699,,TR-11-36-GRE,,
TR-11,Formal Trousers,Trousers,Raymond,6203,30,Black,2999,2699,,TR-11-30-BLA,,
TR-11,Formal Trousers,Trousers,Raymond,6203,32,Black,2999,2699,,TR-11-32-BLA,,
TR-11,Formal Trousers,Trousers,Raymond,6203,34,Black,2999,2699,,TR-11-34-BLA,,
TR-11,Formal Trousers,Trousers,Raymond,6203,36,Black,2999,2699,,TR-11-36-BLA,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,S,White,1499,1299,,KU-12-S-WHI,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,M,White,1499,1299,,KU-12-M-WHI,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,L,White,1499,1299,,KU-12-L-WHI,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,XL,White,1499,1299,,KU-12-XL-WHI,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,S,Mustard,1499,1299,,KU-12-S-MUS,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,M,Mustard,1499,1299,,KU-12-M-MUS,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,L,Mustard,1499,1299,,KU-12-L-MUS,,
KU-12,Cotton Kurta,Kurtas,Fabindia,6211,XL,Mustard,1499,1299,,KU-12-XL-MUS,,
KU-13,Silk Kurta,Kurtas,Manyavar,6211,M,Maroon,3499,2999,,KU-13-M-MAR,,
KU-13,Silk Kurta,Kurtas,Manyavar,6211,L,Maroon,3499,2999,,KU-13-L-MAR,,
KU-13,Silk Kurta,Kurtas,Manyavar,6211,XL,Maroon,3499,2999,,KU-13-XL-MAR,,
KU-13,Silk Kurta,Kurtas,Manyavar,6211,M,Gold,3499,2999,,KU-13-M-GOL,,
KU-13,Silk Kurta,Kurtas,Manyavar,6211,L,Gold,3499,2999,,KU-13-L-GOL,,
KU-13,Silk Kurta,Kurtas,Manyavar,6211,XL,Gold,3499,2999,,KU-13-XL-GOL,,
KU-14,Kurta Set,Kurtas,Manyavar,6211,M,Peach,5999,5499,,KU-14-M-PEA,,
KU-14,Kurta Set,Kurtas,Manyavar,6211,L,Peach,5999,5499,,KU-14-L-PEA,,
KU-14,Kurta Set,Kurtas,Manyavar,6211,XL,Peach,5999,5499,,KU-14-XL-PEA,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,S,Pink,2499,1999,,WK-15-S-PIN,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,M,Pink,2499,1999,,WK-15-M-PIN,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,L,Pink,2499,1999,,WK-15-L-PIN,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,XL,Pink,2499,1999,,WK-15-XL-PIN,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,S,Teal,2499,1999,,WK-15-S-TEA,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,M,Teal,2499,1999,,WK-15-M-TEA,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,L,Teal,2499,1999,,WK-15-L-TEA,,
WK-15,Anarkali Kurti,Women's kurtis,Biba,6204,XL,Teal,2499,1999,,WK-15-XL-TEA,,
WK-16,Straight Kurti,Women's kurtis,W,6204,S,Blue,1599,1199,,WK-16-S-BLU,,
WK-16,Straight Kurti,Women's kurtis,W,6204,M,Blue,1599,1199,,WK-16-M-BLU,,
WK-16,Straight Kurti,Women's kurtis,W,6204,L,Blue,1599,1199,,WK-16-L-BLU,,
WK-16,Straight Kurti,Women's kurtis,W,6204,XL,Blue,1599,1199,,WK-16-XL-BLU,,
WK-16,Straight Kurti,Women's kurtis,W,6204,XXL,Blue,1599,1199,,WK-16-XXL-BLU,,
WK-16,Straight Kurti,Women's kurtis,W,6204,S,Yellow,1599,1199,,WK-16-S-YEL,,
WK-16,Straight Kurti,Women's kurtis,W,6204,M,Yellow,1599,1199,,WK-16-M-YEL,,
WK-16,Straight Kurti,Women's kurtis,W,6204,L,Yellow,1599,1199,,WK-16-L-YEL,,
WK-16,Straight Kurti,Women's kurtis,W,6204,XL,Yellow,1599,1199,,WK-16-XL-YEL,,
WK-16,Straight Kurti,Women's kurtis,W,6204,XXL,Yellow,1599,1199,,WK-16-XXL-YEL,,
SA-17,Cotton Saree,Sarees,Taneira,5208,Free,Red,2999,2499,,SA-17-Free-RED,,
SA-17,Cotton Saree,Sarees,Taneira,5208,Free,Green,2999,2499,,SA-17-Free-GRE,,
SA-17,Cotton Saree,Sarees,Taneira,5208,Free,Blue,2999,2499,,SA-17-Free-BLU,,
SA-18,Silk Saree,Sarees,Taneira,5007,Free,Maroon,12999,11999,,SA-18-Free-MAR,,
SA-18,Silk Saree,Sarees,Taneira,5007,Free,Gold,12999,11999,,SA-18-Free-GOL,,
DR-19,Summer Dress,Dresses,Zara,6204,XS,Floral,2999,2599,,DR-19-XS-FLO,,
DR-19,Summer Dress,Dresses,Zara,6204,S,Floral,2999,2599,,DR-19-S-FLO,,
DR-19,Summer Dress,Dresses,Zara,6204,M,Floral,2999,2599,,DR-19-M-FLO,,
DR-19,Summer Dress,Dresses,Zara,6204,L,Floral,2999,2599,,DR-19-L-FLO,,
DR-19,Summer Dress,Dresses,Zara,6204,XS,Black,2999,2599,,DR-19-XS-BLA,,
DR-19,Summer Dress,Dresses,Zara,6204,S,Black,2999,2599,,DR-19-S-BLA,,
DR-19,Summer Dress,Dresses,Zara,6204,M,Black,2999,2599,,DR-19-M-BLA,,
DR-19,Summer Dress,Dresses,Zara,6204,L,Black,2999,2599,,DR-19-L-BLA,,
DR-20,Maxi Dress,Dresses,AND,6204,S,Navy,3999,2999,,DR-20-S-NAV,,
DR-20,Maxi Dress,Dresses,AND,6204,M,Navy,3999,2999,,DR-20-M-NAV,,
DR-20,Maxi Dress,Dresses,AND,6204,L,Navy,3999,2999,,DR-20-L-NAV,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,S,Black,599,499,,LG-21-S-BLA,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,M,Black,599,499,,LG-21-M-BLA,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,L,Black,599,499,,LG-21-L-BLA,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,XL,Black,599,499,,LG-21-XL-BLA,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,S,White,599,499,,LG-21-S-WHI,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,M,White,599,499,,LG-21-M-WHI,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,L,White,599,499,,LG-21-L-WHI,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,XL,White,599,499,,LG-21-XL-WHI,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,S,Navy,599,499,,LG-21-S-NAV,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,M,Navy,599,499,,LG-21-M-NAV,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,L,Navy,599,499,,LG-21-L-NAV,,
LG-21,Leggings,Women's bottoms,Go Colors,6104,XL,Navy,599,499,,LG-21-XL-NAV,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,2Y,Yellow,499,399,,KD-22-2Y-YEL,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,4Y,Yellow,499,399,,KD-22-4Y-YEL,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,6Y,Yellow,499,399,,KD-22-6Y-YEL,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,8Y,Yellow,499,399,,KD-22-8Y-YEL,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,2Y,Blue,499,399,,KD-22-2Y-BLU,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,4Y,Blue,499,399,,KD-22-4Y-BLU,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,6Y,Blue,499,399,,KD-22-6Y-BLU,,
KD-22,Kids T-shirt,Kids,Gini & Jony,6109,8Y,Blue,499,399,,KD-22-8Y-BLU,,
KD-23,Kids Frock,Kids,Hopscotch,6209,2Y,Pink,899,749,,KD-23-2Y-PIN,,
KD-23,Kids Frock,Kids,Hopscotch,6209,4Y,Pink,899,749,,KD-23-4Y-PIN,,
KD-23,Kids Frock,Kids,Hopscotch,6209,6Y,Pink,899,749,,KD-23-6Y-PIN,,
FW-24,Running Shoes,Footwear,Nike,6404,7,Black,4999,4499,,FW-24-7-BLA,,
FW-24,Running Shoes,Footwear,Nike,6404,8,Black,4999,4499,,FW-24-8-BLA,,
FW-24,Running Shoes,Footwear,Nike,6404,9,Black,4999,4499,,FW-24-9-BLA,,
FW-24,Running Shoes,Footwear,Nike,6404,10,Black,4999,4499,,FW-24-10-BLA,,
FW-24,Running Shoes,Footwear,Nike,6404,7,White,4999,4499,,FW-24-7-WHI,,
FW-24,Running Shoes,Footwear,Nike,6404,8,White,4999,4499,,FW-24-8-WHI,,
FW-24,Running Shoes,Footwear,Nike,6404,9,White,4999,4499,,FW-24-9-WHI,,
FW-24,Running Shoes,Footwear,Nike,6404,10,White,4999,4499,,FW-24-10-WHI,,
FW-25,Leather Sandals,Footwear,Bata,6403,6,Brown,1999,1799,,FW-25-6-BRO,,
FW-25,Leather Sandals,Footwear,Bata,6403,7,Brown,1999,1799,,FW-25-7-BRO,,
FW-25,Leather Sandals,Footwear,Bata,6403,8,Brown,1999,1799,,FW-25-8-BRO,,
FW-25,Leather Sandals,Footwear,Bata,6403,9,Brown,1999,1799,,FW-25-9-BRO,,
FW-25,Leather Sandals,Footwear,Bata,6403,10,Brown,1999,1799,,FW-25-10-BRO,,
FW-26,Flip Flops,Footwear,Bata,6402,6,Blue,399,349,,FW-26-6-BLU,,
FW-26,Flip Flops,Footwear,Bata,6402,7,Blue,399,349,,FW-26-7-BLU,,
FW-26,Flip Flops,Footwear,Bata,6402,8,Blue,399,349,,FW-26-8-BLU,,
FW-26,Flip Flops,Footwear,Bata,6402,9,Blue,399,349,,FW-26-9-BLU,,
AC-27,Leather Belt,Accessories,Hidesign,4203,Free,Brown,1499,1299,,AC-27-Free-BRO,18,
AC-27,Leather Belt,Accessories,Hidesign,4203,Free,Black,1499,1299,,AC-27-Free-BLA,18,
AC-28,Wallet,Accessories,Hidesign,4202,Free,Brown,1999,1799,,AC-28-Free-BRO,18,
AC-29,Cotton Socks 3-pack,Accessories,Jockey,6115,Free,Assorted,399,349,,AC-29-Free-ASS,5,
AC-30,Silk Tie,Accessories,Van Heusen,6215,Free,Navy,999,799,,AC-30-Free-NAV,12,
AC-30,Silk Tie,Accessories,Van Heusen,6215,Free,Maroon,999,799,,AC-30-Free-MAR,12,
"""


def check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        raise SystemExit(f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text}")
    return r.json() if r.content else {}


async def ensure_shop() -> None:
    async with AsyncSessionLocal() as db:
        exists = await db.scalar(select(StaffUser).where(StaffUser.username == OWNER["username"]))
    if not exists:
        await create_owner(OWNER["username"], "Kavya Rao", "KF1", "Jayanagar", OWNER["password"], OWNER["pin"], SLUG, NAME)
    async with AsyncSessionLocal() as db:
        org = await db.scalar(select(Organization).where(Organization.slug == SLUG))
        org.category, org_id = "apparel", org.id
        await db.commit()
    await dispose_engine()
    client = aioredis.from_url(settings.REDIS_URL)
    await invalidate_entitlements(client, org_id)  # the API sees the business type at once
    await client.aclose()


async def main() -> None:
    await ensure_shop()
    async with httpx.AsyncClient(timeout=60) as c:
        owner = check(await c.post(f"{API}/staff/auth/login", json={"username": OWNER["username"], "password": OWNER["password"]}))
        headers = {"Authorization": f"Bearer {owner['access_token']}"}
        ent = check(await c.get(f"{API}/me/entitlements", headers=headers))
        result = check(await c.post(f"{API}/apparel/import", json={"csv": CATALOGUE}, headers=headers))
        if result["errors"]:
            raise SystemExit(f"Import errors: {result['errors'][:3]}")
        print(f"{NAME} ({ent['category']}): {result['products_created']} styles and {result['variants_created']} variants "
              f"created, {result['variants_updated']} variants updated.")
        print(f"Sign in: {OWNER['username']} / {OWNER['password']} (PIN {OWNER['pin']}).")


if __name__ == "__main__":
    asyncio.run(main())
