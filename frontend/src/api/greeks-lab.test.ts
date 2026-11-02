import { describe, expect, it } from 'vitest'
import {
  boardHasGreeks,
  boardSessionExchange,
  buildCoaMap,
  dayCumulativePnl,
  inScenarioLine,
  marginByCurrency,
  marginOf,
  outRuleLine,
  pnlByCurrency,
  runningPnl,
  scenarioGloss,
  type CoaBoard,
  type CoaTrade,
} from './greeks-lab'

function board(partial: Partial<CoaBoard>): CoaBoard {
  return {
    slug: 'nifty',
    title: 'NIFTY',
    greeks_exchange: 'NFO',
    greeks_underlying: 'NIFTY',
    ...partial,
  }
}

describe('boardHasGreeks', () => {
  it('keeps cash and MCX boards on the OpenAlgo snapshot', () => {
    expect(boardHasGreeks(board({ slug: 'nifty' }))).toBe(true)
    expect(
      boardHasGreeks(
        board({ slug: 'natgas', greeks_exchange: 'MCX', greeks_underlying: 'NATURALGAS' })
      )
    ).toBe(true)
  })

  it('does not bind BTC to a leftover Nifty snapshot', () => {
    expect(
      boardHasGreeks(
        board({
          slug: 'btc-options',
          title: 'BTC',
          greeks_exchange: null,
          greeks_underlying: null,
        })
      )
    ).toBe(false)
  })
})

describe('boardSessionExchange', () => {
  it('maps each Lab tile to its own session clock', () => {
    expect(boardSessionExchange(board({ slug: 'nifty' }))).toBe('NFO')
    expect(boardSessionExchange(board({ slug: 'sensex', greeks_exchange: 'BFO' }))).toBe('BFO')
    expect(boardSessionExchange(board({ slug: 'natgas', greeks_exchange: 'MCX' }))).toBe('MCX')
    expect(
      boardSessionExchange(
        board({ slug: 'btc-options', title: 'BTC', greeks_exchange: null, greeks_underlying: null })
      )
    ).toBe('CRYPTO')
  })
})

describe('buildCoaMap', () => {
  it('orders BTC levels and keeps last inside the floor-ceiling band', () => {
    const map = buildCoaMap(
      board({
        slug: 'btc-options',
        title: 'BTC',
        last: 77254,
        support: 75200,
        eos: 75000,
        resistance: 79000,
        eor: 79200,
      })
    )
    expect(map).not.toBeNull()
    expect(map?.marks.map((mark) => mark.label)).toEqual([
      'Put-buy',
      'Ceiling',
      'Last',
      'Floor',
      'Call-buy',
    ])
    expect(map?.inside).not.toBeNull()
    expect(map?.callZone).not.toBeNull()
    expect(map?.putZone).not.toBeNull()
    expect(map?.callZone?.left).toBeLessThan(map?.inside?.left || 0)
    expect(map?.putZone?.left).toBeGreaterThan(map?.inside?.left || 0)
    const last = map?.marks.find((mark) => mark.tone === 'spot')
    expect(last?.pct).toBeGreaterThan(map?.marks.find((mark) => mark.tone === 'floor')?.pct || 0)
    expect(last?.pct).toBeLessThan(map?.marks.find((mark) => mark.tone === 'ceiling')?.pct || 100)
  })

  it('keeps a tight Nifty range readable left-to-right', () => {
    const map = buildCoaMap(
      board({
        last: 23053,
        support: 23000,
        eos: 22980,
        resistance: 23100,
        eor: 23120,
      })
    )
    expect(map?.callZone?.width).toBeGreaterThan(5)
    expect(map?.putZone?.width).toBeGreaterThan(5)
    expect((map?.callZone?.left || 0) + (map?.callZone?.width || 0)).toBeCloseTo(
      map?.inside?.left || 0,
      5
    )
  })
})

describe('coa pnl totals', () => {
  const rows: CoaTrade[] = [
    {
      id: 'a',
      board: 'banknifty',
      title: 'Banknifty',
      side: 'CE',
      in_label: 'CE on CoA 1.1',
      out_label: 'spot target opposite S/R',
      hold_label: '~3 min',
      in_at: '2026-10-30T11:56:00+05:30',
      day: '30 Oct 2026',
      atm_pnl: 1662,
      entry_ltp: 877.5,
      qty: 30,
      currency: 'INR',
    },
    {
      id: 'b',
      board: 'banknifty',
      title: 'Banknifty',
      side: 'CE',
      in_label: 'CE on CoA 1.1',
      out_label: 'scenario kill 1.5',
      hold_label: '~3 min',
      in_at: '2026-10-30T12:26:00+05:30',
      day: '30 Oct 2026',
      atm_pnl: -153,
      entry_ltp: 884.7,
      qty: 30,
      currency: 'INR',
    },
    {
      id: 'c',
      board: 'nifty',
      title: 'Nifty',
      side: 'CE',
      in_label: 'CE on CoA 1.1',
      out_label: '15:15 flatten',
      hold_label: '~4 min',
      in_at: '2026-10-31T10:00:00+05:30',
      day: '31 Oct 2026',
      atm_pnl: 200,
      entry_ltp: 100,
      qty: 65,
      currency: 'INR',
    },
  ]

  it('sums daily and cumulative by currency', () => {
    expect(pnlByCurrency(rows)).toEqual({ INR: 1709 })
    expect(pnlByCurrency(rows.filter((row) => row.day === '31 Oct 2026'))).toEqual({ INR: 200 })
  })

  it('running pnl is oldest to newest', () => {
    const run = runningPnl(rows)
    expect(run.get('a')).toBe(1662)
    expect(run.get('b')).toBe(1509)
    expect(run.get('c')).toBe(1709)
  })

  it('day cumulative walks oldest day first', () => {
    const groups: Array<[string, CoaTrade[]]> = [
      ['31 Oct 2026', [rows[2]]],
      ['30 Oct 2026', [rows[0], rows[1]]],
    ]
    const cum = dayCumulativePnl(groups)
    expect(cum.get('30 Oct 2026')).toEqual({ INR: 1509 })
    expect(cum.get('31 Oct 2026')).toEqual({ INR: 1709 })
  })

  it('margin is ATM premium blocked at entry', () => {
    expect(marginOf(rows[0])).toBe(26325)
    expect(marginOf({ ...rows[0], margin_used: 25000 })).toBe(25000)
    expect(marginByCurrency(rows)).toEqual({ INR: 59366 })
  })

  it('explains CoA scenario numbers in plain English', () => {
    expect(scenarioGloss('1.1')?.trade).toBe('CE and PE can arm')
    expect(inScenarioLine(rows[0])).toContain('Floor and ceiling are both holding')
    expect(outRuleLine(rows[0])).toBe('Spot printed the ceiling (resistance)')
    expect(outRuleLine({ ...rows[1], exit_scenario: '1.5', rule: 'scenario kill CoA 1.5' })).toContain(
      'CE no longer allowed'
    )
    expect(outRuleLine({ ...rows[2], rule: '15:15 flatten' })).toBe('House flatten at 15:15 IST')
    expect(
      outRuleLine({
        ...rows[0],
        side: 'PE',
        in_label: 'PE on CoA 1.6',
        out_label: 'scenario kill 1.9',
        exit_scenario: '1.9',
        rule: 'scenario kill CoA 1.9',
      })
    ).toContain('PE no longer allowed')
  })
})
