# Strategy Spec — Strike Desk Octashield QuadLegs NIFTY

## METADATA
| Field | Value |
| Strategy name | Strike Desk Octashield QuadLegs NIFTY |
| Description | Research only. Positional NRML. Octashield ATM walk on QuadShield legs. Set 1 buys 1-lot current-week wings at 3.5pct of ATM-SPOT straddle. Set 2 sells 1-lot current-week ATM+1 CE and ATM-1 PE. When spot moves 100 pts the shorts roll in one Repair Continuous to the new ATM±1. Wings stay. Time-exit only on the held contract's expiry day at 13:30 IST. PNL < -3500 x multiplier remains as a kill switch. Not Live Auto QuadShield. Private. |
| Default underlying | NIFTY 50 |
| Price execution | Market Price |
| Reactivate every | 720 |
| First trade after open | 1 |
| Last trade before close | 60 |
| Exit shorts first | Yes |

## INIT VARS (top-level)
| Name | Type | Default expression | Notes |
| set1 | Number | Number(0) | 1 after cheap wings fill |
| set2 | Number | Number(0) | 1 after the short strangle fills |
| set2open | Number | Number(1) | 2 while the short strangle is the live walk |
| eup | Number | Number(0) | Rounded NIFTY spot at last short open |
| ce_k | Number | Number(0) | CE strike currently short |
| pe_k | Number | Number(0) | PE strike currently short |

## UNIVERSAL EXIT (OR-joined)
- (group, AND-joined)
    - `Days Difference (D2-D1)(Today('NSE'), Traded Instrument Name('Entry','expiry','NIFTY 50',1,1,1))` `==` `Number(0)`
    - `Time(NSE)` `>=` `Number(1330)`
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
- (group, OR-joined)
    - `Days Difference (D2-D1)(Today('NSE'), Current Week Expiry("NIFTY 50", 0))` `!=` `Number(0)`
    - `Time(NSE)` `<` `Number(1330)`
#### Legs
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | CE | Current Week | `Find Strike("NIFTY 50", Select Expiry("week", 0), "ltp", Math Operation(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,Current Week,CE,ATM-SPOT,,")), LTP(Instrument Name("NFO,NIFTY 50,Current Week,PE,ATM-SPOT,,")), "+"), Number(0.035), "*"), "CE", "greater")` | 1 | NRML |
| 2 | B | NIFTY 50 | PE | Current Week | `Find Strike("NIFTY 50", Select Expiry("week", 0), "ltp", Math Operation(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,Current Week,CE,ATM-SPOT,,")), LTP(Instrument Name("NFO,NIFTY 50,Current Week,PE,ATM-SPOT,,")), "+"), Number(0.035), "*"), "PE", "greater")` | 1 | NRML |
#### Set Runtime Vars written on entry
| Name | Type | Value expression |
| set1 | Number | Number(1) |

### Set 2: Short strangle
#### Identity
| Field | Value |
| Set name | Short strangle |
| Underlying | NIFTY 50 |
| List | 0 |
#### Entry condition (AND-joined)
- `Time(NSE)` `>=` `Number(922)`
- `Get Runtime Number('set1')` `==` `Number(1)`
- `Get Runtime Number('set2')` `!=` `Number(1)`
- (group, OR-joined)
    - `Days Difference (D2-D1)(Today('NSE'), Current Week Expiry("NIFTY 50", 0))` `!=` `Number(0)`
    - `Time(NSE)` `<` `Number(1330)`
#### Legs
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | S | NIFTY 50 | CE | Current Week | `Round(ATM SPOT("NIFTY 50", 1), 50)` | 1 | NRML |
| 2 | S | NIFTY 50 | PE | Current Week | `Round(ATM SPOT("NIFTY 50", -1), 50)` | 1 | NRML |
#### Set Runtime Vars written on entry
| Name | Type | Value expression |
| set2 | Number | Number(1) |
| set2open | Number | Number(2) |
| eup | Number | Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) |
| ce_k | Number | Round(ATM SPOT("NIFTY 50", 1), 50) |
| pe_k | Number | Round(ATM SPOT("NIFTY 50", -1), 50) |
#### Repair conditions
##### Repair 1: roll shorts to the new ATM±1
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('set2open')` `==` `Number(2)`
- `Absolute(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), Get Runtime Number('eup'), '-'))` `>=` `Number(100)`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | CE | Current Week | `Get Runtime Number('ce_k')` | 1 | NRML |
| 2 | B | NIFTY 50 | PE | Current Week | `Get Runtime Number('pe_k')` | 1 | NRML |
| 3 | S | NIFTY 50 | CE | Current Week | `Round(ATM SPOT("NIFTY 50", 1), 50)` | 1 | NRML |
| 4 | S | NIFTY 50 | PE | Current Week | `Round(ATM SPOT("NIFTY 50", -1), 50)` | 1 | NRML |
###### Runtime Vars
| Name | Type | Value expression |
| eup | Number | Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) |
| ce_k | Number | Round(ATM SPOT("NIFTY 50", 1), 50) |
| pe_k | Number | Round(ATM SPOT("NIFTY 50", -1), 50) |
