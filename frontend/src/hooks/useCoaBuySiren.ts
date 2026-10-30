import { useCallback, useEffect, useRef, useState } from 'react'
import type { CoaBoard } from '@/api/greeks-lab'

export const COA_SIREN_SNOOZE_MS = 5 * 60 * 1000

export function isCoaBuy(label?: string | null): boolean {
  const text = (label || '').toUpperCase().trim()
  return text === 'CALL TAGGED' || text === 'PUT TAGGED'
}

type AudioWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: typeof AudioContext
  }

export function useCoaBuySiren(boards: CoaBoard[], onBuy?: (board: CoaBoard) => void) {
  const [armed, setArmed] = useState(true)
  const [alarming, setAlarming] = useState(false)
  const [alarmLabel, setAlarmLabel] = useState('')
  const [snoozedUntil, setSnoozedUntil] = useState<number | null>(null)
  const [, setTick] = useState(0)
  const onBuyRef = useRef(onBuy)
  onBuyRef.current = onBuy

  const primedRef = useRef(false)
  const lastRef = useRef<Record<string, string>>({})
  const audioRef = useRef<AudioContext | null>(null)
  const oscARef = useRef<OscillatorNode | null>(null)
  const oscBRef = useRef<OscillatorNode | null>(null)
  const gainRef = useRef<GainNode | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const snoozeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const hiRef = useRef(true)
  const titleRef = useRef(document.title)
  const unlockedRef = useRef(false)

  const ensureAudio = useCallback(() => {
    const Ctor = window.AudioContext || (window as AudioWindow).webkitAudioContext
    if (!Ctor) return null
    if (!audioRef.current) audioRef.current = new Ctor()
    if (audioRef.current.state === 'suspended') void audioRef.current.resume()
    return audioRef.current
  }, [])

  const stopSiren = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    try {
      oscARef.current?.stop()
    } catch {
      // already stopped
    }
    try {
      oscBRef.current?.stop()
    } catch {
      // already stopped
    }
    oscARef.current = null
    oscBRef.current = null
    gainRef.current = null
    document.title = titleRef.current
    setAlarming(false)
    setAlarmLabel('')
  }, [])

  const clearSnooze = useCallback(() => {
    if (snoozeTimerRef.current) {
      clearTimeout(snoozeTimerRef.current)
      snoozeTimerRef.current = null
    }
    setSnoozedUntil(null)
  }, [])

  const startSiren = useCallback(
    (label: string) => {
      const ctx = ensureAudio()
      if (!ctx) return
      stopSiren()
      titleRef.current = document.title
      const gain = ctx.createGain()
      gain.gain.value = 0.9
      gain.connect(ctx.destination)
      const oscA = ctx.createOscillator()
      const oscB = ctx.createOscillator()
      oscA.type = 'square'
      oscB.type = 'square'
      oscA.frequency.value = 880
      oscB.frequency.value = 1320
      oscA.connect(gain)
      oscB.connect(gain)
      oscA.start()
      oscB.start()
      oscARef.current = oscA
      oscBRef.current = oscB
      gainRef.current = gain
      hiRef.current = true
      timerRef.current = setInterval(() => {
        hiRef.current = !hiRef.current
        const hi = hiRef.current
        oscA.frequency.setValueAtTime(hi ? 880 : 660, ctx.currentTime)
        oscB.frequency.setValueAtTime(hi ? 1480 : 990, ctx.currentTime)
        document.title = hi ? `BUY — ${label}` : titleRef.current
      }, 280)
      setAlarming(true)
      setAlarmLabel(label)
      try {
        if (Notification.permission === 'granted') {
          new Notification('CoA buy tagged', { body: label, requireInteraction: true })
        }
      } catch {
        // Notifications are optional
      }
    },
    [ensureAudio, stopSiren]
  )

  const arm = useCallback(async () => {
    clearSnooze()
    ensureAudio()
    primedRef.current = false
    lastRef.current = {}
    setArmed(true)
    try {
      await Notification.requestPermission()
    } catch {
      // optional
    }
  }, [clearSnooze, ensureAudio])

  const silence = useCallback(() => {
    stopSiren()
    setArmed(false)
    clearSnooze()
    const until = Date.now() + COA_SIREN_SNOOZE_MS
    setSnoozedUntil(until)
    snoozeTimerRef.current = setTimeout(() => {
      snoozeTimerRef.current = null
      void arm()
    }, COA_SIREN_SNOOZE_MS)
  }, [arm, clearSnooze, stopSiren])

  const test = useCallback(() => {
    ensureAudio()
    startSiren('TEST')
  }, [ensureAudio, startSiren])

  useEffect(() => {
    const unlock = () => {
      ensureAudio()
      if (!unlockedRef.current) {
        unlockedRef.current = true
        try {
          void Notification.requestPermission()
        } catch {
          // optional
        }
      }
    }
    window.addEventListener('pointerdown', unlock)
    window.addEventListener('keydown', unlock)
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [ensureAudio])

  useEffect(() => {
    if (!armed || boards.length === 0) return
    const fresh: CoaBoard[] = []
    for (const board of boards) {
      const chip = board.scenario_label || ''
      const prev = lastRef.current[board.slug]
      if (primedRef.current && isCoaBuy(chip) && prev !== chip) {
        fresh.push(board)
      }
      lastRef.current[board.slug] = chip
    }
    if (!primedRef.current) {
      primedRef.current = true
      return
    }
    if (fresh.length) {
      startSiren(fresh.map((board) => `${board.title} ${board.scenario_label}`).join(' · '))
      onBuyRef.current?.(fresh[0])
    }
  }, [armed, boards, startSiren])

  useEffect(() => {
    if (snoozedUntil == null) return
    const id = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [snoozedUntil])

  useEffect(
    () => () => {
      stopSiren()
      if (snoozeTimerRef.current) {
        clearTimeout(snoozeTimerRef.current)
        snoozeTimerRef.current = null
      }
    },
    [stopSiren]
  )

  const snoozeLeftMs = snoozedUntil ? Math.max(0, snoozedUntil - Date.now()) : 0

  return { armed, alarming, alarmLabel, snoozeLeftMs, arm, silence, test }
}
