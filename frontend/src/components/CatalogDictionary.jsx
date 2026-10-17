import React from 'react';
import { 
  Database, 
  Layers, 
  Sparkles, 
  Sliders, 
  CheckCircle,
  Clock,
  Compass,
  GitBranch,
  ArrowRight,
  Plus,
  Table
} from 'lucide-react';

export default function CatalogDictionary({ 
  catalog, 
  selectedSource, 
  onQuickQuery,
  onOpenUpload
}) {
  if (!catalog) {
    return (
      <div className="catalog-loading">
        <Clock size={24} className="spin-icon" color="var(--accent-blue)" />
        <p>Loading semantic catalog definitions for {selectedSource}...</p>
      </div>
    );
  }

  const measures = catalog.measures || {};
  const dimensions = catalog.dimensions || {};
  const tables = catalog.tables || [];
  const joins = catalog.joins || [];
  const scenarioModels = catalog.scenario_models || {};
  const policies = catalog.time_series_policies || {};

  return (
    <div className="catalog-dictionary-container">
      {/* Header Banner */}
      <div className="dictionary-hero">
        <div className="hero-left">
          <div className="hero-icon-wrap">
            <Database size={28} color="var(--accent-blue)" />
          </div>
          <div>
            <h2>Approved Semantic Catalog: {catalog.source_id}</h2>
            <p>
              Cryptographically pinned release: <code>{catalog.release_id}</code> (Epoch #{catalog.epoch}).
              All queries are validated through this approved ontology.
            </p>
          </div>
        </div>
        {onOpenUpload && (
          <button className="btn btn-primary" onClick={onOpenUpload}>
            <Plus size={16} />
            <span>Connect / Upload Database</span>
          </button>
        )}
      </div>

      <div className="dictionary-grid">
        {/* Approved Join Relationships (Entity Graph) */}
        {joins.length > 0 && (
          <div className="dict-section full-width">
            <div className="dict-section-header">
              <GitBranch size={18} color="var(--accent-blue)" />
              <h3>Approved Join Relationships & Entity Graph ({joins.length})</h3>
            </div>
            <div className="joins-grid">
              {joins.map((j, idx) => (
                <div key={idx} className="join-edge-card">
                  <div className="join-edge-route">
                    <span className="join-node"><strong>{j.from_table}</strong>.{j.from_column}</span>
                    <ArrowRight size={14} color="var(--accent-blue)" />
                    <span className="join-node"><strong>{j.to_table}</strong>.{j.to_column}</span>
                  </div>
                  <div className="join-edge-badges">
                    <span className="badge-cardinality">{j.cardinality}</span>
                    <span className="badge-jointype">{j.join_type}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Physical Tables */}
        {tables.length > 0 && (
          <div className="dict-section full-width">
            <div className="dict-section-header">
              <Table size={18} color="var(--accent-purple)" />
              <h3>Physical Database Tables ({tables.length})</h3>
            </div>
            <div className="tables-pill-list">
              {tables.map((t, idx) => (
                <div key={idx} className="table-summary-pill">
                  <span className="tbl-name">{t.name}</span>
                  <span className="tbl-rows">Grain: {t.grain}</span>
                  <span className="tbl-cols">Tenant col: <code>{t.tenant_column}</code></span>
                  <span className="tbl-pk">PK: ({t.primary_key.join(', ')})</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Approved Measures */}
        <div className="dict-section">
          <div className="dict-section-header">
            <Sparkles size={18} color="var(--accent-blue)" />
            <h3>Authorized Measures ({Object.keys(measures).length})</h3>
          </div>
          <div className="dict-cards-list">
            {Object.values(measures).map((m) => (
              <div key={m.id} className="measure-card">
                <div className="measure-card-top">
                  <span className="measure-name">{m.name}</span>
                  <span className="measure-unit-badge">{m.unit || 'Numeric'}</span>
                </div>
                <p className="measure-desc">{m.description || `Column ${m.column} on table ${m.table}`}</p>
                <div className="measure-specs">
                  <span className="spec-tag">Agg: {m.aggregation}</span>
                  {m.base_grain && <span className="spec-tag">Grain: {m.base_grain}</span>}
                  {m.min_entities && (
                    <span className="spec-tag suppression">Min {m.min_entities} entities</span>
                  )}
                </div>
                {m.allowed_families && (
                  <div className="allowed-families-row">
                    <span className="families-label">Eligible Families:</span>
                    {m.allowed_families.map((f) => (
                      <span key={f} className="family-micro-badge">{f}</span>
                    ))}
                  </div>
                )}
                <button 
                  onClick={() => onQuickQuery(`What was total ${m.name.toLowerCase()} in 2024?`)}
                  className="btn-quick-analyze"
                >
                  <Compass size={13} />
                  <span>Analyze {m.name}</span>
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Permitted Dimensions */}
        <div className="dict-section">
          <div className="dict-section-header">
            <Layers size={18} color="var(--accent-emerald)" />
            <h3>Permitted Dimensions ({Object.keys(dimensions).length})</h3>
          </div>
          <div className="dict-cards-list">
            {Object.values(dimensions).map((d) => (
              <div key={d.id} className="dimension-card">
                <div className="dim-card-top">
                  <span className="dim-name">{d.name}</span>
                  <span className="dim-type-badge">{d.data_type || 'STRING'}</span>
                </div>
                <div className="dim-lineage">
                  Table: <code>{d.table}</code> • Column: <code>{d.column}</code>
                </div>
                <button 
                  onClick={() => onQuickQuery(`Show revenue grouped by ${d.name.toLowerCase()}.`)}
                  className="btn-quick-analyze"
                >
                  <span>Group by {d.name}</span>
                </button>
              </div>
            ))}

            {/* Time Series Policies */}
            {Object.keys(policies).length > 0 && (
              <div className="policy-box-container">
                <h4 className="policy-box-title">Time-Series Frequency Policies</h4>
                {Object.values(policies).map((p, idx) => (
                  <div key={idx} className="ts-policy-item">
                    <span>Metric: <strong>{p.metric}</strong></span>
                    <span>Frequency: <strong>{p.frequency}</strong></span>
                    <span>Min History: {p.min_history_periods}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Scenario Models */}
        {Object.keys(scenarioModels).length > 0 && (
          <div className="dict-section full-width">
            <div className="dict-section-header">
              <Sliders size={18} color="var(--accent-purple)" />
              <h3>Approved Scenario & Simulation Models ({Object.keys(scenarioModels).length})</h3>
            </div>
            <div className="scenario-models-grid">
              {Object.values(scenarioModels).map((sm) => (
                <div key={sm.model_id} className="scenario-model-card">
                  <div className="sm-top">
                    <span className="sm-name">{sm.name}</span>
                    <span className="sm-formula">Formula: <code>{sm.formula_expression}</code></span>
                  </div>
                  <p className="sm-desc">{sm.description}</p>
                  <div className="sm-drivers-list">
                    <strong>Adjustable Drivers:</strong>
                    {sm.drivers && Object.entries(sm.drivers).map(([dKey, dObj]) => (
                      <span key={dKey} className="driver-pill">
                        {dKey} ({dObj.unit || 'val'}) [Range: {dObj.min_value || 0} – {dObj.max_value || 100}]
                      </span>
                    ))}
                  </div>
                  <button 
                    onClick={() => onQuickQuery(`Run scenario for ${sm.name.toLowerCase()} with price 30.`)}
                    className="btn-quick-analyze purple"
                  >
                    <span>Simulate {sm.name}</span>
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
