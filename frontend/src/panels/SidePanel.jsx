import React, { useState, useMemo, useCallback } from 'react'

// PT walk deduction — must match App.jsx and config.py
const PT_WALK_DEDUCTION = {
  "> 100'000": 180,
  "50'000 bis 100'000": 240,
  "10'000 bis 49'999": 360,
  "2'000 bis 9'999": 480,
  "1'000 bis 1'999": 600,
  "100 bis 999": 720,
}
const PT_WALK_DEFAULT = 600

function formatTime(seconds) {
  if (seconds == null) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m} min`
  return `${m} min`
}

function formatScore(val) {
  if (val == null) return '—'
  return val.toFixed(1)
}

function formatMinutes(val) {
  if (val == null) return '—'
  return `${val.toFixed(1)} min`
}

// Color-by metric definitions
const METRICS = {
  autonomy_score_rel: {
    label: 'Compound Score (relative)',
    desc: 'Attractiveness + relative gain (%). Distance-neutral — doesn\'t bias towards remote areas',
    unit: '',
    isScore: true,
  },
  autonomy_score_abs: {
    label: 'Compound Score (absolute)',
    desc: 'Attractiveness + absolute gain (min saved). Favors remote areas with long commutes',
    unit: '',
    isScore: true,
  },
  chf_per_m2: {
    label: 'Property Price (CHF/m²)',
    desc: 'Estimated property price per square meter',
    unit: 'CHF/m²',
    isScore: false,
  },
  score_status_quo: {
    label: 'Status-Quo Accessibility',
    desc: 'Today\'s best access: min(PT_comfort, driving) to ref. cities',
    unit: '',
    isScore: true,
  },
  score_attractiveness: {
    label: 'Inherent Attractiveness',
    desc: 'How expensive vs peers with similar commute? High = desirable for non-transport reasons',
    unit: '',
    isScore: true,
  },
  score_post_av: {
    label: 'Post-Autonomy Accessibility',
    desc: 'AV-era accessibility (drive time × 0.7 comfort factor)',
    unit: '',
    isScore: true,
  },
  score_rel_gain: {
    label: 'Relative Accessibility Gain',
    desc: 'What % of commute time does AV eliminate? Doesn\'t bias towards remote areas',
    unit: '',
    isScore: true,
  },
  score_abs_delta: {
    label: 'Absolute Accessibility Gain',
    desc: 'Minutes saved by AV (absolute). Naturally favors remote areas with long commutes',
    unit: '',
    isScore: true,
  },
  score_delta: {
    label: 'Accessibility Delta',
    desc: 'Same as absolute gain (legacy)',
    unit: '',
    isScore: true,
    hidden: true,
  },
  score_accessibility: {
    label: 'Accessibility Gain (normalized)',
    desc: 'Normalized version of relative gain — same as above, 0-100 scale',
    unit: '',
    isScore: true,
    hidden: true,
  },
  avg_car_access: {
    label: 'Average Car Access',
    desc: 'Mean driving time to enabled ref. locations (raw minutes, no comfort weighting)',
    unit: 'min',
    isScore: false,
    sortAscending: true,
  },
  avg_pt_access: {
    label: 'Average PT Access',
    desc: 'Mean public transport time to enabled ref. locations (raw minutes)',
    unit: 'min',
    isScore: false,
    sortAscending: true,
  },
  car_pt_delta_min: {
    label: 'Car \u2212 PT Delta (min)',
    desc: 'Positive = car slower (PT advantage). Negative = PT slower (car advantage)',
    unit: 'min',
    isScore: false,
  },
  car_pt_delta_pct: {
    label: 'Car \u2212 PT Delta (%)',
    desc: 'Relative car/PT difference. Positive = PT advantage, negative = car advantage',
    unit: '%',
    isScore: false,
  },
}

function ScoreBar({ value, label, color }) {
  const width = value != null ? Math.max(0, Math.min(100, value)) : 0
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 11,
          marginBottom: 2,
        }}
      >
        <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
        <span style={{ color: color || 'var(--accent-blue)' }}>{formatScore(value)}</span>
      </div>
      <div
        style={{
          height: 4,
          background: 'var(--border)',
          borderRadius: 2,
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${width}%`,
            borderRadius: 2,
            background:
              value != null
                ? color || `hsl(${(width / 100) * 30 + 220}, 70%, 55%)`
                : 'transparent',
            transition: 'width 0.3s',
          }}
        />
      </div>
    </div>
  )
}

