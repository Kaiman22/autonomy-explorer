import React, { useState, useEffect, useCallback, useMemo } from 'react'
import MapView from './layers/MapView'
import SidePanel from './panels/SidePanel'

const DATA_URL = './data/municipalities_scored.geojson'

// Default model parameters (can be adjusted via UI sliders)
const DEFAULT_MODEL_PARAMS = {
  avFactor: 0.65,     // AV comfort factor (0.5 = very comfortable, 1.0 = same as driving)
  ptFactor: 0.90,     // PT comfort factor (1.0 = same burden as driving, lower = PT feels easier)
  vtt: 30,            // Value of travel time (CHF/hour) — Swiss commuter literature ~CHF 23–37
  capRate: 0.04,      // Capitalization rate (perpetuity discount) — typical Swiss RE ~3–5%
}

// Valid colorBy metrics (for URL backward-compat validation)
const VALID_METRICS = [
  'avg_car_access', 'avg_pt_access', 'optimum_access',
  'car_pt_delta_min', 'av_upside', 'av_value_unlock', 'chf_per_m2',
]

// --- Hub-based PT routing for custom reference locations ---
// Instead of crude car-ratio estimates, we make 10 SBB API calls (custom→hub)
// and combine with existing settlement→hub PT data for realistic estimates.
const HUB_CITIES = {
  zurich:     { station: 'Zürich HB',    transferMin: 7.5 },
  bern:       { station: 'Bern',          transferMin: 7.5 },
  basel:      { station: 'Basel SBB',     transferMin: 7.5 },
  luzern:     { station: 'Luzern',        transferMin: 10 },
  geneve:     { station: 'Genève',        transferMin: 12.5 },
  lausanne:   { station: 'Lausanne',      transferMin: 10 },
  stgallen:   { station: 'St. Gallen',    transferMin: 10 },
  lugano:     { station: 'Lugano',        transferMin: 12.5 },
  winterthur: { station: 'Winterthur',    transferMin: 10 },
  biel:       { station: 'Biel/Bienne',   transferMin: 12.5 },
}
const HUB_SHORT_DISTANCE_KM = 15   // Below this, hub routing overestimates — use car-ratio
const SBB_QUERY_DATE = '2026-03-16' // Monday for realistic commuter schedule
const SBB_QUERY_TIME = '07:00'

/** Parse SBB duration string like "00d03:24:00" → seconds */
function parseSbbDuration(str) {
  if (!str) return null
  const m = str.match(/(\d+)d(\d+):(\d+):(\d+)/)
  if (!m) return null
  return parseInt(m[1], 10) * 86400 + parseInt(m[2], 10) * 3600 +
         parseInt(m[3], 10) * 60 + parseInt(m[4], 10)
}

/** Haversine distance in km between two WGS84 points */
function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLon = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2
  return R * 2 * Math.asin(Math.sqrt(a))
}

/**
 * Fetch PT times from a location to all 10 hub cities via SBB API.
 * Makes 10 parallel requests. Filters overnight connections.
 * Returns { hubId: seconds } for successful hubs.
 */
async function fetchSbbHubTimes(lat, lon) {
  const hubTimes = {}
  const promises = Object.entries(HUB_CITIES).map(async ([hubId, hub]) => {
    try {
      const url = `https://transport.opendata.ch/v1/connections?from=${lat},${lon}` +
        `&to=${encodeURIComponent(hub.station)}&date=${SBB_QUERY_DATE}` +
        `&time=${SBB_QUERY_TIME}&isArrivalTime=0&limit=4`
      const resp = await fetch(url)
      if (!resp.ok) {
        console.warn(`[SBB] ${hubId}: HTTP ${resp.status}`)
        return
      }
      const data = await resp.json()
      const connections = data.connections || []

      // Find best non-overnight connection
      let bestSec = null
      for (const conn of connections) {
        const dur = parseSbbDuration(conn.duration)
        if (dur == null) continue
        if (dur > 12 * 3600) continue  // skip overnight (>12h)
        // Skip departures before 05:00
        const depTime = conn.from?.departure
        if (depTime) {
          const depHour = new Date(depTime).getHours()
          if (depHour < 5) continue
        }
        if (bestSec == null || dur < bestSec) {
          bestSec = dur
        }
      }

      if (bestSec != null) {
        hubTimes[hubId] = bestSec
      }
    } catch (err) {
      console.warn(`[SBB] ${hubId}: fetch error`, err.message)
    }
  })

  await Promise.all(promises)
  return hubTimes
}

