# Strategy Spec — Strike Desk QuadLegs DirAdd NIFTY

## METADATA
| Field | Value |
| Strategy name | Strike Desk QuadLegs DirAdd NIFTY |
| Description | Research only. Positional NRML QuadShield legs, no ATM walk. Once per day, if spot is 150 pts from entry AND 5m futures ADX>25, RSI confirms, and 5m volume > 20-bar SMA: on a dip sell 2 extra CE and buy 1 cheap PE hedge; on a rally sell 2 extra PE and buy 1 cheap CE hedge. Time-exit on held expiry at 13:30 IST. PNL < -3500 x multiplier kill switch. Not Live Auto QuadShield. Private. |
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
| eup | Number | Number(0) | Rounded NIFTY spot at short entry |
| dir_add | Number | Number(1) | 1 = add still available today; 2 after it fires |
| g_dip | Number | Number(1) | 2 when dip continuation filters are true |
| g_rally | Number | Number(1) | 2 when rally continuation filters are true |

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
| eup | Number | Round(LTP(Instrument Name("NFO,NIFTY 50,,,,,")), 50) |
#### Repair conditions
##### Repair 1: dip continuation — sell 2 extra CE, buy 1 cheap PE
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('dir_add')` `==` `Number(1)`
- `Get Runtime Number('g_dip')` `==` `Number(2)`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | S | NIFTY 50 | CE | Current Week | `Round(ATM SPOT("NIFTY 50", 1), 50)` | 2 | NRML |
| 2 | B | NIFTY 50 | PE | Current Week | `Find Strike("NIFTY 50", Select Expiry("week", 0), "ltp", Math Operation(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,Current Week,CE,ATM-SPOT,,")), LTP(Instrument Name("NFO,NIFTY 50,Current Week,PE,ATM-SPOT,,")), "+"), Number(0.035), "*"), "PE", "greater")` | 1 | NRML |
###### Runtime Vars
| Name | Type | Value expression |
| dir_add | Number | Number(2) |
##### Repair 2: rally continuation — sell 2 extra PE, buy 1 cheap CE
| Field | Value |
| Type | Repair Continuous |
| Sub-type | None |
###### Condition
- `Get Runtime Number('dir_add')` `==` `Number(1)`
- `Get Runtime Number('g_rally')` `==` `Number(2)`
###### Positions
| # | Buy/Sell | Underlying | Type | Expiry | Strike | Qty | Product |
| 1 | S | NIFTY 50 | PE | Current Week | `Round(ATM SPOT("NIFTY 50", -1), 50)` | 2 | NRML |
| 2 | B | NIFTY 50 | CE | Current Week | `Find Strike("NIFTY 50", Select Expiry("week", 0), "ltp", Math Operation(Math Operation(LTP(Instrument Name("NFO,NIFTY 50,Current Week,CE,ATM-SPOT,,")), LTP(Instrument Name("NFO,NIFTY 50,Current Week,PE,ATM-SPOT,,")), "+"), Number(0.035), "*"), "CE", "greater")` | 1 | NRML |
###### Runtime Vars
| Name | Type | Value expression |
| dir_add | Number | Number(2) |

## PYTHON BLOCK
```python
t = tt_Time('NSE')
today = tt_Today('NSE')
ltp = tt_get_var(tt_get_instrument('NFO,NIFTY 50,,,,,'), 'LTP')
eup = tt_get_runtime('eup')
s2 = tt_get_runtime('set2')
dd = tt_get_runtime('dir_date')
if dd is None or dd != today:
    tt_set_runtime('dir_add', 1)
    tt_set_runtime('dir_date', today)
nfo = tt_get_data(tt_get_instrument('NFO,NIFTY 50,Current Month,,,,'), '5m', 'All')
fut = tt_get_instrument('NFO,NIFTY 50,Current Month,,,,')
vols = tt_get_voi(fut, '5m', 'volume', 'All')
adx1 = tt_get_element(tt_ADX(nfo, '15'), '-1')
rsi1 = tt_get_element(tt_RSI(nfo, '14'), '-1')
vol1 = tt_get_element(vols, '-1')
volsma = tt_get_element(tt_SMA(vols, '20'), '-1')
g_dip = 1
g_rally = 1
exp0 = tt_curr_weekexpiry('NIFTY 50', '0')
allow = False
if t is not None and t >= 1000 and s2 == 1:
    if today != exp0 or t < 1330:
        allow = True
if allow and adx1 is not None and adx1 > 25:
    if vol1 is not None and volsma is not None and vol1 > volsma:
        if ltp is not None and eup is not None and rsi1 is not None:
            if ltp <= eup - 150 and rsi1 < 40:
                g_dip = 2
            elif ltp >= eup + 150 and rsi1 > 60:
                g_rally = 2
tt_set_runtime('g_dip', g_dip)
tt_set_runtime('g_rally', g_rally)
```
