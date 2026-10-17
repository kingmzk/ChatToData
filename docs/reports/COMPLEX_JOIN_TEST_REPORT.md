# Complex Join Test Report

**Result: PASSED — 100% of executed tests passed.**

**Run date:** 2026-10-16  
**Application:** Talk2Data local browser app at `http://127.0.0.1:8000/`  
**Test source:** `complex_relations`

## Test totals

| Check | Result |
|---|---:|
| Full automated suite | 176 passed, 0 failed, 6 skipped |
| New complex-join integration suite | 8 passed, 0 failed |
| Live app API join scenarios | 3 passed, 0 failed |
| Live health and source-catalog checks | Passed; HTTP 200 |

The 100% pass rate is calculated over tests that ran: 176 of 176 passed. Six tests were skipped and are excluded from that rate.

## Verified data and relationships

The generated SQLite sample contains 11 join-focused tables alongside the existing sales demo table. It has 10 explicit foreign-key edges, including a three-hop customer-to-city-to-region path, order-item-to-product-to-category path, and two many-to-many junctions: order tags and order promotions. The app catalog exposes 11 tables, 4 measures, and 11 dimensions for the `complex_relations` source.

## Exact result checks

| Scenario | Expected and observed result |
|---|---|
| Revenue through customer → city → region | North America: $400; Europe: $200; total: $600 |
| Distinct order count by tag and promotion | Tech/BLACKFRIDAY: 1; Tech/SUMMER: 1; Urgent/BLACKFRIDAY: 1; Wholesale/BLACKFRIDAY: 1 |
| Revenue filtered to Tech-tagged orders | $300 |
| Distinct order count by junction reviewer | Alice: 1; Bob: 2 |
| Distinct order count by tag priority | HIGH: 1; CRITICAL: 1; LOW: 1; MEDIUM: 1 |
| Item revenue through product category | Hardware: $380; Software: $220 |
| Parent revenue grouped by many-to-many tag without an allocation rule | Correctly declined |
| SQLite schema introspection | All 10 explicit foreign keys discovered |

The live app API was exercised against the running server for the region rollup, simultaneous tag-and-promotion join, and Tech filter. The home page and source/catalog endpoints responded successfully. Browser-control automation timed out, so this report does not claim a visual click-through check.

## Reproduction

Run the complete suite from the project root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Run only the new complex-join integration cases:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_complex_join_demo.py -q
```
