# Strategy Spec — Strike Desk Octashield NIFTY

## METADATA
| Field | Value |
| Strategy name | Strike Desk Octashield NIFTY |
| Description | NIFTY weekly Octashield. Set 1 buys cheap current-week wings (3 lots CE+PE at 2pct of ATM-SPOT straddle). Set 2 sells a 3-strike MIS cluster (ATM, ATM+50, ATM-50, 1 lot each side) then walks one strike as spot moves 50 pts. Universal flatten 14:25 or PNL < -3500 x multiplier. Private. |
| Default underlying | NIFTY 50 |
| Price execution | Market Price |
| Reactivate every | 720 |
| First trade after open | 1 |
| Last trade before close | 60 |
| Exit shorts first | Yes |

## INIT VARS (top-level)
| Name | Type | Default expression | Notes |
| set1 | Number | Number(0) | 1 after cheap wings fill |
| set2 | Number | Number(0) | 1 after short cluster fills |
| set2open | Number | Number(0) | 1 while the short cluster is the live walk |
| eup | Number | Number(0) | Rounded NIFTY spot at last cluster open |
| rc1 | Number | Number(0) | 1 after rally cover, before re-sell |
| rc2 | Number | Number(0) | 1 after drop cover, before re-sell |

## UNIVERSAL EXIT (OR-joined)
- `Time(NSE)` `>=` `Number(1425)`
- `PNL()` `<` `Math Operation(Number(-3500), Multiplier(), '*')`

## SETS
### Set 1: Cheap wings
#### Identity
| Field | Value |
| Set name | Cheap wings |
| Underlying | NIFTY 50 |
| List | 0 |
#### Entry condition (AND-joined)
- `Time(NSE)` `>=` `Number(921)`
- `Get Runtime Number('set1')` `!=` `Number(1)`
#### Legs
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | CE | Current Week | `Find Strike("NIFTY 50", Select Expiry("week", 0), "ltp", Math Operation(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,Current Week,CE,ATM-SPOT,,")), LTP(Instrument Name("NFO,NIFTY 50,Current Week,PE,ATM-SPOT,,")), "+"), Number(0.02), "*"), "CE", "greater")` | 3 | MIS |
| 2 | B | NIFTY 50 | PE | Current Week | `Find Strike("NIFTY 50", Select Expiry("week", 0), "ltp", Math Operation(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,Current Week,CE,ATM-SPOT,,")), LTP(Instrument Name("NFO,NIFTY 50,Current Week,PE,ATM-SPOT,,")), "+"), Number(0.02), "*"), "PE", "greater")` | 3 | MIS |
#### Set Runtime Vars written on entry
| Name | Type | Value expression |
| set1 | Number | Number(1) |

### Set 2: Short cluster
#### Identity
| Field | Value |
| Set name | Short cluster |
| Underlying | NIFTY 50 |
| List | 0 |
#### Entry condition (AND-joined)
- `Time(NSE)` `>=` `Number(922)`
- `Get Runtime Number('set1')` `==` `Number(1)`
- `Get Runtime Number('set2')` `!=` `Number(1)`
#### Legs
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | S | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50)` | 1 | MIS |
| 2 | S | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50)` | 1 | MIS |
| 3 | S | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) + Number(50)` | 1 | MIS |
| 4 | S | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) + Number(50)` | 1 | MIS |
| 5 | S | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) - Number(50)` | 1 | MIS |
| 6 | S | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) - Number(50)` | 1 | MIS |
#### Set Runtime Vars written on entry
| Name | Type | Value expression |
| set2 | Number | Number(1) |
| set2open | Number | Number(1) |
| eup | Number | Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) |
#### Repair conditions
##### Repair 1: rally cover
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('set2open')` `==` `Number(1)`
- `LTP(Instrument Name("NFO,NIFTY 50,,,,,"))` `>` `Math Operation(Get Runtime Number('eup'), Number(50), '+')`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) - Number(100)` | 1 | MIS |
| 2 | B | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) - Number(100)` | 1 | MIS |
###### Runtime Vars
| Name | Type | Value expression |
| set2open | Number | Number(2) |
| rc1 | Number | Number(1) |
##### Repair 2: drop cover
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('set2open')` `==` `Number(1)`
- `LTP(Instrument Name("NFO,NIFTY 50,,,,,"))` `<` `Math Operation(Get Runtime Number('eup'), Number(50), '-')`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) + Number(100)` | 1 | MIS |
| 2 | B | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) + Number(100)` | 1 | MIS |
###### Runtime Vars
| Name | Type | Value expression |
| set2open | Number | Number(2) |
| rc2 | Number | Number(1) |
##### Repair 3: rally re-sell
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('rc1')` `==` `Number(1)`
- `Get Runtime Number('set2open')` `==` `Number(2)`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | S | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) + Number(50)` | 1 | MIS |
| 2 | S | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) + Number(50)` | 1 | MIS |
###### Runtime Vars
| Name | Type | Value expression |
| rc1 | Number | Number(0) |
| set2open | Number | Number(1) |
| eup | Number | Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) |
##### Repair 4: drop re-sell
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('rc2')` `==` `Number(1)`
- `Get Runtime Number('set2open')` `==` `Number(2)`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | S | NIFTY 50 | CE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) - Number(50)` | 1 | MIS |
| 2 | S | NIFTY 50 | PE | Current Week | `Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) - Number(50)` | 1 | MIS |
###### Runtime Vars
| Name | Type | Value expression |
| rc2 | Number | Number(0) |
| set2open | Number | Number(1) |
| eup | Number | Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) |
#### Exit condition
- `Time(NSE)` `>` `Number(1420)`
