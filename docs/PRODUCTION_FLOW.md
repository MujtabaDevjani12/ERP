# Production flow

```
Bill of materials (BOM)  ->  Work order  ->  Approval  ->  Release
      |                                                    |
      |                         Issue materials (raw -> WIP, all-or-nothing, tolerance-checked)
      |                                                    |
      +--> operations ----------> Job cards (in sequence: start -> complete good / rejected qty)
                                                           |
                                          In-process quality check (if the operation requires it)
                                                           |
                                   Finished goods receipt (WIP -> FG at WO cost / planned qty)
                                                           |
                                     Final quality check -> accepted quantity only -> FG stock
                                                           |
                                          Complete -> Close (left-over WIP -> production variance)
```

## BOM

* Versioned; one **active** version per finished item. `new_bom_version` copies the active one into a draft.
* Components may have a scrap percentage; no cycles (a component can never contain the item itself, directly or indirectly).
* Operations carry minutes per unit, labour rate and overhead rate per hour, and an optional machine.
* `bom_cost` gives a material / labour / overhead estimate at current average cost.

## Work order

* Needs an **active** BOM. Required materials = BOM quantity x planned quantity (+ scrap).
* `material_shortage` shows what is missing before release. Issuing more than required (beyond
  `PRODUCTION_ISSUE_TOLERANCE_PERCENT`, default 0) is refused. Unused material can be returned.
* Status: draft -> pending approval -> released -> in progress -> completed -> closed (or rejected / cancelled).
* Cost = materials issued + labour + overhead absorbed on job cards. Finished goods unit cost = total cost / planned quantity
  (so rejected units carry their cost into the good ones); anything left in WIP when the order is closed goes to **production variance**.

## Job cards

One per operation, strictly in sequence; completed quantity can never exceed what the previous operation passed.
Labour and overhead are posted to WIP from actual (or standard) minutes. Operations flagged "QC required" wait for an inspection
before the card closes, and the inspection result sets the final good / rejected quantities.

## Reports (`production/reports.py`, `/erp/report/...`)

Work-order status, pending / delayed orders, material consumption, cost, efficiency, scrap by operation, machine utilisation,
operator performance, WIP.
