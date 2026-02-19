import React, { useState, useEffect, useCallback, useMemo } from 'react'
import MapView from './layers/MapView'
import SidePanel from './panels/SidePanel'

const DATA_URL = './data/municipalities_scored.geojson'

// Default model parameters (can be adjusted via UI sliders)
const DEFAULT_MODEL_PARAMS = {
  avFactor: 0.70,     // AV comfort factor (0.5 = very comfortable, 1.0 = same as driving)
  ptFactor: 0.70,     // PT comfort factor (0.5 = very comfortable, 1.0 = same as driving)
}

/**
 * Recompute all metrics from raw travel times, prices —
 * accounting for which cities/custom locations are enabled, max travel time
 * constraints per ref, and the scoring weights.
 *
 * Each ref has an optional max travel time. Municipalities that exceed ANY
 * ref's max-time constraint are excluded from scoring and normalization.
 * Aggregation: simple average across enabled refs.
 */
function recomputeScores(geojson, weights, enabledCities, customLocations, refMaxTimes, modelParams) {
  if (!geojson) return null
  const { avFactor, ptFactor } = modelParams

  function parseTimes(raw) {
    return typeof raw === 'string' ? JSON.parse(raw) : raw || {}
  }

  // Combine all reference IDs (city IDs + custom location IDs) with their max time constraint
  const allRefs = [] // { id, maxMinutes }
  enabledCities.forEach((id) => {
    allRefs.push({ id, maxMinutes: refMaxTimes[id] ?? null }) // null = no limit
  })
  if (customLocations) {
    customLocations.forEach((loc) => {
      if (loc.id && loc.enabled) {
        allRefs.push({ id: loc.id, maxMinutes: refMaxTimes[loc.id] ?? null })
      }
    })
  }
  const allRefIds = allRefs.map((r) => r.id)

  // First pass: collect raw values for normalization
  const rawStatusQuo = []       // status-quo accessibility (lower = better today)
  const rawPostAV = []          // post-AV accessibility (lower = better with AV)
  const rawDelta = []           // delta = status_quo - post_AV (higher = more gain)
  const excluded = []           // true if municipality violates any max-time constraint

  // Temporary arrays for peer-group benchmarking
  const sqPricePairs = []       // { index, sq, price } for municipalities with both values

  geojson.features.forEach((f, i) => {
    const p = f.properties
    const driveTimes = parseTimes(p.drive_times)
    const ptTimes = parseTimes(p.pt_times)

    // --- Check max-time constraints ---
    let isExcluded = false
    for (const ref of allRefs) {
      if (ref.maxMinutes == null) continue // no limit set
      const driveS = driveTimes[ref.id]
      const ptS = ptTimes[ref.id]

      // Status quo time to this ref (best of driving or PT comfort-weighted)
      const candidates = []
      if (driveS != null) candidates.push(driveS / 60)
      if (ptS != null) candidates.push((ptS / 60) * ptFactor)
      const bestTime = candidates.length > 0 ? Math.min(...candidates) : Infinity

      if (bestTime > ref.maxMinutes) {
        isExcluded = true
        break
      }
    }
    excluded.push(isExcluded)

    // If excluded, still compute values (for detail panel) but they won't
    // participate in normalization
    // --- Average aggregation across refs ---
    let sqWeightedSum = 0
    let sqWeightTotal = 0
    let avWeightedSum = 0
    let avWeightTotal = 0

    for (const ref of allRefs) {
      const driveS = driveTimes[ref.id]
      const ptS = ptTimes[ref.id]

      // Status quo: best of manual drive or PT for this ref
      const candidates = []
      if (driveS != null) candidates.push(driveS / 60)
      if (ptS != null) candidates.push((ptS / 60) * ptFactor)
      if (candidates.length > 0) {
        const sqTime = Math.min(...candidates)
        sqWeightedSum += sqTime
        sqWeightTotal += 1
      }

      // Post-AV: best of AV driving or PT
      const avCandidates = []
      if (driveS != null) avCandidates.push((driveS / 60) * avFactor)
      if (ptS != null) avCandidates.push((ptS / 60) * ptFactor)
      if (avCandidates.length > 0) {
        const avTime = Math.min(...avCandidates)
        avWeightedSum += avTime
        avWeightTotal += 1
      }
    }

    const sq = sqWeightTotal > 0 ? sqWeightedSum / sqWeightTotal : null
    rawStatusQuo.push(sq)

    const av = avWeightTotal > 0 ? avWeightedSum / avWeightTotal : null
    rawPostAV.push(av)

    // Delta: how much AV improves accessibility
    if (sq != null && av != null) {
      rawDelta.push(sq - av)
    } else {
      rawDelta.push(null)
    }

    // Collect pairs for peer-group benchmarking
    const price = p.chf_per_m2
    if (price != null && sq != null && price > 0) {
      sqPricePairs.push({ index: i, sq, price })
    }
  })

  // --- Inherent attractiveness via peer-group price percentile ---
  // For each municipality: find all places with similar status-quo accessibility
  // (within ±15% of commute time), then ask "what % of those peers are cheaper?"
  //
  // High percentile = most peers with similar commute are cheaper = this place is
  // expensive for its accessibility level (Gstaad, St. Moritz).
  // Low percentile = most peers are more expensive = this place is a bargain
  // for its accessibility level (hidden gem).
  //
  // Attractiveness = 100 - percentile (so bargains score high).
  const rawAttractiveness = new Array(geojson.features.length).fill(null)
  const pricePercentile = new Array(geojson.features.length).fill(null)

  if (sqPricePairs.length > 10) {
    // Sort by sq for efficient windowing
    const sorted = [...sqPricePairs].sort((a, b) => a.sq - b.sq)

    for (const entry of sqPricePairs) {
      // Find peers: places with status-quo accessibility within ±15%
      // (but at least ±5 min to avoid tiny peer groups for very short commutes)
      const margin = Math.max(5, entry.sq * 0.15)
      const lo = entry.sq - margin
      const hi = entry.sq + margin

      // Collect peer prices using the sorted array
      const peerPrices = []
      for (const peer of sorted) {
        if (peer.sq < lo) continue
        if (peer.sq > hi) break
        peerPrices.push(peer.price)
      }

      if (peerPrices.length >= 5) {
        // What % of peers are cheaper than this place?
        const cheaper = peerPrices.filter((p) => p < entry.price).length
        const pctile = (cheaper / peerPrices.length) * 100  // 0 = cheapest among peers, 100 = most expensive
        pricePercentile[entry.index] = Math.round(pctile)
        rawAttractiveness[entry.index] = 100 - pctile  // invert: high = bargain
      }
    }
  }

  // Normalize helper — only uses non-excluded municipalities for min/max range
  function normalize(values) {
    const valid = values.filter((v, i) => v !== null && !excluded[i])
    if (valid.length === 0) return values.map(() => null)
    const lo = Math.min(...valid)
    const hi = Math.max(...valid)
    const range = hi - lo || 1
    return values.map((v, i) =>
      v !== null && !excluded[i] ? Math.round(((v - lo) / range) * 1000) / 10 : null
    )
  }

  // Normalize for inverted metrics (lower raw = better = higher score)
  function normalizeInverted(values) {
    const valid = values.filter((v, i) => v !== null && !excluded[i])
    if (valid.length === 0) return values.map(() => null)
    const lo = Math.min(...valid)
    const hi = Math.max(...valid)
    const range = hi - lo || 1
    return values.map((v, i) =>
      v !== null && !excluded[i] ? Math.round(((hi - v) / range) * 1000) / 10 : null
    )
  }

  const normDelta = normalize(rawDelta)             // higher delta = higher score
  const normAttract = normalize(rawAttractiveness)   // higher attract = higher score
  const normSQ = normalizeInverted(rawStatusQuo)     // lower raw SQ = better = higher score
  const normPostAV = normalizeInverted(rawPostAV)    // lower raw postAV = better = higher score

  // Second pass: build enriched features
  const features = geojson.features.map((f, i) => {
    const p = f.properties
    const driveTimes = parseTimes(p.drive_times)
    const ptTimes = parseTimes(p.pt_times)

    const scoreAccess = normDelta[i]
    const scoreAttract = normAttract[i]

    // Weighted combination
    let score = null
    const components = []
    if (scoreAccess !== null) components.push({ v: scoreAccess, w: weights.accessibility_gain })
    if (scoreAttract !== null) components.push({ v: scoreAttract, w: weights.inherent_attractiveness })
    if (components.length > 0) {
      const totalWeight = components.reduce((s, c) => s + c.w, 0)
      if (totalWeight > 0) {
        score = components.reduce((s, c) => s + c.v * c.w, 0) / totalWeight
        score = Math.round(score * 10) / 10
      }
    }

    // Per-city gains for detail panel (all cities, not just enabled)
    const gainPerCity = {}
    for (const [refId, driveS] of Object.entries(driveTimes)) {
      const ptS = ptTimes[refId]
      if (driveS != null && ptS != null) {
        const humanDrive = driveS / 60
        const ptComfort = (ptS / 60) * ptFactor
        const bestToday = Math.min(humanDrive, ptComfort)
        const bestPostAV = Math.min((driveS / 60) * avFactor, ptComfort)
        gainPerCity[refId] = Math.round((bestToday - bestPostAV) * 10) / 10
      }
    }

    // Min drive/pt for enabled refs only
    const enabledDrive = allRefIds
      .map((c) => driveTimes[c])
      .filter((v) => v != null)
    const enabledPt = allRefIds
      .map((c) => ptTimes[c])
      .filter((v) => v != null)

    const isExcl = excluded[i]

    return {
      ...f,
      properties: {
        ...p,
        // Exclusion flag (municipality violates a max-time constraint)
        excluded: isExcl,
        // Raw values (in minutes)
        status_quo_access: rawStatusQuo[i] != null ? Math.round(rawStatusQuo[i] * 10) / 10 : null,
        post_av_access: rawPostAV[i] != null ? Math.round(rawPostAV[i] * 10) / 10 : null,
        delta_accessibility: rawDelta[i] != null ? Math.round(rawDelta[i] * 10) / 10 : null,
        inherent_attractiveness_raw: rawAttractiveness[i] != null ? Math.round(rawAttractiveness[i] * 10) / 10 : null,
        price_percentile: pricePercentile[i],  // "X% of similar-commute places are cheaper"
        // Normalized scores (0-100, higher = better) — null if excluded
        score_accessibility: isExcl ? null : scoreAccess,
        score_attractiveness: isExcl ? null : scoreAttract,
        score_status_quo: isExcl ? null : normSQ[i],
        score_post_av: isExcl ? null : normPostAV[i],
        score_delta: isExcl ? null : normDelta[i],
        // Per-city detail
        gain_per_city: gainPerCity,
        min_drive_s: enabledDrive.length ? Math.min(...enabledDrive) : p.min_drive_s,
        min_pt_s: enabledPt.length ? Math.min(...enabledPt) : p.min_pt_s,
        // Final combined score — null if excluded
        autonomy_score: isExcl ? null : score,
      },
    }
  })

  return { ...geojson, features }
}