// Normalize accented characters so "zurich" matches "Zürich"
function normalize(str) {
  return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
}

function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value)
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

function SearchBox({ data, onSelect }) {
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const debouncedQuery = useDebounce(query, 150)

  const results = useMemo(() => {
    if (!data || debouncedQuery.length < 2) return []
    const q = normalize(debouncedQuery)
    const isNumeric = /^\d+$/.test(query.trim())

    // Search by name or settlement name
    const matches = data.features.filter((f) => {
      const nameMatch = normalize(f.properties.name).includes(q)
      const settlementMatch = normalize(f.properties.settlement_name || '').includes(q)
      return nameMatch || settlementMatch
    })

    // Deduplicate: for same municipality, pick best-scoring PLZ
    const byMuni = {}
    for (const f of matches) {
      const key = f.properties.municipality_id || f.properties.id
      if (!byMuni[key] || (f.properties.autonomy_score_rel || 0) > (byMuni[key].properties.autonomy_score_rel || 0)) {
        byMuni[key] = f
      }
    }

    return Object.values(byMuni)
      .sort((a, b) => {
        const aName = normalize(a.properties.name)
        const bName = normalize(b.properties.name)
        const aStarts = aName.startsWith(q) ? 0 : 1
        const bStarts = bName.startsWith(q) ? 0 : 1
        if (aStarts !== bStarts) return aStarts - bStarts
        return (b.properties.autonomy_score_rel || 0) - (a.properties.autonomy_score_rel || 0)
      })
      .slice(0, 8)
  }, [data, query])

  return (
    <div className="search-box">
      <input
        type="text"
        placeholder="Search municipality or settlement..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 200)}
        className="search-input"
      />
      {focused && results.length > 0 && (
        <div className="search-results">
          {results.map((f) => (
            <div
              key={f.properties.id}
              className="search-result-item"
              onMouseDown={() => {
                onSelect(f)
                setQuery('')
                setFocused(false)
              }}
            >
              <span className="search-result-name">{f.properties.name}</span>
              <span className="search-result-meta">
                {f.properties.settlement_name && f.properties.settlement_name !== f.properties.name && <>{f.properties.settlement_name} · </>}
                {f.properties.canton_code}
                {f.properties.autonomy_score_rel != null && (
                  <> · {f.properties.autonomy_score_rel.toFixed(1)}</>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function RankedList({ items, onSelect, colorBy, startRank = 1 }) {
  if (items.length === 0) return null

  return (
    <div className="top-list">
      {items.map((f, i) => {
        const val = f.properties[colorBy || 'autonomy_score_rel']
        return (
          <div
            key={f.properties.id}
            className="top-list-item"
            onClick={() => onSelect(f)}
          >
            <span className="top-list-rank">{startRank + i}</span>
            <span className="top-list-name">{f.properties.name}</span>
            <span className="top-list-canton">{f.properties.canton_code}</span>
            <span className="top-list-score">
              {colorBy === 'chf_per_m2'
                ? val?.toLocaleString()
                : val?.toFixed(1)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function useRankedData(data, colorBy) {
  return useMemo(() => {
    if (!data) return { sorted: [], top: [], bottom: [] }
    const prop = colorBy || 'autonomy_score_rel'
    const metric = METRICS[prop] || METRICS.autonomy_score_rel
    const ascending = metric?.sortAscending

    // Deduplicate by municipality: pick the best PLZ per municipality
    const byMuni = {}
    for (const f of data.features) {
      if (f.properties[prop] == null || f.properties.excluded) continue
      const key = f.properties.municipality_id || f.properties.id
      if (!byMuni[key] || (ascending
        ? f.properties[prop] < byMuni[key].properties[prop]
        : f.properties[prop] > byMuni[key].properties[prop])) {
        byMuni[key] = f
      }
    }

    const sorted = Object.values(byMuni)
      .sort((a, b) => ascending
        ? a.properties[prop] - b.properties[prop]
        : b.properties[prop] - a.properties[prop])

    const top = sorted.slice(0, 10)
    const bottom = sorted.length > 10 ? sorted.slice(-10).reverse() : []

    return { sorted, top, bottom }
  }, [data, colorBy])
}

function TopList({ data, onSelect, colorBy }) {
  const { top } = useRankedData(data, colorBy)
  return <RankedList items={top} onSelect={onSelect} colorBy={colorBy} startRank={1} />
}

function BottomList({ data, onSelect, colorBy }) {
  const { sorted, bottom } = useRankedData(data, colorBy)
  if (bottom.length === 0) return null
  // Start rank is total count minus 9 (so the last item is ranked = total)
  const startRank = sorted.length - 9
  return <RankedList items={bottom} onSelect={onSelect} colorBy={colorBy} startRank={startRank} />
}

const MAX_TIME_OPTIONS = [
  { value: null, label: 'Any' },
  { value: 10, label: '10 min' },
  { value: 20, label: '20 min' },
  { value: 30, label: '30 min' },
  { value: 40, label: '40 min' },
  { value: 50, label: '50 min' },
  { value: 60, label: '1 h' },
  { value: 90, label: '1.5 h' },
  { value: 120, label: '2 h' },
  { value: 150, label: '2.5 h' },
  { value: 180, label: '3 h' },
]

function CityCheckboxes({ allCities, enabledCities, toggleCity, refMaxTimes, setRefMaxTime }) {
  const cityEntries = Object.entries(allCities)
  if (cityEntries.length === 0) return null

  return (
    <div className="ref-list">
      {cityEntries.map(([id, name]) => {
        const checked = enabledCities.includes(id)
        const maxTime = refMaxTimes[id] ?? null
        return (
          <div key={id} className="ref-item">
            <label className="city-checkbox-label">
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggleCity(id)}
              />
              <span className={checked ? '' : 'disabled'}>{name}</span>
            </label>
            {checked && (
              <select
                className="ref-max-time-select"
                value={maxTime ?? ''}
                onChange={(e) => setRefMaxTime(id, e.target.value === '' ? null : parseInt(e.target.value))}
                title={maxTime ? `Max ${maxTime} min` : 'No limit'}
              >
                {MAX_TIME_OPTIONS.map((opt) => (
                  <option key={opt.label} value={opt.value ?? ''}>{opt.label}</option>
                ))}
              </select>
            )}
          </div>
        )
      })}
    </div>
  )
}

function MunicipalityPicker({ data, addCustomLocation, customLocations }) {
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)
  const debouncedQuery = useDebounce(query, 150)

  const existingIds = useMemo(() => {
    const ids = new Set()
    if (customLocations) {
      customLocations.forEach((loc) => ids.add(loc.id))
    }
    return ids
  }, [customLocations])

  const results = useMemo(() => {
    if (!data || debouncedQuery.length < 2) return []
    const q = normalize(debouncedQuery)

    const byMuni = {}
    for (const f of data.features) {
      const nameMatch = normalize(f.properties.name).includes(q)
      const settlementMatch = normalize(f.properties.settlement_name || '').includes(q)
      if (!nameMatch && !settlementMatch) continue
      const key = f.properties.municipality_id || f.properties.id
      if (existingIds.has(`custom_muni_${key}`)) continue
      if (!byMuni[key] || (f.properties.autonomy_score_rel || 0) > (byMuni[key].properties.autonomy_score_rel || 0)) {
        byMuni[key] = f
      }
    }

    return Object.values(byMuni)
      .sort((a, b) => {
        const aName = normalize(a.properties.name)
        const bName = normalize(b.properties.name)
        const aStarts = aName.startsWith(q) ? 0 : 1
        const bStarts = bName.startsWith(q) ? 0 : 1
        if (aStarts !== bStarts) return aStarts - bStarts
        return (b.properties.autonomy_score_rel || 0) - (a.properties.autonomy_score_rel || 0)
      })
      .slice(0, 8)
  }, [data, debouncedQuery, existingIds])

  const handleSelect = useCallback((f) => {
    const key = f.properties.municipality_id || f.properties.id
    addCustomLocation({
      id: `custom_muni_${key}`,
      name: f.properties.name,
      lat: f.geometry.coordinates[1],
      lon: f.geometry.coordinates[0],
      enabled: true,
    })
    setQuery('')
    setFocused(false)
  }, [addCustomLocation])

  return (
    <div className="search-box" style={{ marginTop: 6 }}>
      <input
        type="text"
        placeholder="Add reference location (search municipality)..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 200)}
        className="search-input"
        style={{ fontSize: 11 }}
      />
      {focused && results.length > 0 && (
        <div className="search-results">
          {results.map((f) => (
            <div
              key={f.properties.id}
              className="search-result-item"
              onMouseDown={() => handleSelect(f)}
            >
              <span className="search-result-name">{f.properties.name}</span>
              <span className="search-result-meta">{f.properties.canton_code}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CustomLocationsList({ customLocations, toggleCustomLocation, removeCustomLocation, refMaxTimes, setRefMaxTime }) {
  if (customLocations.length === 0) return null

  return (
    <div className="custom-locations-list">
      {customLocations.map((loc) => {
        const maxTime = refMaxTimes[loc.id] ?? null
        return (
          <div key={loc.id} className="ref-item custom-location-item">
            <label className="city-checkbox-label" style={{ flex: 1 }}>
              <input
                type="checkbox"
                checked={loc.enabled}
                onChange={() => toggleCustomLocation(loc.id)}
              />
              <span className={loc.enabled ? '' : 'disabled'}>
                {loc.name}
                {loc.loading && <span style={{ marginLeft: 4, fontSize: '0.8em', opacity: 0.6 }}>loading...</span>}
              </span>
            </label>
            {loc.enabled && (
              <select
                className="ref-max-time-select"
                value={maxTime ?? ''}
                onChange={(e) => setRefMaxTime(loc.id, e.target.value === '' ? null : parseInt(e.target.value))}
                title={maxTime ? `Max ${maxTime} min` : 'No limit'}
              >
                {MAX_TIME_OPTIONS.map((opt) => (
                  <option key={opt.label} value={opt.value ?? ''}>{opt.label}</option>
                ))}
              </select>
            )}
            <button
              onClick={() => removeCustomLocation(loc.id)}
              className="remove-location-btn"
              title="Remove"
            >
              ×
            </button>
          </div>
        )
      })}
    </div>
  )
}

function MunicipalityDetail({ feature, onClose, allCities, enabledCities, customLocations, modelParams, colorBy }) {
  const p = feature.properties
  const driveTimesRaw = p.drive_times
  const ptTimesRaw = p.pt_times

  const driveTimes =
    typeof driveTimesRaw === 'string' ? JSON.parse(driveTimesRaw) : driveTimesRaw || {}
  const ptTimes =
    typeof ptTimesRaw === 'string' ? JSON.parse(ptTimesRaw) : ptTimesRaw || {}

  const { avFactor = 0.7, ptFactor = 0.7 } = modelParams || {}

  const compoundScore = p.autonomy_score_rel
  const scoreColor =
    compoundScore != null
      ? compoundScore > 70
        ? 'var(--accent)'
        : compoundScore > 40
        ? '#f57f17'
        : 'var(--accent-blue)'
      : 'var(--text-secondary)'

  // Active metric display
  const isCompound = colorBy === 'autonomy_score_rel' || colorBy === 'autonomy_score_abs'
  const activeMetric = METRICS[colorBy]
  const activeVal = p[colorBy]
  let activeDisplay = '—'
  if (activeVal != null) {
    if (colorBy === 'chf_per_m2') activeDisplay = `${activeVal.toLocaleString()} CHF/m²`
    else if (activeMetric?.unit === '%') activeDisplay = `${activeVal > 0 ? '+' : ''}${activeVal.toFixed(1)}%`
    else if (activeMetric?.unit === 'min') activeDisplay = `${activeVal.toFixed(1)} min`
    else activeDisplay = formatScore(activeVal)
  }

  // Combine predefined + custom locations
  const allRefs = { ...allCities }
  if (customLocations) {
    customLocations.forEach((loc) => {
      allRefs[loc.id] = loc.name
    })
  }

  // Show enabled first, then disabled
  const enabledSet = new Set(enabledCities)
  if (customLocations) {
    customLocations.forEach((loc) => {
      if (loc.enabled) enabledSet.add(loc.id)
    })
  }

  const sortedRefs = Object.entries(allRefs).sort((a, b) => {
    const aOn = enabledSet.has(a[0]) ? 0 : 1
    const bOn = enabledSet.has(b[0]) ? 0 : 1
    return aOn - bOn
  })

  return (
    <div className="detail-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
        }}
      >
        <div>
          <div className="detail-name">{p.name}</div>
          <div className="detail-canton">
            {p.settlement_name && p.settlement_name !== p.name && <>{p.settlement_name} · </>}{p.canton} ({p.canton_code})
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: 18,
          }}
        >
          ×
        </button>
      </div>

      {isCompound ? (
        <>
          <div className="detail-score" style={{ color: scoreColor }}>
            {formatScore(p.autonomy_score_rel)}
          </div>
          <div className="detail-score-label" style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
            <span>Compound (rel)</span>
            <span style={{ color: 'var(--text-secondary)' }}>|</span>
            <span style={{ color: 'var(--text-secondary)' }}>{formatScore(p.autonomy_score_abs)} (abs)</span>
          </div>
        </>
      ) : (
        <>
          <div className="detail-score" style={{ color: 'var(--accent-blue)' }}>
            {activeDisplay}
          </div>
          <div className="detail-score-label">{activeMetric?.label || colorBy}</div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'center', marginTop: 4 }}>
            Compound: {formatScore(p.autonomy_score_rel)} (rel) · {formatScore(p.autonomy_score_abs)} (abs)
          </div>
        </>
      )}

      <div className="detail-grid">
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.chf_per_m2 != null ? `${p.chf_per_m2.toLocaleString()} CHF/m²` : '—'}
          </div>
          <div className="detail-stat-label">Price / m²</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.tax_multiplier != null ? `${p.tax_multiplier}%` : '—'}
          </div>
          <div className="detail-stat-label">Tax Multiplier</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.status_quo_access)}</div>
          <div className="detail-stat-label">Avg Access (today)</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.post_av_access)}</div>
          <div className="detail-stat-label">Avg Access (AV)</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.delta_accessibility != null ? `${p.delta_accessibility > 0 ? '+' : ''}${formatMinutes(p.delta_accessibility)}` : '—'}
          </div>
          <div className="detail-stat-label">Δ Absolute</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.relative_gain_pct != null ? `${p.relative_gain_pct.toFixed(1)}%` : '—'}
          </div>
          <div className="detail-stat-label">Δ Relative</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatTime(p.min_drive_s)}</div>
          <div className="detail-stat-label">Best Drive</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.avg_car_access)}</div>
          <div className="detail-stat-label">Avg Car</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.avg_pt_access)}</div>
          <div className="detail-stat-label">Avg PT</div>
        </div>
      </div>

      <ScoreBar value={p.score_rel_gain} label="Relative Accessibility Gain" color="var(--accent-blue)" />
      <ScoreBar value={p.score_attractiveness} label="Inherent Attractiveness" color="var(--accent-green)" />
      {p.price_percentile != null && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.3 }}>
          {p.price_percentile < 30
            ? `Bargain: only ${p.price_percentile}% of places with a similar commute are cheaper.`
            : p.price_percentile < 60
            ? `Fair price: ${p.price_percentile}% of places with a similar commute are cheaper.`
            : `Pricey: ${p.price_percentile}% of places with a similar commute are cheaper.`}
        </div>
      )}

      <h3
        style={{
          fontSize: 12,
          color: 'var(--text-secondary)',
          marginTop: 16,
          marginBottom: 8,
        }}
      >
        Travel Times by Reference
      </h3>
      <table className="detail-city-table">
        <thead>
          <tr>
            <th>City</th>
            <th title="Felt time today: best of manual driving (1:1) or PT (×comfort factor)">Today</th>
            <th title="Felt time with AV: best of AV driving (×AV factor) or PT (×comfort factor)">With AV</th>
            <th title="Minutes saved: today minus with-AV (same formula used for scoring)">Saved</th>
          </tr>
        </thead>
        <tbody>
          {sortedRefs.map(([id, name]) => {
            const driveS = driveTimes[id]
            const rawPtS = ptTimes[id]
            // Apply walk deduction to PT times (must match App.jsx scoring)
            const walkDed = PT_WALK_DEDUCTION[p.pop_category] || PT_WALK_DEFAULT
            const ptS = rawPtS != null ? Math.max(0, rawPtS - walkDed) : null
            const isEnabled = enabledSet.has(id)

            // Comfort-weighted times — same formula as recomputeScores in App.jsx
            const driveMin = driveS != null ? driveS / 60 : null          // manual drive: factor 1.0
            const ptComfortMin = ptS != null ? (ptS / 60) * ptFactor : null  // PT × comfort
            const avDriveMin = driveS != null ? (driveS / 60) * avFactor : null  // AV drive × factor

            // Best option today = min(manual drive, PT×comfort)
            const todayCandidates = [driveMin, ptComfortMin].filter((v) => v != null)
            const bestToday = todayCandidates.length > 0 ? Math.min(...todayCandidates) : null

            // Best option with AV = min(AV drive, PT×comfort) — you still have PT as an option
            const avCandidates = [avDriveMin, ptComfortMin].filter((v) => v != null)
            const bestWithAV = avCandidates.length > 0 ? Math.min(...avCandidates) : null

            // Saved = today - withAV (positive = AV saves time)
            const saved = bestToday != null && bestWithAV != null ? bestToday - bestWithAV : null

            return (
              <tr key={id} className={isEnabled ? '' : 'row-disabled'}>
                <th>{typeof name === 'string' ? name : name}</th>
                <td title={driveMin != null && ptComfortMin != null
                  ? `Drive ${Math.round(driveMin)} min vs PT ${Math.round(ptComfortMin)} min (felt)`
                  : undefined}>
                  {bestToday != null ? `${Math.round(bestToday)} min` : '—'}
                </td>
                <td title={avDriveMin != null && ptComfortMin != null
                  ? `AV ${Math.round(avDriveMin)} min vs PT ${Math.round(ptComfortMin)} min (felt)`
                  : undefined}
                  style={{ color: 'var(--accent)' }}>
                  {bestWithAV != null ? `${Math.round(bestWithAV)} min` : '—'}
                </td>
                <td className={saved > 0 ? 'positive' : saved < 0 ? 'negative' : ''}>
                  {saved != null ? `${saved > 0 ? '+' : ''}${Math.round(saved)} min` : '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default function SidePanel({
  data,
  selected,
  colorBy,
  setColorBy,
  filterCity,
  setFilterCity,
  weights,
  setWeights,
  allCities,
  enabledCities,
  toggleCity,
  customLocations,
  addCustomLocation,
  removeCustomLocation,
  toggleCustomLocation,
  refMaxTimes,
  setRefMaxTime,
  modelParams,
  setModelParams,
  onClose,
  onSelectFeature,
  resetToDefaults,
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showCities, setShowCities] = useState(true)
  const [showTop, setShowTop] = useState(true)

  const totalFeatures = data?.features?.length || 0
  const excludedCount = data?.features?.filter((f) => f.properties.excluded).length || 0
  const withScore =
    data?.features?.filter((f) => f.properties.autonomy_score_rel != null && !f.properties.excluded).length || 0
  const totalCities = Object.keys(allCities).length
  const activeCities = enabledCities.length + (customLocations?.filter((l) => l.enabled).length || 0)
  const totalRefs = totalCities + (customLocations?.length || 0)

  return (
    <div className="side-panel">
      <div className="side-panel-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1>Autonomy Explorer</h1>
            <p>Swiss real estate upside from autonomous driving</p>
          </div>
          {resetToDefaults && (
            <button
              onClick={resetToDefaults}
              title="Reset all settings to defaults"
              style={{
                background: 'none', border: '1px solid var(--border)', borderRadius: 4,
                color: 'var(--text-secondary)', cursor: 'pointer', padding: '4px 8px',
                fontSize: 11, marginTop: 4, whiteSpace: 'nowrap',
              }}
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Search */}
      <div className="panel-section">
        <SearchBox data={data} onSelect={onSelectFeature} />
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
          {totalFeatures} locations | {withScore} scored{excludedCount > 0 ? ` | ${excludedCount} filtered out` : ''}
        </div>
      </div>

      {/* Reference Cities + Custom Locations */}
      <div className="panel-section">
        <div
          className="collapsible-header"
          onClick={() => setShowCities(!showCities)}
        >
          <h3 style={{ margin: 0 }}>
            Reference Locations
            <span style={{ fontWeight: 400, opacity: 0.6, marginLeft: 6 }}>
              {activeCities}/{totalRefs}
            </span>
          </h3>
          <span className={`arrow ${showCities ? 'open' : ''}`}>▶</span>
        </div>
        {showCities && (
          <>
            <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 6, marginBottom: 2, lineHeight: 1.3 }}>
              Set a max acceptable travel time per location. Places exceeding any limit are filtered out.
            </div>
            <CityCheckboxes
              allCities={allCities}
              enabledCities={enabledCities}
              toggleCity={toggleCity}
              refMaxTimes={refMaxTimes}
              setRefMaxTime={setRefMaxTime}
            />
            <CustomLocationsList
              customLocations={customLocations || []}
              toggleCustomLocation={toggleCustomLocation}
              removeCustomLocation={removeCustomLocation}
              refMaxTimes={refMaxTimes}
              setRefMaxTime={setRefMaxTime}
            />
            <MunicipalityPicker data={data} addCustomLocation={addCustomLocation} customLocations={customLocations} />
          </>
        )}
      </div>

      {/* Visualization metric selector */}
      <div className="panel-section">
        <h3>Visualization</h3>

        <div className="control-group">
          <label>Color map by</label>
          <select value={colorBy} onChange={(e) => setColorBy(e.target.value)}>
            <optgroup label="Compound">
              <option value="autonomy_score_rel">Compound (relative gain)</option>
              <option value="autonomy_score_abs">Compound (absolute gain)</option>
            </optgroup>
            <optgroup label="Pricing">
              <option value="chf_per_m2">Property Price (CHF/m²)</option>
              <option value="score_attractiveness">Inherent Attractiveness</option>
            </optgroup>
            <optgroup label="Accessibility">
              <option value="score_status_quo">Status-Quo Accessibility</option>
              <option value="score_post_av">Post-Autonomy Accessibility</option>
              <option value="score_rel_gain">Relative Gain (% improvement)</option>
              <option value="score_abs_delta">Absolute Gain (minutes saved)</option>
            </optgroup>
            <optgroup label="Raw Travel Times">
              <option value="avg_car_access">Average Car Access (min)</option>
              <option value="avg_pt_access">Average PT Access (min)</option>
              <option value="car_pt_delta_min">Car − PT Delta (min)</option>
              <option value="car_pt_delta_pct">Car − PT Delta (%)</option>
            </optgroup>
          </select>
        </div>

        {METRICS[colorBy] && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
            {METRICS[colorBy].desc}
          </div>
        )}
      </div>

      {/* Scoring Weights + Model Parameters */}
      <div className="panel-section">
        <div
          className="collapsible-header"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          <h3 style={{ margin: 0 }}>Scoring Weights</h3>
          <span className={`arrow ${showAdvanced ? 'open' : ''}`}>▶</span>
        </div>

        {showAdvanced && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 10, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 10 }}>
              What matters more to you?
            </div>

            <div className="control-group">
              <label>Commute improvement vs. bargain hunting</label>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-secondary)', marginBottom: 2 }}>
                <span>Shorter commute</span>
                <span>Cheap hidden gems</span>
              </div>
              <div className="slider-row">
                <input
                  type="range"
                  min="0"
                  max="100"
                  value={Math.round(weights.inherent_attractiveness * 100)}
                  onChange={(e) => {
                    const attract = parseInt(e.target.value) / 100
                    setWeights({ accessibility_gain: 1 - attract, inherent_attractiveness: attract })
                  }}
                />
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.3 }}>
                <strong style={{ color: 'var(--text-primary)' }}>Left:</strong> Favor places where AV makes the biggest commute improvement vs. today.{' '}
                <strong style={{ color: 'var(--text-primary)' }}>Right:</strong> Favor places that are cheap compared to others with a similar commute — bargains the market hasn't priced in yet.
              </div>
            </div>

            <div style={{ fontSize: 10, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 4, marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 12 }}>
              Your travel preferences
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.4 }}>
              How do you personally experience different ways of travelling?
            </div>

            <div className="control-group">
              <label style={{ lineHeight: 1.3 }}>Public transport vs. driving yourself</label>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6, lineHeight: 1.3 }}>
                I'd rather sit on a train for 60 min than drive myself for...
              </div>
              <div className="slider-row">
                <input
                  type="range"
                  min="18"
                  max="60"
                  value={Math.round(60 * modelParams.ptFactor)}
                  onChange={(e) =>
                    setModelParams((p) => ({ ...p, ptFactor: parseInt(e.target.value) / 60 }))
                  }
                />
                <span className="slider-value" style={{ minWidth: 44 }}>
                  {Math.round(60 * modelParams.ptFactor)} min
                </span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.3 }}>
                {Math.round(60 * modelParams.ptFactor) < 40
                  ? 'You strongly prefer PT — you can read, nap, or work on the train. Driving feels like wasted time.'
                  : Math.round(60 * modelParams.ptFactor) <= 50
                  ? 'You somewhat prefer PT — the train is more relaxed, but driving has its perks.'
                  : Math.round(60 * modelParams.ptFactor) < 58
                  ? 'Roughly equal — PT and driving feel about the same to you.'
                  : 'You prefer driving — you value the flexibility and door-to-door convenience.'}
              </div>
            </div>

            <div className="control-group">
              <label style={{ lineHeight: 1.3 }}>Autonomous vehicle vs. driving yourself</label>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6, lineHeight: 1.3 }}>
                I'd rather ride in a self-driving car for 60 min than drive myself for...
              </div>
              <div className="slider-row">
                <input
                  type="range"
                  min="18"
                  max="60"
                  value={Math.round(60 * modelParams.avFactor)}
                  onChange={(e) =>
                    setModelParams((p) => ({ ...p, avFactor: parseInt(e.target.value) / 60 }))
                  }
                />
                <span className="slider-value" style={{ minWidth: 44 }}>
                  {Math.round(60 * modelParams.avFactor)} min
                </span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.3 }}>
                {Math.round(60 * modelParams.avFactor) < 35
                  ? 'AVs are a game-changer for you — basically a mobile office or living room. Long commutes become productive time.'
                  : Math.round(60 * modelParams.avFactor) <= 45
                  ? 'AVs are a big improvement — you can work or relax, making longer commutes much more acceptable.'
                  : Math.round(60 * modelParams.avFactor) < 55
                  ? 'AVs help somewhat — not having to focus on driving is nice, but it still feels like commuting.'
                  : 'AVs don\'t change much for you — sitting in a car is sitting in a car, whether you drive or not.'}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Selected municipality detail */}
      {selected && (
        <MunicipalityDetail
          feature={selected}
          onClose={onClose}
          allCities={allCities}
          enabledCities={enabledCities}
          customLocations={customLocations}
          modelParams={modelParams}
          colorBy={colorBy}
        />
      )}

      {/* Top 10 & Bottom 10 ranking */}
      {!selected && (
        <div className="panel-section" style={{ flex: 1 }}>
          <div
            className="collapsible-header"
            onClick={() => setShowTop(!showTop)}
          >
            <h3 style={{ margin: 0 }}>
              Top & Bottom 10
              <span style={{ fontWeight: 400, opacity: 0.6, marginLeft: 6 }}>
                by {METRICS[colorBy]?.label || colorBy}
              </span>
            </h3>
            <span className={`arrow ${showTop ? 'open' : ''}`}>▶</span>
          </div>
          {showTop && (
            <>
              <div style={{ fontSize: 11, color: 'var(--accent)', fontWeight: 600, marginTop: 8, marginBottom: 4 }}>
                ▲ Top 10
              </div>
              <TopList data={data} onSelect={onSelectFeature} colorBy={colorBy} />
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600, marginTop: 16, marginBottom: 4, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                ▼ Bottom 10
              </div>
              <BottomList data={data} onSelect={onSelectFeature} colorBy={colorBy} />
            </>
          )}
        </div>
      )}
    </div>
  )
}