/**
 * Compute hub-routed PT time for a settlement.
 * Formula: min over all hubs { settlement→hub + custom→hub + transfer_time }
 * @param {Object} settlementPtTimes - settlement's existing PT times to cities (seconds)
 * @param {Object} customToHubTimes - custom location's SBB times to hubs (seconds)
 * @returns {number|null} estimated PT seconds, or null if insufficient data
 */
function computeHubRoutedPtTime(settlementPtTimes, customToHubTimes) {
  let bestSec = null
  for (const [hubId, hub] of Object.entries(HUB_CITIES)) {
    const customToHub = customToHubTimes[hubId]
    const settlementToHub = settlementPtTimes[hubId]
    if (customToHub == null || settlementToHub == null) continue

    const totalSec = settlementToHub + customToHub + hub.transferMin * 60
    if (bestSec == null || totalSec < bestSec) {
      bestSec = totalSec
    }
  }
  return bestSec
}

/**
 * Recompute all metrics from raw travel times and prices.
 * All metrics are AVERAGES over SELECTED (enabled) reference locations only.
 * No walk deduction — PT times used as-is from the TravelTime API.
 * Comfort factors applied only where explicitly needed (AV upside).
 * PT comfort factor applies only to in-vehicle time (IVT) when breakdown
 * data is available; walk/wait time kept at face value.
 */