export default function App() {
  const [rawData, setRawData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [hovered, setHovered] = useState(null)
  const [colorBy, setColorBy] = useState('autonomy_score')
  const [filterCity, setFilterCity] = useState('best')
  const [weights, setWeights] = useState({
    accessibility_gain: 0.5,
    inherent_attractiveness: 0.5,
  })

  // All available cities from metadata, and which are enabled
  const [allCities, setAllCities] = useState({})
  const [enabledCities, setEnabledCities] = useState([])

  // Custom reference locations (user-added addresses)
  const [customLocations, setCustomLocations] = useState([])

  // Per-reference max travel time in minutes (refId → minutes, null = no limit)
  const [refMaxTimes, setRefMaxTimes] = useState({})

  // Model parameters (comfort factors etc.)
  const [modelParams, setModelParams] = useState(DEFAULT_MODEL_PARAMS)

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((geojson) => {
        setRawData(geojson)
        const cities = geojson.metadata?.cities || {}
        setAllCities(cities)
        setEnabledCities(Object.keys(cities))
        setLoading(false)
      })
      .catch((err) => {
        console.error('Failed to load data:', err)
        setLoading(false)
      })
  }, [])

  // Add a custom reference location
  const addCustomLocation = useCallback((location) => {
    // location: { id, name, lat, lon, enabled }
    setCustomLocations((prev) => [...prev, { ...location, enabled: true }])

    // Compute travel times for this custom location to all municipalities
    // For now use haversine estimate; later can use OSRM API
    setRawData((prevData) => {
      if (!prevData) return prevData
      const newFeatures = prevData.features.map((f) => {
        const p = f.properties
        const driveTimes = typeof p.drive_times === 'string' ? JSON.parse(p.drive_times) : { ...p.drive_times }
        const ptTimes = typeof p.pt_times === 'string' ? JSON.parse(p.pt_times) : { ...p.pt_times }

        // Haversine distance estimate → drive time
        const coords = f.geometry.coordinates // [lon, lat]
        const R = 6371
        const dLat = ((location.lat - coords[1]) * Math.PI) / 180
        const dLon = ((location.lon - coords[0]) * Math.PI) / 180
        const a =
          Math.sin(dLat / 2) ** 2 +
          Math.cos((coords[1] * Math.PI) / 180) *
            Math.cos((location.lat * Math.PI) / 180) *
            Math.sin(dLon / 2) ** 2
        const dist = R * 2 * Math.asin(Math.sqrt(a))

        // Estimate driving time: ~70 km/h avg for Swiss roads
        const driveSec = Math.round((dist / 70) * 3600)
        // Estimate PT: 1.3-1.8x driving based on distance
        const ptRatio = dist < 30 ? 1.2 : dist < 80 ? 1.4 : 1.7
        const ptSec = Math.round(driveSec * ptRatio)

        driveTimes[location.id] = driveSec
        ptTimes[location.id] = ptSec

        return {
          ...f,
          properties: {
            ...p,
            drive_times: driveTimes,
            pt_times: ptTimes,
          },
        }
      })
      return { ...prevData, features: newFeatures }
    })
  }, [])

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

  const data = useMemo(
    () => recomputeScores(rawData, weights, enabledCities, customLocations, refMaxTimes, modelParams),
    [rawData, weights, enabledCities, customLocations, refMaxTimes, modelParams]
  )

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
    // For raw minute values, show as minutes
    if (['status_quo_access', 'post_av_access', 'delta_accessibility'].includes(colorBy)) {
      return `${val.toFixed(1)} min`
    }
    if (colorBy === 'chf_per_m2') {
      return `${val.toLocaleString()} CHF/m²`
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
        <MapView
          data={data}
          colorBy={colorBy}
          filterCity={filterCity}
          weights={weights}
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
        filterCity={filterCity}
        setFilterCity={setFilterCity}
        weights={weights}
        setWeights={setWeights}
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
      />
    </div>
  )
}
