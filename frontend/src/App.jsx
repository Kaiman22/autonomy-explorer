import React, { useState, useEffect, useCallback, useMemo } from 'react'
import MapView from './layers/MapView'
import SidePanel from './panels/SidePanel'

const DATA_URL = './data/municipalities_scored.geojson'

// Default model parameters (can be adjusted via UI sliders)
const DEFAULT_MODEL_PARAMS = {
  avFactor: 0.70,     // AV comfort factor (0.5 = very comfortable, 1.0 = same as driving)
  ptFactor: 1.00,     // PT comfort factor (1.0 = same burden as driving, lower = PT feels easier)
}

// Valid colorBy metrics (for URL backward-compat validation)
const VALID_METRICS = [
  'avg_car_access', 'avg_pt_access', 'optimum_access',
  'car_pt_delta_min', 'car_pt_delta_pct', 'av_upside', 'chf_per_m2',
]

/**
 * Recompute all metrics from raw travel times and prices.
 * All metrics are AVERAGES over SELECTED (enabled) reference locations only.
 * No walk deduction — PT times used as-is from the TravelTime API.
 * Comfort factors applied only where explicitly needed (AV upside).
 */
function recomputeScores(geojson, enabledCities, customLocations, refMaxTimes, modelParams, colorBy) {
  if (!geojson) return null
  const { avFactor, ptFactor } = modelParams

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
        ptComfortSum += ptMin * ptFactor
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
    const avgMid = avgCar != null && avgPt != null ? (avgCar + avgPt) / 2 : null
    const carPtDeltaPct = avgMid != null && avgMid > 0
      ? ((avgCar - avgPt) / avgMid) * 100 : null

    // AV upside: condition on averages across selected cities
    // avg_av_drive < avg_pt_comfort < avg_manual_drive
    // = places where PT is currently better than manual driving,
    //   but AV would beat PT. These are the AV opportunity zones.
    let avUpside = null
    if (bothCount > 0) {
      const avgPtComfort = ptComfortSum / bothCount
      const avgAvDrive = avDriveSum / bothCount
      const avgManualDrive = manualDriveSum / bothCount

      if (avgAvDrive < avgPtComfort && avgPtComfort < avgManualDrive) {
        avUpside = avgPtComfort - avgAvDrive  // minutes of AV advantage over PT
      }
    }

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
        car_pt_delta_pct: carPtDeltaPct != null ? Math.round(carPtDeltaPct * 10) / 10 : null,
        av_upside: avUpside != null ? Math.round(avUpside * 10) / 10 : null,
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
      const results = {}

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
                results[origIdx] = { drive_s: driveTime }
              })
            }
          } catch (err) {
            console.warn(`[Custom location] Fetch error batch ${batchIdx + 1}:`, err.message)
          }

          if (batchIdx < batches.length - 1) {
            await new Promise((r) => setTimeout(r, 200))
          }
        }

        console.log(`[Custom location] Got Geoapify times for ${Object.keys(results).length}/${features.length} settlements`)
      }

      setRawData((prevData) => {
        if (!prevData) return prevData
        const newFeatures = prevData.features.map((f, i) => {
          const p = f.properties
          const driveTimes = typeof p.drive_times === 'string' ? JSON.parse(p.drive_times) : { ...p.drive_times }
          const ptTimes = typeof p.pt_times === 'string' ? JSON.parse(p.pt_times) : { ...p.pt_times }

          let driveSec
          if (results[i] && results[i].drive_s != null) {
            driveSec = results[i].drive_s
          } else {
            const coords = f.geometry.coordinates
            const R = 6371
            const dLat = ((location.lat - coords[1]) * Math.PI) / 180
            const dLon = ((location.lon - coords[0]) * Math.PI) / 180
            const a =
              Math.sin(dLat / 2) ** 2 +
              Math.cos((coords[1] * Math.PI) / 180) *
                Math.cos((location.lat * Math.PI) / 180) *
                Math.sin(dLon / 2) ** 2
            const dist = R * 2 * Math.asin(Math.sqrt(a))
            driveSec = Math.round((dist / 50) * 3600)
          }

          const ptRatio = driveSec < 1800 ? 1.3 : driveSec < 4800 ? 1.5 : 1.8
          const ptSec = Math.round(driveSec * ptRatio)

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
      car_pt_delta_pct: bounds('car_pt_delta_pct'),
      av_upside: bounds('av_upside'),
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
    if (colorBy === 'car_pt_delta_min') {
      return `${val > 0 ? '+' : ''}${val.toFixed(1)} min`
    }
    if (colorBy === 'car_pt_delta_pct') {
      return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`
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
              <div style={{ fontSize: 48, marginBottom: 16 }}>\u26a0\ufe0f</div>
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
              {hovered.feature.properties.name}
              {hovered.feature.properties.settlement_name && hovered.feature.properties.settlement_name !== hovered.feature.properties.name && (
                <span style={{ opacity: 0.6, marginLeft: 4, fontSize: '0.85em' }}>
                  {hovered.feature.properties.settlement_name}
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
