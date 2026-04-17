import React, { useState, useMemo, useCallback } from 'react'

function formatTime(seconds) {
  if (seconds == null) return '\u2014'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m} min`
  return `${m} min`
}

function formatMinutes(val) {
  if (val == null) return '\u2014'
  return `${val.toFixed(1)} min`
}

// Color-by metric definitions — ordered from simple to complex
const METRICS = {
  avg_car_access: {
    label: 'Average Car Access',
    desc: 'Mean driving time to selected reference locations (raw minutes)',
    unit: 'min',
    sortAscending: true,
  },
  avg_pt_access: {
    label: 'Average PT Access',
    desc: 'Mean public transport time to selected reference locations (raw minutes, no comfort weighting)',
    unit: 'min',
    sortAscending: true,
  },
  optimum_access: {
    label: 'Optimum Accessibility',
    desc: 'Best of car or PT per reference location, then averaged. The fastest way to get there today, regardless of mode',
    unit: 'min',
    sortAscending: true,
  },
  car_pt_delta_min: {
    label: 'Car − PT Delta (min)',
    desc: 'Positive = car is slower (PT advantage). Negative = PT is slower (car advantage)',
    unit: 'min',
  },
  av_upside: {
    label: 'AV Upside',
    desc: 'Minutes saved by AV vs the current best option (PT comfort or manual drive). Measures how much faster AV makes the commute compared to today\'s best available mode.',
    unit: 'min',
  },
  av_value_unlock: {
    label: 'AV Value Unlock',
    desc: 'Latent property value unlocked by autonomous vehicles. Converts AV time savings into CHF via value-of-time and capitalization rate: annual minutes saved × VTT, discounted as a perpetuity.',
    unit: 'CHF',
  },
  av_deal_score: {
    label: 'AV Deal Score (m\u00b2)',
    desc: 'Compound "bang for buck" metric: AV Value Unlock divided by price per m\u00b2. Expresses the AV upside as equivalent square meters of property it pays for. High = low price + high AV potential (best deal). Low = high price + low AV potential (worst deal).',
    unit: 'm\u00b2',
  },
  chf_per_m2: {
    label: 'Property Price (CHF/m\u00b2)',
    desc: 'Estimated property price per square meter',
    unit: 'CHF/m\u00b2',
  },
}

// Normalize accented characters so "zurich" matches "Z\u00fcrich"
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

    // Search by name or settlement name
    const matches = data.features.filter((f) => {
      const nameMatch = normalize(f.properties.name).includes(q)
      const settlementMatch = normalize(f.properties.settlement_name || '').includes(q)
      return nameMatch || settlementMatch
    })

    // Deduplicate: for same municipality, pick best (lowest) avg_car_access
    const byMuni = {}
    for (const f of matches) {
      const key = f.properties.municipality_id || f.properties.id
      if (!byMuni[key] || (f.properties.avg_car_access || Infinity) < (byMuni[key].properties.avg_car_access || Infinity)) {
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
        return (a.properties.avg_car_access || Infinity) - (b.properties.avg_car_access || Infinity)
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
              <span className="search-result-name">{f.properties.settlement_name || f.properties.name}</span>
              <span className="search-result-meta">
                {f.properties.settlement_name && f.properties.settlement_name !== f.properties.name && <>{f.properties.name} ·</>}
                {f.properties.canton_code}
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
        const val = f.properties[colorBy || 'avg_car_access']
        return (
          <div
            key={f.properties.id}
            className="top-list-item"
            onClick={() => onSelect(f)}
          >
            <span className="top-list-rank">{startRank + i}</span>
            <span className="top-list-name">{f.properties.settlement_name || f.properties.name}</span>
            <span className="top-list-canton">{f.properties.canton_code}</span>
            <span className="top-list-score">
              {colorBy === 'chf_per_m2'
                ? val?.toLocaleString()
                : colorBy === 'av_value_unlock'
                ? val != null ? `${Math.round(val / 1000)}k` : '\u2014'
                : colorBy === 'car_pt_delta_min' || colorBy === 'av_upside'
                ? val != null ? `${val > 0 ? '+' : ''}${val.toFixed(1)}` : '\u2014'
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
    const prop = colorBy || 'avg_car_access'
    const metric = METRICS[prop] || METRICS.avg_car_access
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
      if (!byMuni[key] || (f.properties.avg_car_access || Infinity) < (byMuni[key].properties.avg_car_access || Infinity)) {
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
        return (a.properties.avg_car_access || Infinity) - (b.properties.avg_car_access || Infinity)
      })
      .slice(0, 8)
  }, [data, debouncedQuery, existingIds])

  const handleSelect = useCallback((f) => {
    const key = f.properties.municipality_id || f.properties.id
    addCustomLocation({
      id: `custom_muni_${key}`,
      name: f.properties.settlement_name || f.properties.name,
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
              <span className="search-result-name">{f.properties.settlement_name || f.properties.name}</span>
              <span className="search-result-meta">
                {f.properties.settlement_name && f.properties.settlement_name !== f.properties.name && <>{f.properties.name} ·</>}
                {f.properties.canton_code}
              </span>
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

  const { avFactor = 0.7, ptFactor = 1.0 } = modelParams || {}

  // Active metric display
  const activeMetric = METRICS[colorBy]
  const activeVal = p[colorBy]
  let activeDisplay = '\u2014'
  if (activeVal != null) {
    if (colorBy === 'chf_per_m2') activeDisplay = `${activeVal.toLocaleString()} CHF/m\u00b2`
    else if (colorBy === 'car_pt_delta_min') activeDisplay = `${activeVal > 0 ? '+' : ''}${activeVal.toFixed(1)} min`
    else if (colorBy === 'av_upside') activeDisplay = `${activeVal.toFixed(1)} min saved`
    else if (colorBy === 'av_value_unlock') activeDisplay = `CHF ${Math.round(activeVal).toLocaleString()}`
    else if (colorBy === 'av_deal_score') activeDisplay = `${activeVal.toFixed(1)} m\u00b2 equivalent`
    else if (activeMetric?.unit === 'min') activeDisplay = `${activeVal.toFixed(1)} min`
    else activeDisplay = activeVal.toFixed(1)
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
          <div className="detail-name">{p.settlement_name || p.name}</div>
          <div className="detail-canton">
            {p.settlement_name && p.settlement_name !== p.name && <>{p.name} ·</>}{p.canton} ({p.canton_code})
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

      <div className="detail-score" style={{ color: 'var(--accent-blue)' }}>
        {activeDisplay}
      </div>
      <div className="detail-score-label">{activeMetric?.label || colorBy}</div>

      <div className="detail-grid">
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.chf_per_m2 != null ? `${p.chf_per_m2.toLocaleString()} CHF/m\u00b2` : '\u2014'}
          </div>
          <div className="detail-stat-label">Price / m²</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.tax_multiplier != null ? `${p.tax_multiplier}%` : '\u2014'}
          </div>
          <div className="detail-stat-label">Tax Multiplier</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.avg_car_access)}</div>
          <div className="detail-stat-label">Avg Car</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.avg_pt_access)}</div>
          <div className="detail-stat-label">Avg PT</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatMinutes(p.optimum_access)}</div>
          <div className="detail-stat-label">Optimum</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.av_upside != null ? `${p.av_upside.toFixed(1)} min` : '\u2014'}
          </div>
          <div className="detail-stat-label">AV Upside</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.av_value_unlock != null ? `CHF ${p.av_value_unlock.toLocaleString()}` : '\u2014'}
          </div>
          <div className="detail-stat-label">AV Value</div>
        </div>
      </div>

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
      <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 6 }}>
        Each cell: actual / <span style={{ color: 'var(--accent)' }}>comfort-adjusted</span>.
        AV comfort ×{avFactor.toFixed(2)}, PT comfort ×{ptFactor.toFixed(2)}.
      </div>
      <table className="detail-city-table">
        <thead>
          <tr>
            <th>City</th>
            <th title="Car: actual drive time / AV comfort-equivalent (drive \u00d7 avFactor)">Car</th>
            <th title="PT: actual time / comfort-weighted (PT \u00d7 ptFactor)">PT</th>
            <th title="Delta: (Car − PT) actual / (Car AV equiv − PT comfort-weighted)">Delta</th>
          </tr>
        </thead>
        <tbody>
          {sortedRefs.map(([id, name]) => {
            const driveS = driveTimes[id]
            const ptS = ptTimes[id]
            const isEnabled = enabledSet.has(id)

            // Raw times in minutes
            const carActual = driveS != null ? driveS / 60 : null
            const ptActual = ptS != null ? ptS / 60 : null

            // Comfort-weighted equivalents
            const carAV = driveS != null ? (driveS / 60) * avFactor : null
            const ptComfort = ptS != null ? (ptS / 60) * ptFactor : null

            // Delta: car - PT (negative = car faster)
            const deltaActual = carActual != null && ptActual != null ? carActual - ptActual : null
            const deltaEquiv = carAV != null && ptComfort != null ? carAV - ptComfort : null

            const fmtDelta = (v) => v != null ? `${v > 0 ? '+' : ''}${Math.round(v)}` : '\u2014'
            const deltaClass = (v) => v != null ? (v < 0 ? 'positive' : v > 0 ? 'negative' : '') : ''

            return (
              <tr key={id} className={isEnabled ? '' : 'row-disabled'}>
                <th>{name}</th>
                <td>
                  {carActual != null
                    ? <>{Math.round(carActual)} / <span style={{ color: 'var(--accent)' }}>{Math.round(carAV)}</span></>
                    : '\u2014'}
                </td>
                <td>
                  {ptActual != null
                    ? <>{Math.round(ptActual)} / <span style={{ color: 'var(--text-secondary)' }}>{Math.round(ptComfort)}</span></>
                    : '\u2014'}
                </td>
                <td className={deltaClass(deltaEquiv)}>
                  {deltaActual != null
                    ? <>{fmtDelta(deltaActual)} / <span style={{ fontWeight: 600 }}>{fmtDelta(deltaEquiv)}</span></>
                    : '\u2014'}
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
  const withData = data?.features?.filter((f) => f.properties.avg_car_access != null && !f.properties.excluded).length || 0
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
          {totalFeatures} locations{withData < totalFeatures ? ` | ${withData} with data` : ''}{excludedCount > 0 ? ` | ${excludedCount} filtered out` : ''}
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
            <optgroup label="Travel Times">
              <option value="avg_car_access">Average Car Access (min)</option>
              <option value="avg_pt_access">Average PT Access (min)</option>
              <option value="optimum_access">Optimum Accessibility (best mode)</option>
            </optgroup>
            <optgroup label="Mode Comparison">
              <option value="car_pt_delta_min">Car − PT Delta (min)</option>
              <option value="av_upside">AV Upside Potential</option>
              <option value="av_value_unlock">AV Value Unlock (CHF)</option>
            </optgroup>
            <optgroup label="Pricing">
              <option value="chf_per_m2">Property Price (CHF/m²)</option>
              <option value="av_deal_score">AV Deal Score (m² bang-for-buck)</option>
            </optgroup>
          </select>
        </div>

        {METRICS[colorBy] && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
            {METRICS[colorBy].desc}
          </div>
        )}
      </div>

      {/* Travel Preferences (comfort factors) */}
      <div className="panel-section">
        <div
          className="collapsible-header"
          onClick={() => setShowAdvanced(!showAdvanced)}
        >
          <h3 style={{ margin: 0 }}>Travel Preferences</h3>
          <span className={`arrow ${showAdvanced ? 'open' : ''}`}>▶</span>
        </div>

        {showAdvanced && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 10, lineHeight: 1.4 }}>
              How do you personally experience different ways of travelling?
              These affect comfort-adjusted comparisons and AV upside calculations.
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
                {Math.round(60 * modelParams.ptFactor) < 35
                  ? 'You strongly prefer PT \u2014 reading, napping, or working on the train makes commuting productive.'
                  : Math.round(60 * modelParams.ptFactor) < 48
                  ? 'You moderately prefer PT \u2014 the train is more relaxed than driving.'
                  : Math.round(60 * modelParams.ptFactor) < 55
                  ? 'You slightly prefer PT \u2014 a small edge to public transport.'
                  : 'Roughly equal \u2014 time on PT feels about the same as time driving.'}
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
                  ? 'AVs are a game-changer for you \u2014 basically a mobile office or living room. Long commutes become productive time.'
                  : Math.round(60 * modelParams.avFactor) <= 45
                  ? 'AVs are a big improvement \u2014 you can work or relax, making longer commutes much more acceptable.'
                  : Math.round(60 * modelParams.avFactor) < 55
                  ? 'AVs help somewhat \u2014 not having to focus on driving is nice, but it still feels like commuting.'
                  : 'AVs don\'t change much for you \u2014 sitting in a car is sitting in a car, whether you drive or not.'}
              </div>
            </div>

            <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                Economic Assumptions (AV Value Unlock)
              </div>

              <div className="control-group">
                <label style={{ lineHeight: 1.3 }}>Value of travel time</label>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 4, lineHeight: 1.3 }}>
                  What is one hour of commute time worth to you? Swiss literature: CHF 23{'\u2013'}37/h.
                </div>
                <div className="slider-row">
                  <input
                    type="range"
                    min="10"
                    max="60"
                    step="5"
                    value={modelParams.vtt}
                    onChange={(e) =>
                      setModelParams((p) => ({ ...p, vtt: parseInt(e.target.value) }))
                    }
                  />
                  <span className="slider-value" style={{ minWidth: 54 }}>
                    CHF {modelParams.vtt}/h
                  </span>
                </div>
              </div>

              <div className="control-group">
                <label style={{ lineHeight: 1.3 }}>Capitalization rate</label>
                <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 4, lineHeight: 1.3 }}>
                  Discount rate for perpetuity. Lower = higher capitalized value. Swiss RE: 3{'\u2013'}5%.
                </div>
                <div className="slider-row">
                  <input
                    type="range"
                    min="2"
                    max="8"
                    step="0.5"
                    value={modelParams.capRate * 100}
                    onChange={(e) =>
                      setModelParams((p) => ({ ...p, capRate: parseFloat(e.target.value) / 100 }))
                    }
                  />
                  <span className="slider-value" style={{ minWidth: 44 }}>
                    {(modelParams.capRate * 100).toFixed(1)}%
                  </span>
                </div>
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
