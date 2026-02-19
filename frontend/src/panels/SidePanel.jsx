import React, { useState, useMemo, useCallback } from 'react'

function formatTime(seconds) {
  if (seconds == null) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function formatScore(val) {
  if (val == null) return '—'
  return val.toFixed(1)
}

function formatMinutes(val) {
  if (val == null) return '—'
  return `${val.toFixed(0)}m`
}

// Color-by metric definitions
const METRICS = {
  autonomy_score: {
    label: 'Compound Score',
    desc: 'Weighted combo: inherent attractiveness + accessibility gain',
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
    desc: 'How cheap is this place compared to others with a similar commute?',
    unit: '',
    isScore: true,
  },
  score_post_av: {
    label: 'Post-Autonomy Accessibility',
    desc: 'AV-era accessibility (drive time × 0.7 comfort factor)',
    unit: '',
    isScore: true,
  },
  score_delta: {
    label: 'Accessibility Delta',
    desc: 'Improvement from AV: status-quo minus post-AV accessibility',
    unit: '',
    isScore: true,
  },
  score_accessibility: {
    label: 'Accessibility Gain (normalized)',
    desc: 'Normalized version of delta — same as above, 0-100 scale',
    unit: '',
    isScore: true,
    hidden: true, // same as score_delta, hide to avoid confusion
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

function SearchBox({ data, onSelect }) {
  const [query, setQuery] = useState('')
  const [focused, setFocused] = useState(false)

  const results = useMemo(() => {
    if (!data || query.length < 2) return []
    const q = normalize(query)
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
      if (!byMuni[key] || (f.properties.autonomy_score || 0) > (byMuni[key].properties.autonomy_score || 0)) {
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
        return (b.properties.autonomy_score || 0) - (a.properties.autonomy_score || 0)
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
                {f.properties.autonomy_score != null && (
                  <> · {f.properties.autonomy_score.toFixed(1)}</>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function TopList({ data, onSelect, colorBy }) {
  const top = useMemo(() => {
    if (!data) return []
    const prop = colorBy || 'autonomy_score'

    // Deduplicate by municipality: pick the best PLZ per municipality
    const byMuni = {}
    for (const f of data.features) {
      if (f.properties[prop] == null || f.properties.excluded) continue
      const key = f.properties.municipality_id || f.properties.id
      if (!byMuni[key] || f.properties[prop] > byMuni[key].properties[prop]) {
        byMuni[key] = f
      }
    }

    return Object.values(byMuni)
      .sort((a, b) => b.properties[prop] - a.properties[prop])
      .slice(0, 10)
  }, [data, colorBy])

  if (top.length === 0) return null

  const metric = METRICS[colorBy] || METRICS.autonomy_score

  return (
    <div className="top-list">
      {top.map((f, i) => {
        const val = f.properties[colorBy || 'autonomy_score']
        return (
          <div
            key={f.properties.id}
            className="top-list-item"
            onClick={() => onSelect(f)}
          >
            <span className="top-list-rank">{i + 1}</span>
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

const MAX_TIME_OPTIONS = [
  { value: null, label: 'Any' },
  { value: 30, label: '30 min' },
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

function CustomLocationInput({ addCustomLocation }) {
  const [input, setInput] = useState('')
  const [searching, setSearching] = useState(false)

  const handleAdd = useCallback(async () => {
    if (!input.trim()) return
    setSearching(true)

    try {
      // Use Nominatim (OpenStreetMap) for geocoding
      const query = `${input.trim()}, Switzerland`
      const resp = await fetch(
        `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1&countrycodes=ch`,
        {
          headers: { 'User-Agent': 'AutonomyExplorer/1.0' },
        }
      )
      const results = await resp.json()

      if (results.length > 0) {
        const r = results[0]
        const loc = {
          id: `custom_${Date.now()}`,
          name: r.display_name.split(',')[0],
          lat: parseFloat(r.lat),
          lon: parseFloat(r.lon),
          enabled: true,
        }
        addCustomLocation(loc)
        setInput('')
      } else {
        alert('Location not found. Try a more specific address in Switzerland.')
      }
    } catch (err) {
      console.error('Geocoding error:', err)
      alert('Failed to find location. Please try again.')
    } finally {
      setSearching(false)
    }
  }, [input, addCustomLocation])

  return (
    <div className="custom-location-input">
      <input
        type="text"
        placeholder="Add address (e.g. Bahnhofstrasse 1, Zürich)"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
        className="search-input"
        style={{ fontSize: 11 }}
      />
      <button
        onClick={handleAdd}
        disabled={searching || !input.trim()}
        className="add-location-btn"
      >
        {searching ? '...' : '+'}
      </button>
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
              <span className={loc.enabled ? '' : 'disabled'}>{loc.name}</span>
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

function MunicipalityDetail({ feature, onClose, allCities, enabledCities, customLocations }) {
  const p = feature.properties
  const driveTimesRaw = p.drive_times
  const ptTimesRaw = p.pt_times
  const gainRaw = p.gain_per_city

  const driveTimes =
    typeof driveTimesRaw === 'string' ? JSON.parse(driveTimesRaw) : driveTimesRaw || {}
  const ptTimes =
    typeof ptTimesRaw === 'string' ? JSON.parse(ptTimesRaw) : ptTimesRaw || {}
  const gains =
    typeof gainRaw === 'string' ? JSON.parse(gainRaw) : gainRaw || {}

  const scoreColor =
    p.autonomy_score != null
      ? p.autonomy_score > 70
        ? 'var(--accent)'
        : p.autonomy_score > 40
        ? '#f57f17'
        : 'var(--accent-blue)'
      : 'var(--text-secondary)'

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

      <div className="detail-score" style={{ color: scoreColor }}>
        {formatScore(p.autonomy_score)}
      </div>
      <div className="detail-score-label">Compound Score</div>

      <div className="detail-grid">
        <div className="detail-stat">
          <div className="detail-stat-value">
            {p.chf_per_m2 != null ? `${p.chf_per_m2.toLocaleString()} CHF` : '—'}
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
          <div className="detail-stat-label">Delta</div>
        </div>
        <div className="detail-stat">
          <div className="detail-stat-value">{formatTime(p.min_drive_s)}</div>
          <div className="detail-stat-label">Best Drive</div>
        </div>
      </div>

      <ScoreBar value={p.score_accessibility} label="Accessibility Gain" color="var(--accent-blue)" />
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
            <th>Drive</th>
            <th>PT</th>
            <th>Gain</th>
          </tr>
        </thead>
        <tbody>
          {sortedRefs.map(([id, name]) => {
            const gain = gains[id]
            const isEnabled = enabledSet.has(id)
            return (
              <tr key={id} className={isEnabled ? '' : 'row-disabled'}>
                <th>{typeof name === 'string' ? name : name}</th>
                <td>{formatTime(driveTimes[id])}</td>
                <td>{formatTime(ptTimes[id])}</td>
                <td className={gain > 0 ? 'positive' : gain < 0 ? 'negative' : ''}>
                  {gain != null ? `${gain > 0 ? '+' : ''}${gain.toFixed(0)}m` : '—'}
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
}) {
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showCities, setShowCities] = useState(true)
  const [showTop, setShowTop] = useState(true)

  const totalFeatures = data?.features?.length || 0
  const excludedCount = data?.features?.filter((f) => f.properties.excluded).length || 0
  const withScore =
    data?.features?.filter((f) => f.properties.autonomy_score != null && !f.properties.excluded).length || 0
  const totalCities = Object.keys(allCities).length
  const activeCities = enabledCities.length + (customLocations?.filter((l) => l.enabled).length || 0)
  const totalRefs = totalCities + (customLocations?.length || 0)

  return (
    <div className="side-panel">
      <div className="side-panel-header">
        <h1>Autonomy Explorer</h1>
        <p>Swiss real estate upside from autonomous driving</p>
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
            <CustomLocationInput addCustomLocation={addCustomLocation} />
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
              <option value="autonomy_score">Compound Score (combined)</option>
            </optgroup>
            <optgroup label="Pricing">
              <option value="chf_per_m2">Property Price (CHF/m²)</option>
              <option value="score_attractiveness">Inherent Attractiveness</option>
            </optgroup>
            <optgroup label="Accessibility">
              <option value="score_status_quo">Status-Quo Accessibility</option>
              <option value="score_post_av">Post-Autonomy Accessibility</option>
              <option value="score_delta">Accessibility Delta (improvement)</option>
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
        />
      )}

      {/* Top 10 ranking */}
      {!selected && (
        <div className="panel-section" style={{ flex: 1 }}>
          <div
            className="collapsible-header"
            onClick={() => setShowTop(!showTop)}
          >
            <h3 style={{ margin: 0 }}>
              Top 10
              <span style={{ fontWeight: 400, opacity: 0.6, marginLeft: 6 }}>
                by {METRICS[colorBy]?.label || colorBy}
              </span>
            </h3>
            <span className={`arrow ${showTop ? 'open' : ''}`}>▶</span>
          </div>
          {showTop && <TopList data={data} onSelect={onSelectFeature} colorBy={colorBy} />}
        </div>
      )}
    </div>
  )
}
