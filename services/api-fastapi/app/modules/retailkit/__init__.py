"""
The shop helper kit shared by the dress (`apparel`) and hardware (`hardware`) modules.

Pure functions and column mixins only: no tables of its own, no routers, and no knowledge of either module. A helper that would
need `if module == ...` does not belong here.
"""