function recomputeScores(geojson, enabledCities, customLocations, refMaxTimes, modelParams, colorBy) {
  if (!geojson) return null
  const { avFactor, ptFactor, vtt = 30, capRate = 0.04 } = modelParams

  function parseTimes(raw) {
    return typeof raw === 'string' ? JSON.parse(raw) : raw || {}
  }

  // Combine all reference IDs (city IDs + custom location IDs) with their max time constraint
  const allRefs = []
  enabledCities.forEach((id) => {
    allRefs.push({ id, maxMinutes: refMaxTimes[id] ?? null })
  })
  if (customLocations) {
    customLocations.forEach((loc) => {
      if (loc.id && loc.enabled) {
        allRefs.push({ id: loc.id, maxMinutes: refMaxTimes[loc.id] ?? null })
      }
    })
  }
  const allRefIds = allRefs.map((r) => r.id)

  const features = geojson.features.map((f) => {
    const p = f.properties
    const driveTimes = parseTimes(p.drive_times)
    const ptTimes = parseTimes(p.pt_times)
    const ptBreakdown = typeof p.pt_breakdown === 'string' ? JSON.parse(p.pt_breakdown) : p.pt_breakdown || null

    // --- Check max-time constraints ---
    // Which travel mode to check depends on the active visualization:
    //   avg_car_access → car only
    //   avg_pt_access → PT only
    //   everything else → min(car, PT) = best available
    let isExcluded = false
    for (const ref of allRefs) {
      if (ref.maxMinutes == null) continue
      const driveS = driveTimes[ref.id]
      const ptS = ptTimes[ref.id]

      let checkTime = Infinity
      if (colorBy === 'avg_car_access') {
        checkTime = driveS != null ? driveS / 60 : Infinity
      } else if (colorBy === 'avg_pt_access') {
        checkTime = ptS != null ? ptS / 60 : Infinity
      } else {
        const candidates = []
        if (driveS != null) candidates.push(driveS / 60)
        if (ptS != null) candidates.push(ptS / 60)
        checkTime = candidates.length > 0 ? Math.min(...candidates) : Infinity
      }

      if (checkTime > ref.maxMinutes) {
        isExcluded = true
        break
      }
    }

    // --- Compute metrics over selected refs (averages) ---
    let carSum = 0, carCount = 0
    let ptSum = 0, ptCount = 0
    let optimumSum = 0, optimumCount = 0
    // For AV upside: need both modes available per city
    let ptComfortSum = 0, avDriveSum = 0, manualDriveSum = 0, bothCount = 0

    for (const ref of allRefs) {
      const driveS = driveTimes[ref.id]
      const ptS = ptTimes[ref.id]

      // Plausibility filter: skip city pairs where PT/car ratio > 3.5
      // (bad SBB routing, e.g. overnight connection) or car/PT > 3.5
      const ratio = (ptS != null && driveS != null && driveS > 0) ? ptS / driveS : null
      const ptImplausible = ratio != null && ratio > 3.5
      const carImplausible = ratio != null && ratio < (1 / 3.5)

      const carMin = driveS != null && !carImplausible ? driveS / 60 : null
      const ptMin = ptS != null && !ptImplausible ? ptS / 60 : null

      if (carMin != null) { carSum += carMin; carCount++ }
      if (ptMin != null) { ptSum += ptMin; ptCount++ }

      // Optimum: min(car, pt) per city — use whichever mode is available
      if (carMin != null && ptMin != null) {
        optimumSum += Math.min(carMin, ptMin)
        optimumCount++
      } else if (carMin != null) {
        optimumSum += carMin
        optimumCount++
      } else if (ptMin != null) {
        optimumSum += ptMin
        optimumCount++
      }

      // AV upside: need both modes for this city
      if (carMin != null && ptMin != null) {
        // PT comfort: apply ptFactor only to in-vehicle time (IVT),
        // keep walk/wait at face value (no comfort discount for those)
        const bd = ptBreakdown?.[ref.id]
        if (bd) {
          const walkMin = bd.w / 60
          const waitMin = bd.t / 60
          const ivtMin = bd.i / 60
          ptComfortSum += ivtMin * ptFactor + walkMin + waitMin
        } else {
          // Fallback: apply ptFactor to entire PT time (no breakdown available)
          ptComfortSum += ptMin * ptFactor
        }
        avDriveSum += carMin * avFactor
        manualDriveSum += carMin
        bothCount++
      }
    }

    const avgCar = carCount > 0 ? carSum / carCount : null
    const avgPt = ptCount > 0 ? ptSum / ptCount : null
    const optimumAccess = optimumCount > 0 ? optimumSum / optimumCount : null

    // Delta: positive = car slower = PT advantage
    const carPtDeltaMin = avgCar != null && avgPt != null ? avgCar - avgPt : null

    // AV upside: minutes saved by AV vs the current best option
    // (PT comfort or manual drive, whichever is faster today).
    // AV beats manual drive everywhere (av_factor < 1), so the upside is
    // always vs. the current best mode — not conditioned on PT winning.
    let avUpside = null
    if (bothCount > 0) {
      const avgPtComfort = ptComfortSum / bothCount
      const avgAvDrive = avDriveSum / bothCount
      const avgManualDrive = manualDriveSum / bothCount
      const bestCurrent = Math.min(avgPtComfort, avgManualDrive)

      if (avgAvDrive < bestCurrent) {
        avUpside = bestCurrent - avgAvDrive  // minutes AV beats the current best
      }
    }

    // AV Value Unlock: capitalize AV upside minutes into CHF
    // Formula: upside_min × 2 trips × 220 workdays × (VTT_per_hour / 60) / cap_rate
    const avValueUnlock = avUpside != null
      ? Math.round(avUpside * 2 * 220 * (vtt / 60) / capRate)
      : null

    // Min drive/pt for enabled refs (for detail panel)
    const enabledDrive = allRefIds.map((c) => driveTimes[c]).filter((v) => v != null)
    const enabledPt = allRefIds.map((c) => ptTimes[c]).filter((v) => v != null)

    return {
      ...f,
      properties: {
        ...p,
        excluded: isExcluded,
        avg_car_access: avgCar != null ? Math.round(avgCar * 10) / 10 : null,
        avg_pt_access: avgPt != null ? Math.round(avgPt * 10) / 10 : null,
        optimum_access: optimumAccess != null ? Math.round(optimumAccess * 10) / 10 : null,
        car_pt_delta_min: carPtDeltaMin != null ? Math.round(carPtDeltaMin * 10) / 10 : null,
        av_upside: avUpside != null ? Math.round(avUpside * 10) / 10 : null,
        av_value_unlock: avValueUnlock,
        min_drive_s: enabledDrive.length ? Math.min(...enabledDrive) : p.min_drive_s,
        min_pt_s: enabledPt.length ? Math.min(...enabledPt) : p.min_pt_s,
      },
    }
  })

  return { ...geojson, features }
}

