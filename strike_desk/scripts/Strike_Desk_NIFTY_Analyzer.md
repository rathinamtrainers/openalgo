# Strategy Spec — Strike Desk NIFTY Analyzer

## METADATA
| Field | Value |
| Strategy name | Strike Desk NIFTY Analyzer |
| Description | Dedicated Tradetron hands for Strike Desk NIFTY Analyzer. The desk mind decides; this template buys 1 lot NIFTY weekly ATM CE or PE when the webhook flag is set, and flattens on an_exit or 15:15 IST. One lot, MIS. Live Offline. |
| Default underlying | NIFTY 50 |
| Price execution | Market Price |
| Reactivate every | 5 |
| First trade after open | 1 |
| Last trade before close | 120 |
| Public | No |
| Exit shorts first | No |

## INIT VARS (top-level)
| Name | Type | Default expression | Notes |
| an_ce | Number | Number(0) | Webhook: buy ATM CE |
| an_pe | Number | Number(0) | Webhook: buy ATM PE |
| an_exit | Number | Number(0) | Webhook: flatten |

## UNIVERSAL EXIT (OR-joined)
- `Get Runtime Number('an_exit')` `==` `Number(1)`
- `Time(NSE)` `>=` `Number(1515)`

## SETS
### Set 1: Buy CE
#### Identity
| Field | Value |
| Set name | Buy CE |
| Underlying | NIFTY 50 |
| List | 0 |
#### Entry condition
- `Get Runtime Number('an_ce')` `==` `Number(1)`
- `Time(NSE)` `>=` `Number(930)`
- `Time(NSE)` `<=` `Number(1510)`
#### Legs
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | CE | Current Week | `ATM SPOT("NIFTY 50", 0)` | 1 | MIS |
#### Set Runtime Vars written on entry
| Name | Type | Value expression |
| an_ce | Number | Number(0) |
#### Exit condition
- `Get Runtime Number('an_exit')` `==` `Number(1)`
- `Time(NSE)` `>=` `Number(1515)`
#### Set Runtime Vars written on exit
| Name | Type | Value expression |
| an_exit | Number | Number(0) |
| an_ce | Number | Number(0) |

### Set 2: Buy PE
#### Identity
| Field | Value |
| Set name | Buy PE |
| Underlying | NIFTY 50 |
| List | 0 |
#### Entry condition
- `Get Runtime Number('an_pe')` `==` `Number(1)`
- `Time(NSE)` `>=` `Number(930)`
- `Time(NSE)` `<=` `Number(1510)`
#### Legs
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | B | NIFTY 50 | PE | Current Week | `ATM SPOT("NIFTY 50", 0)` | 1 | MIS |
#### Set Runtime Vars written on entry
| Name | Type | Value expression |
| an_pe | Number | Number(0) |
#### Exit condition
- `Get Runtime Number('an_exit')` `==` `Number(1)`
- `Time(NSE)` `>=` `Number(1515)`
#### Set Runtime Vars written on exit
| Name | Type | Value expression |
| an_exit | Number | Number(0) |
| an_pe | Number | Number(0) |
