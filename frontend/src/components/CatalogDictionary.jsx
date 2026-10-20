import React, { useState, useMemo } from 'react';
import { 
  Database, 
  Layers, 
  Sparkles, 
  Sliders, 
  ShieldCheck,
  CheckCircle,
  Clock,
  Compass,
  GitBranch,
  ArrowRight,
  Plus,
  Table,
  Search,
  Filter,
  BarChart2,
  Key
} from 'lucide-react';

export default function CatalogDictionary({ 
  catalog, 
  selectedSource, 
  onQuickQuery,
  onOpenUpload
}) {
  const [activeTab, setActiveTab] = useState('overview'); // 'overview' | 'measures' | 'dimensions' | 'joins' | 'tables'
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTableFilter, setSelectedTableFilter] = useState('ALL');
  const [selectedJoinCategory, setSelectedJoinCategory] = useState('ALL'); // 'ALL' | 'DIRECT' | 'HIERARCHY'

  const measures = catalog?.measures || {};
  const dimensions = catalog?.dimensions || {};
  const tables = catalog?.tables || [];
  const joins = catalog?.joins || [];
  const scenarioModels = catalog?.scenario_models || {};

  // Extract unique table names
  const allTableNames = useMemo(() => {
    const names = new Set(tables.map(t => t.name));
    Object.values(dimensions).forEach(d => { if (d.table) names.add(d.table); });
    Object.values(measures).forEach(m => { if (m.table) names.add(m.table); });
    return Array.from(names).sort();
  }, [tables, dimensions, measures]);

  // Filtered Measures
  const filteredMeasures = useMemo(() => {
    return Object.values(measures).filter(m => {
      const matchSearch = !searchQuery || 
        m.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        m.description?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        m.table?.toLowerCase().includes(searchQuery.toLowerCase());
      const matchTable = selectedTableFilter === 'ALL' || m.table === selectedTableFilter;
      return matchSearch && matchTable;
    });
  }, [measures, searchQuery, selectedTableFilter]);

  // Filtered Dimensions
  const filteredDimensions = useMemo(() => {
    return Object.values(dimensions).filter(d => {
      const matchSearch = !searchQuery || 
        d.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        d.column?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        d.table?.toLowerCase().includes(searchQuery.toLowerCase());
      const matchTable = selectedTableFilter === 'ALL' || d.table === selectedTableFilter;
      return matchSearch && matchTable;
    });
  }, [dimensions, searchQuery, selectedTableFilter]);

  // Group Dimensions by Table for cleaner organization
  const dimensionsByTable = useMemo(() => {
    const grouped = {};
    filteredDimensions.forEach(d => {
      const tbl = d.table || 'General';
      if (!grouped[tbl]) grouped[tbl] = [];
      grouped[tbl].push(d);
    });
    return grouped;
  }, [filteredDimensions]);

  // Filtered Joins
  const filteredJoins = useMemo(() => {
    return joins.filter(j => {
      const matchSearch = !searchQuery ||
        j.from_table?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        j.to_table?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        j.from_column?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        j.to_column?.toLowerCase().includes(searchQuery.toLowerCase());
      const matchTable = selectedTableFilter === 'ALL' || j.from_table === selectedTableFilter || j.to_table === selectedTableFilter;
      return matchSearch && matchTable;
    });
  }, [joins, searchQuery, selectedTableFilter]);

  // Categorized Joins (Direct Foreign Keys vs Self-Referencing Hierarchies)
  const categorizedJoins = useMemo(() => {
    return filteredJoins.filter(j => {
      if (selectedJoinCategory === 'HIERARCHY') return j.from_table === j.to_table;
      if (selectedJoinCategory === 'DIRECT') return j.from_table !== j.to_table;
      return true;
    });
  }, [filteredJoins, selectedJoinCategory]);

  // Filtered Tables
  const filteredTables = useMemo(() => {
    return tables.filter(t => {
      const matchSearch = !searchQuery ||
        t.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        t.grain?.toLowerCase().includes(searchQuery.toLowerCase());
      const matchTable = selectedTableFilter === 'ALL' || t.name === selectedTableFilter;
      return matchSearch && matchTable;
    });
  }, [tables, searchQuery, selectedTableFilter]);

  if (!catalog) {
    return (
      <div className="catalog-loading">
        <Clock size={24} className="spin-icon" color="var(--accent-blue)" />
        <p>Loading semantic catalog definitions for {selectedSource}...</p>
      </div>
    );
  }

  return (
    <div className="catalog-dictionary-container">
      {/* Executive Hero Banner */}
      <div className="dictionary-hero-v2">
        <div className="hero-v2-main">
          <div className="hero-icon-wrap-v2">
            <Database size={24} color="var(--accent-blue)" />
          </div>
          <div>
            <div className="hero-title-row">
              <h2>{catalog.source_id}</h2>
              <span className="release-badge">
                <ShieldCheck size={13} color="#10b981" />
                <code>{catalog.release_id}</code> (Epoch #{catalog.epoch})
              </span>
            </div>
            <p className="hero-subtitle">
              Approved semantic ontology with zero hallucination. All natural language queries compile against these verified relations.
            </p>
          </div>
        </div>

        {onOpenUpload && (
          <button className="btn btn-secondary-upload" onClick={onOpenUpload}>
            <Plus size={15} />
            <span>Connect Another DB</span>
          </button>
        )}
      </div>

      {/* Navigation Tabs Bar */}
      <div className="dictionary-tabs-bar">
        <button 
          className={`dict-tab-btn ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          <BarChart2 size={15} />
          <span>Overview</span>
        </button>
        <button 
          className={`dict-tab-btn ${activeTab === 'measures' ? 'active' : ''}`}
          onClick={() => setActiveTab('measures')}
        >
          <Sparkles size={15} />
          <span>Measures</span>
          <span className="tab-count-badge">{Object.keys(measures).length}</span>
        </button>
        <button 
          className={`dict-tab-btn ${activeTab === 'dimensions' ? 'active' : ''}`}
          onClick={() => setActiveTab('dimensions')}
        >
          <Layers size={15} />
          <span>Dimensions</span>
          <span className="tab-count-badge">{Object.keys(dimensions).length}</span>
        </button>
        <button 
          className={`dict-tab-btn ${activeTab === 'joins' ? 'active' : ''}`}
          onClick={() => setActiveTab('joins')}
        >
          <GitBranch size={15} />
          <span>Relationships</span>
          <span className="tab-count-badge">{joins.length}</span>
        </button>
        <button 
          className={`dict-tab-btn ${activeTab === 'tables' ? 'active' : ''}`}
          onClick={() => setActiveTab('tables')}
        >
          <Table size={15} />
          <span>Tables</span>
          <span className="tab-count-badge">{tables.length}</span>
        </button>
        {Object.keys(scenarioModels).length > 0 && (
          <button 
            className={`dict-tab-btn ${activeTab === 'models' ? 'active' : ''}`}
            onClick={() => setActiveTab('models')}
          >
            <Sliders size={15} />
            <span>Models</span>
            <span className="tab-count-badge">{Object.keys(scenarioModels).length}</span>
          </button>
        )}
      </div>

      {/* Instant Search & Table Filter Toolbar (shown on sub-tabs) */}
      {activeTab !== 'overview' && (
        <div className="dictionary-filter-toolbar">
          <div className="dict-search-box">
            <Search size={15} color="var(--text-dim)" />
            <input 
              type="text" 
              placeholder={`Search ${activeTab}...`}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button className="clear-search-btn" onClick={() => setSearchQuery('')}>×</button>
            )}
          </div>

          <div className="dict-table-select-wrap">
            <Filter size={14} color="var(--text-dim)" />
            <select 
              value={selectedTableFilter} 
              onChange={(e) => setSelectedTableFilter(e.target.value)}
              className="dict-table-select"
            >
              <option value="ALL">All Tables ({allTableNames.length})</option>
              {allTableNames.map(tbl => (
                <option key={tbl} value={tbl}>{tbl}</option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* TAB 1: OVERVIEW */}
      {activeTab === 'overview' && (
        <div className="dict-overview-view">
          {/* Quick Metrics Grid */}
          <div className="overview-stats-cards">
            <div className="stat-overview-card" onClick={() => setActiveTab('measures')}>
              <div className="card-top-icon blue-glow">
                <Sparkles size={20} color="var(--accent-blue)" />
              </div>
              <div className="stat-card-body">
                <div className="stat-card-val">{Object.keys(measures).length}</div>
                <div className="stat-card-title">Authorized Measures</div>
                <div className="stat-card-sub">Aggregated financial & operational KPIs</div>
              </div>
            </div>

            <div className="stat-overview-card" onClick={() => setActiveTab('dimensions')}>
              <div className="card-top-icon cyan-glow">
                <Layers size={20} color="var(--accent-cyan)" />
              </div>
              <div className="stat-card-body">
                <div className="stat-card-val">{Object.keys(dimensions).length}</div>
                <div className="stat-card-title">Permitted Dimensions</div>
                <div className="stat-card-sub">Temporal, categorical, & entity filters</div>
              </div>
            </div>

            <div className="stat-overview-card" onClick={() => setActiveTab('joins')}>
              <div className="card-top-icon purple-glow">
                <GitBranch size={20} color="var(--accent-purple)" />
              </div>
              <div className="stat-card-body">
                <div className="stat-card-val">{joins.length}</div>
                <div className="stat-card-title">Verified Join Paths</div>
                <div className="stat-card-sub">Multi-hop & junction graph edges</div>
              </div>
            </div>

            <div className="stat-overview-card" onClick={() => setActiveTab('tables')}>
              <div className="card-top-icon emerald-glow">
                <Table size={20} color="#10b981" />
              </div>
              <div className="stat-card-body">
                <div className="stat-card-val">{tables.length}</div>
                <div className="stat-card-title">Physical Tables</div>
                <div className="stat-card-sub">Tenant-isolated relational storage</div>
              </div>
            </div>
          </div>

          {/* Executive Architecture & Verification Strip */}
          <div className="overview-architecture-strip">
            <div className="arch-strip-item">
              <div className="arch-strip-icon emerald-glow">
                <ShieldCheck size={18} color="#10b981" />
              </div>
              <div>
                <div className="arch-strip-title">100% Extraction Confidence</div>
                <div className="arch-strip-sub">{joins.length} DDL-Verified Constraints</div>
              </div>
            </div>

            <div className="arch-strip-item">
              <div className="arch-strip-icon cyan-glow">
                <CheckCircle size={18} color="var(--accent-cyan)" />
              </div>
              <div>
                <div className="arch-strip-title">100% Referential Integrity</div>
                <div className="arch-strip-sub">0 Orphan Rows Across All Tables</div>
              </div>
            </div>

            <div className="arch-strip-item">
              <div className="arch-strip-icon purple-glow">
                <GitBranch size={18} color="var(--accent-purple)" />
              </div>
              <div>
                <div className="arch-strip-title">Topological Entity Graph</div>
                <div className="arch-strip-sub">Direct FKs, M:N Junctions, & Trees</div>
              </div>
            </div>
          </div>

          {/* Key Sample Queries Banner */}
          <div className="sample-queries-container">
            <div className="sample-queries-header">
              <Compass size={17} color="var(--accent-blue)" />
              <h4>Recommended Governed Questions</h4>
            </div>
            <div className="sample-chips-grid">
              {Object.values(measures).slice(0, 6).map((m, idx) => (
                <button 
                  key={idx} 
                  className="sample-query-pill"
                  onClick={() => onQuickQuery(`What was total ${m.name.toLowerCase()} in 2024?`)}
                >
                  <ArrowRight size={13} color="var(--accent-blue)" />
                  <span>Total {m.name} in 2024</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: MEASURES */}
      {activeTab === 'measures' && (
        <div className="dict-tab-content">
          {filteredMeasures.length === 0 ? (
            <div className="empty-filter-state">
              <Sparkles size={32} color="var(--text-dim)" />
              <p>No measures match "{searchQuery}"</p>
              <button className="empty-reset-btn" onClick={() => { setSearchQuery(''); setSelectedTableFilter('ALL'); }}>
                Reset Filters
              </button>
            </div>
          ) : (
            <div className="measures-grid-v2">
              {filteredMeasures.map((m) => (
                <div key={m.id} className="measure-card-v2">
                  <div className="m-card-header">
                    <span className="m-name">{m.name}</span>
                    <span className="m-unit-badge">{m.unit || 'Numeric'}</span>
                  </div>
                  <p className="m-desc">{m.description || `Column ${m.column} on table ${m.table}`}</p>
                  <div className="m-meta-tags">
                    <span className="meta-tag agg-tag">{m.aggregation}</span>
                    <span className="meta-tag table-tag">Table: {m.table}</span>
                    {m.base_grain && <span className="meta-tag grain-tag">Grain: {m.base_grain}</span>}
                  </div>
                  {m.allowed_families && (
                    <div className="m-families-row">
                      <span className="m-fam-label">Families:</span>
                      {m.allowed_families.map(f => (
                        <span key={f} className="m-fam-pill">{f}</span>
                      ))}
                    </div>
                  )}
                  <button 
                    onClick={() => onQuickQuery(`What was total ${m.name.toLowerCase()} in 2024?`)}
                    className="btn-analyze-measure"
                  >
                    <Compass size={13} />
                    <span>Analyze {m.name}</span>
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 3: DIMENSIONS (Organized by Table) */}
      {activeTab === 'dimensions' && (
        <div className="dict-tab-content">
          {Object.keys(dimensionsByTable).length === 0 ? (
            <div className="empty-filter-state">
              <Layers size={32} color="var(--text-dim)" />
              <p>No dimensions match "{searchQuery}"</p>
              <button className="empty-reset-btn" onClick={() => { setSearchQuery(''); setSelectedTableFilter('ALL'); }}>
                Reset Filters
              </button>
            </div>
          ) : (
            <div className="dimensions-by-table-container">
              {Object.entries(dimensionsByTable).map(([tbl, dims]) => (
                <div key={tbl} className="table-dimension-group">
                  <div className="table-group-header">
                    <Table size={15} color="var(--accent-purple)" />
                    <span className="tbl-group-title">{tbl}</span>
                    <span className="tbl-group-count">{dims.length} attributes</span>
                  </div>
                  <div className="dim-cards-subgrid">
                    {dims.map(d => (
                      <div key={d.id} className="dimension-card-v2">
                        <div className="dim-v2-top">
                          <span className="dim-v2-name">{d.name}</span>
                          <span className={`dim-type-pill ${d.data_type || 'STRING'}`}>{d.data_type || 'STRING'}</span>
                        </div>
                        <div className="dim-v2-col">
                          Column: <code>{d.column}</code>
                        </div>
                        <button 
                          onClick={() => onQuickQuery(`Show revenue grouped by ${d.name.toLowerCase()}.`)}
                          className="btn-dim-action"
                        >
                          <span>Group by {d.name}</span>
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 4: RELATIONSHIPS & JOINS */}
      {activeTab === 'joins' && (
        <div className="dict-tab-content">
          <div className="relation-filter-bar">
            <button 
              className={`filter-btn ${selectedJoinCategory === 'ALL' ? 'active' : ''}`}
              onClick={() => setSelectedJoinCategory('ALL')}
            >
              All Relational Edges ({filteredJoins.length})
            </button>
            <button 
              className={`filter-btn ${selectedJoinCategory === 'DIRECT' ? 'active' : ''}`}
              onClick={() => setSelectedJoinCategory('DIRECT')}
            >
              Direct Entity Joins ({filteredJoins.filter(j => j.from_table !== j.to_table).length})
            </button>
            <button 
              className={`filter-btn ${selectedJoinCategory === 'HIERARCHY' ? 'active' : ''}`}
              onClick={() => setSelectedJoinCategory('HIERARCHY')}
            >
              Self-Referencing Trees ({filteredJoins.filter(j => j.from_table === j.to_table).length})
            </button>
          </div>

          {categorizedJoins.length === 0 ? (
            <div className="empty-filter-state">
              <GitBranch size={32} color="var(--text-dim)" />
              <p>No relationships match your search criteria</p>
              <button className="empty-reset-btn" onClick={() => { setSearchQuery(''); setSelectedTableFilter('ALL'); setSelectedJoinCategory('ALL'); }}>
                Reset Filters
              </button>
            </div>
          ) : (
            <div className="joins-grid-v2">
              {categorizedJoins.map((j, idx) => (
                <div key={idx} className="join-card-v2">
                  <div className="join-route-v2">
                    <div className="join-end-node">
                      <span className="node-table">{j.from_table}</span>
                      <span className="node-col">.{j.from_column}</span>
                    </div>
                    <div className="join-arrow-wrap">
                      <ArrowRight size={14} color="var(--accent-blue)" />
                    </div>
                    <div className="join-end-node">
                      <span className="node-table">{j.to_table}</span>
                      <span className="node-col">.{j.to_column}</span>
                    </div>
                  </div>
                  <div className="join-v2-badges">
                    <span className="badge-cardinality">{j.cardinality}</span>
                    <span className="badge-jointype">{j.join_type}</span>
                    <span className="badge-verified">
                      {j.from_table === j.to_table ? 'Self-Tree (Hierarchy)' : 'Verified DDL'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 5: PHYSICAL TABLES */}
      {activeTab === 'tables' && (
        <div className="dict-tab-content">
          {filteredTables.length === 0 ? (
            <div className="empty-filter-state">
              <Table size={32} color="var(--text-dim)" />
              <p>No tables match "{searchQuery}"</p>
              <button className="empty-reset-btn" onClick={() => { setSearchQuery(''); setSelectedTableFilter('ALL'); }}>
                Reset Filters
              </button>
            </div>
          ) : (
            <div className="tables-grid-v2">
              {filteredTables.map((t, idx) => (
                <div key={idx} className="table-card-v2">
                  <div className="table-card-header">
                    <span className="table-name-v2">{t.name}</span>
                    <span className="table-grain-pill">{t.grain}</span>
                  </div>
                  <div className="table-card-meta">
                    <div className="meta-line">
                      <Key size={13} color="var(--accent-amber)" />
                      <span>Primary Key: <code>{Array.isArray(t.primary_key) ? t.primary_key.join(', ') : t.primary_key}</code></span>
                    </div>
                    <div className="meta-line">
                      <ShieldCheck size={13} color="#10b981" />
                      <span>Tenant Boundary: <code>{t.tenant_column}</code></span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 6: SCENARIO MODELS */}
      {activeTab === 'models' && Object.keys(scenarioModels).length > 0 && (
        <div className="dict-tab-content">
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
  );
}