// --- URL state helpers ---
function getUrlState() {
  try {
    const params = new URLSearchParams(window.location.search)
    const state = {}
    if (params.get('colorBy')) state.colorBy = params.get('colorBy')
    if (params.get('cities')) state.enabledCities = params.get('cities').split(',')
    if (params.get('avFactor')) state.avFactor = parseFloat(params.get('avFactor'))
    if (params.get('ptFactor')) state.ptFactor = parseFloat(params.get('ptFactor'))
    return state
  } catch { return {} }
}

function setUrlState(state) {
  try {
    const params = new URLSearchParams()
    if (state.colorBy && state.colorBy !== 'avg_car_access') params.set('colorBy', state.colorBy)
    if (state.enabledCities) params.set('cities', state.enabledCities.join(','))
    if (state.avFactor != null && state.avFactor !== DEFAULT_MODEL_PARAMS.avFactor) params.set('avFactor', state.avFactor.toFixed(2))
    if (state.ptFactor != null && state.ptFactor !== DEFAULT_MODEL_PARAMS.ptFactor) params.set('ptFactor', state.ptFactor.toFixed(2))
    const str = params.toString()
    const newUrl = str ? `${window.location.pathname}?${str}` : window.location.pathname
    window.history.replaceState(null, '', newUrl)
  } catch { /* ignore */ }
}

export default function App() {
  const urlState = useMemo(() => getUrlState(), [])

  const [rawData, setRawData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [hovered, setHovered] = useState(null)

  // Validate colorBy from URL (backward compat: old metrics redirect to default)
  const initialColorBy = urlState.colorBy && VALID_METRICS.includes(urlState.colorBy)
    ? urlState.colorBy : 'avg_car_access'
  const [colorBy, setColorBy] = useState(initialColorBy)

  // All available cities from metadata, and which are enabled
  const [allCities, setAllCities] = useState({})
  const [enabledCities, setEnabledCities] = useState([])

  // Custom reference locations (user-added addresses)
  const [customLocations, setCustomLocations] = useState([])

  // Per-reference max travel time in minutes (refId → minutes, null = no limit)
  const [refMaxTimes, setRefMaxTimes] = useState({})

  // Model parameters (comfort factors)
  const [modelParams, setModelParams] = useState({
    avFactor: urlState.avFactor ?? DEFAULT_MODEL_PARAMS.avFactor,
    ptFactor: urlState.ptFactor ?? DEFAULT_MODEL_PARAMS.ptFactor,
  })

  // Sync state → URL (debounced)
  useEffect(() => {
    const t = setTimeout(() => {
      setUrlState({
        colorBy,
        enabledCities,
        avFactor: modelParams.avFactor,
        ptFactor: modelParams.ptFactor,
      })
    }, 300)
    return () => clearTimeout(t)
  }, [colorBy, enabledCities, modelParams])

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: Failed to load data`)
        return r.json()
      })
      .then((geojson) => {
        setRawData(geojson)
        const cities = geojson.metadata?.cities || {}
        setAllCities(cities)
        // Use URL state cities if valid, otherwise enable all
        if (urlState.enabledCities && urlState.enabledCities.every(c => c in cities)) {
          setEnabledCities(urlState.enabledCities)
        } else {
          setEnabledCities(Object.keys(cities))
        }
        setLoading(false)
      })
      .catch((err) => {
        console.error('Failed to load data:', err)
        setLoadError(err.message)
        setLoading(false)
      })
  }, [])

  // Geoapify API key for frontend routing (custom locations)
  const geoapifyKey = import.meta.env.VITE_GEOAPIFY_API_KEY || ''

  // Add a custom reference location
  const addCustomLocation = useCallback((location) => {
    setCustomLocations((prev) => [...prev, { ...location, enabled: true, loading: true }])

    const fetchRoutingTimes = async () => {
      if (!rawData) return
      const features = rawData.features
      const driveResults = {}

      // Fire SBB hub routing immediately (runs in parallel with Geoapify)
      // SBB takes ~1-3s (10 parallel requests) vs Geoapify's ~30-60s
      const sbbPromise = fetchSbbHubTimes(location.lat, location.lon)

      // Geoapify driving times (sequential batches)
      if (geoapifyKey) {
        const BATCH_SIZE = 100
        const batches = []
        for (let i = 0; i < features.length; i += BATCH_SIZE) {
          batches.push(features.slice(i, i + BATCH_SIZE).map((f, idx) => ({ f, origIdx: i + idx })))
        }

        console.log(`[Custom location] Fetching ${batches.length} batches from Geoapify...`)

        for (let batchIdx = 0; batchIdx < batches.length; batchIdx++) {
          const batch = batches[batchIdx]
          const payload = {
            mode: 'drive',
            traffic: 'approximated',
            sources: [{ location: [location.lon, location.lat] }],
            targets: batch.map(({ f }) => ({
              location: [f.geometry.coordinates[0], f.geometry.coordinates[1]],
            })),
          }

          try {
            const resp = await fetch(
              `https://api.geoapify.com/v1/routematrix?apiKey=${geoapifyKey}`,
              {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
              }
            )

            if (resp.status === 429) {
              console.warn(`[Custom location] Rate limited at batch ${batchIdx + 1}, using haversine for remaining`)
              break
            }

            if (!resp.ok) {
              console.warn(`[Custom location] Geoapify error ${resp.status}, using haversine for batch ${batchIdx + 1}`)
              continue
            }

            const data = await resp.json()
            const s2t = data.sources_to_targets || []

            if (s2t.length > 0) {
              const row = s2t[0]
              row.forEach((cell, tgtIdx) => {
                const origIdx = batch[tgtIdx].origIdx
                const driveTime = cell.time != null ? Math.round(cell.time) : null
                driveResults[origIdx] = { drive_s: driveTime }
              })
            }
          } catch (err) {
            console.warn(`[Custom location] Fetch error batch ${batchIdx + 1}:`, err.message)
          }

          if (batchIdx < batches.length - 1) {
            await new Promise((r) => setTimeout(r, 200))
          }
        }

        console.log(`[Custom location] Got Geoapify times for ${Object.keys(driveResults).length}/${features.length} settlements`)
      }

      // Wait for SBB hub routing (should be done by now)
      const hubTimes = await sbbPromise
      const hubCount = Object.keys(hubTimes).length
      console.log(`[SBB] Got hub times for ${hubCount}/10 hubs`, hubTimes)
      const useHubRouting = hubCount >= 3

      setRawData((prevData) => {
        if (!prevData) return prevData
        const newFeatures = prevData.features.map((f, i) => {
          const p = f.properties
          const driveTimes = typeof p.drive_times === 'string' ? JSON.parse(p.drive_times) : { ...p.drive_times }
          const ptTimes = typeof p.pt_times === 'string' ? JSON.parse(p.pt_times) : { ...p.pt_times }

          const coords = f.geometry.coordinates

          // Drive time: Geoapify result or haversine fallback
          let driveSec
          if (driveResults[i] && driveResults[i].drive_s != null) {
            driveSec = driveResults[i].drive_s
          } else {
            driveSec = Math.round((haversineKm(location.lat, location.lon, coords[1], coords[0]) / 50) * 3600)
          }

          // PT time: hub routing with car-ratio fallback
          const distKm = haversineKm(location.lat, location.lon, coords[1], coords[0])

          // Car-ratio estimate (always computed as fallback/safety bound)
          const ptRatio = driveSec < 1800 ? 1.3 : driveSec < 4800 ? 1.5 : 1.8
          const carRatioPt = Math.round(driveSec * ptRatio)

          let ptSec
          if (!useHubRouting || distKm < HUB_SHORT_DISTANCE_KM) {
            // Short distance (<15km) or SBB failed: use car-ratio
            ptSec = carRatioPt
          } else {
            // Hub routing: settlement→hub (existing data) + custom→hub (SBB) + transfer
            const hubRoutedSec = computeHubRoutedPtTime(ptTimes, hubTimes)
            if (hubRoutedSec != null) {
              // Take minimum of hub-routed and car-ratio as safety bound
              // (hub routing is upper bound; car-ratio catches direct regional connections)
              ptSec = Math.min(hubRoutedSec, carRatioPt)
            } else {
              ptSec = carRatioPt
            }
          }

          driveTimes[location.id] = driveSec
          ptTimes[location.id] = ptSec

          return {
            ...f,
            properties: { ...p, drive_times: driveTimes, pt_times: ptTimes },
          }
        })
        return { ...prevData, features: newFeatures }
      })

      setCustomLocations((prev) =>
        prev.map((l) => (l.id === location.id ? { ...l, loading: false } : l))
      )
    }

    fetchRoutingTimes()
  }, [rawData, geoapifyKey])

  const removeCustomLocation = useCallback((locId) => {
    setCustomLocations((prev) => prev.filter((l) => l.id !== locId))
  }, [])

  const toggleCustomLocation = useCallback((locId) => {
    setCustomLocations((prev) =>
      prev.map((l) => (l.id === locId ? { ...l, enabled: !l.enabled } : l))
    )
  }, [])

  const setRefMaxTime = useCallback((refId, minutes) => {
    setRefMaxTimes((prev) => ({ ...prev, [refId]: minutes }))
  }, [])

  // Reset everything to defaults
  const resetToDefaults = useCallback(() => {
    setColorBy('avg_car_access')
    setModelParams(DEFAULT_MODEL_PARAMS)
    setEnabledCities(Object.keys(allCities))
    setRefMaxTimes({})
    setCustomLocations([])
    setSelected(null)
    window.history.replaceState(null, '', window.location.pathname)
  }, [allCities])

  const data = useMemo(
    () => recomputeScores(rawData, enabledCities, customLocations, refMaxTimes, modelParams, colorBy),
    [rawData, enabledCities, customLocations, refMaxTimes, modelParams, colorBy]
  )

  // Compute percentile-based color bounds for dynamic map scales
  const colorBounds = useMemo(() => {
    if (!data) return {}

    function getValues(prop) {
      return data.features
        .filter(f => f.properties[prop] != null && !f.properties.excluded)
        .map(f => f.properties[prop])
    }

    function pctl(sorted, p) {
      const idx = Math.floor(sorted.length * p / 100)
      return sorted[Math.min(idx, sorted.length - 1)]
    }

    function bounds(prop) {
      const vals = getValues(prop)
      if (vals.length === 0) return null
      vals.sort((a, b) => a - b)
      return {
        p2: pctl(vals, 2),
        p10: pctl(vals, 10),
        p25: pctl(vals, 25),
        p50: pctl(vals, 50),
        p75: pctl(vals, 75),
        p90: pctl(vals, 90),
        p98: pctl(vals, 98),
        min: vals[0],
        max: vals[vals.length - 1],
      }
    }

    return {
      avg_car_access: bounds('avg_car_access'),
      avg_pt_access: bounds('avg_pt_access'),
      optimum_access: bounds('optimum_access'),
      car_pt_delta_min: bounds('car_pt_delta_min'),
      av_upside: bounds('av_upside'),
      av_value_unlock: bounds('av_value_unlock'),
    }
  }, [data])

  const handleSelect = useCallback((feature) => {
    setSelected((prev) => {
      if (!feature) return null
      return feature
    })
  }, [])

  // Keep selected feature in sync with recomputed scores
  const resolvedSelected = useMemo(() => {
    if (!selected || !data) return null
    const id = selected.properties?.id
    const fresh = data.features.find((f) => f.properties.id === id)
    return fresh || selected
  }, [selected, data])

  const handleHover = useCallback((feature, point) => {
    setHovered(feature ? { feature, point } : null)
  }, [])

  const handleSelectFromSearch = useCallback((feature) => {
    setSelected(feature)
  }, [])

  const toggleCity = useCallback((cityId) => {
    setEnabledCities((prev) => {
      if (prev.includes(cityId)) {
        return prev.filter((c) => c !== cityId)
      }
      return [...prev, cityId]
    })
  }, [])

  // Tooltip value based on current colorBy
  const tooltipValue = useMemo(() => {
    if (!hovered) return null
    const p = hovered.feature.properties
    const val = p[colorBy]
    if (val == null) return 'No data'
    if (['avg_car_access', 'avg_pt_access', 'optimum_access'].includes(colorBy)) {
      return `${val.toFixed(1)} min`
    }
    if (colorBy === 'av_upside') {
      return `${val.toFixed(1)} min saved`
    }
    if (colorBy === 'av_value_unlock') {
      return `CHF ${Math.round(val).toLocaleString()}`
    }
    if (colorBy === 'car_pt_delta_min') {
      return `${val > 0 ? '+' : ''}${val.toFixed(1)} min`
    }
    if (colorBy === 'chf_per_m2') {
      return `${val.toLocaleString()} CHF/m\u00b2`
    }
    return `${val.toFixed(1)}`
  }, [hovered, colorBy])

  return (
    <div className="app">
      <div className="map-container">
        {loading && (
          <div className="loading-overlay">
            <div className="loading-spinner" />
          </div>
        )}
        {loadError && !loading && (
          <div className="loading-overlay" style={{ background: 'rgba(20,20,30,0.95)' }}>
            <div style={{ textAlign: 'center', color: '#fff', maxWidth: 400, padding: 24 }}>
              <div style={{ fontSize: 48, marginBottom: 16 }}>⚠️</div>
              <h2 style={{ marginBottom: 8 }}>Failed to load data</h2>
              <p style={{ color: 'rgba(255,255,255,0.6)', marginBottom: 16 }}>{loadError}</p>
              <button
                onClick={() => window.location.reload()}
                style={{
                  padding: '8px 24px', borderRadius: 6, border: 'none',
                  background: 'var(--accent-blue, #1976d2)', color: '#fff', cursor: 'pointer',
                  fontSize: 14,
                }}
              >
                Retry
              </button>
            </div>
          </div>
        )}
        <MapView
          data={data}
          colorBy={colorBy}
          colorBounds={colorBounds}
          onSelect={handleSelect}
          onHover={handleHover}
          selected={resolvedSelected}
        />
        {hovered && (
          <div
            className="map-tooltip"
            style={{
              left: hovered.point.x + 12,
              top: hovered.point.y - 12,
            }}
          >
            <div className="map-tooltip-name">
              {hovered.feature.properties.settlement_name || hovered.feature.properties.name}
              {hovered.feature.properties.settlement_name && hovered.feature.properties.settlement_name !== hovered.feature.properties.name && (
                <span style={{ opacity: 0.6, marginLeft: 4, fontSize: '0.85em' }}>
                  ({hovered.feature.properties.name})
                </span>
              )}
            </div>
            <div className="map-tooltip-score">
              {tooltipValue}
            </div>
          </div>
        )}
      </div>
      <SidePanel
        data={data}
        selected={resolvedSelected}
        colorBy={colorBy}
        setColorBy={setColorBy}
        allCities={allCities}
        enabledCities={enabledCities}
        toggleCity={toggleCity}
        customLocations={customLocations}
        addCustomLocation={addCustomLocation}
        removeCustomLocation={removeCustomLocation}
        toggleCustomLocation={toggleCustomLocation}
        refMaxTimes={refMaxTimes}
        setRefMaxTime={setRefMaxTime}
        modelParams={modelParams}
        setModelParams={setModelParams}
        onClose={() => setSelected(null)}
        onSelectFeature={handleSelectFromSearch}
        resetToDefaults={resetToDefaults}
      />
    </div>
  )
}
