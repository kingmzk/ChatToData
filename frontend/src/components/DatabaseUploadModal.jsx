import React, { useState } from 'react';
import { 
  Upload, 
  Database, 
  CheckCircle, 
  AlertCircle, 
  X, 
  GitBranch, 
  Sparkles, 
  Layers, 
  ArrowRight,
  Clock
} from 'lucide-react';
import { api } from '../services/api';

export default function DatabaseUploadModal({ isOpen, onClose, onSourceCreated }) {
  const [file, setFile] = useState(null);
  const [sourceName, setSourceName] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStep, setUploadStep] = useState(0); // 0: Idle, 1: Uploading, 2: Introspecting, 3: Extracting joins, 4: Done
  const [error, setError] = useState(null);
  const [manifest, setManifest] = useState(null);

  if (!isOpen) return null;

  const handleFileChange = (e) => {
    const selected = e.target.files[0];
    if (selected) {
      setFile(selected);
      if (!sourceName) {
        // Auto-generate clean source name from filename
        const clean = selected.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_]/g, "_").toLowerCase();
        setSourceName(clean);
      }
      setError(null);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const dropped = e.dataTransfer.files[0];
      setFile(dropped);
      if (!sourceName) {
        const clean = dropped.name.replace(/\.[^/.]+$/, "").replace(/[^a-zA-Z0-9_]/g, "_").toLowerCase();
        setSourceName(clean);
      }
      setError(null);
    }
  };

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!file) {
      setError('Please select a SQLite database file (.sqlite, .db, .sqlite3).');
      return;
    }

    setIsUploading(true);
    setError(null);
    setUploadStep(1);

    try {
      // Step 1: Uploading
      setTimeout(() => setUploadStep(2), 500); // Introspecting
      setTimeout(() => setUploadStep(3), 1000); // Extracting relationships

      const res = await api.uploadDatabase(file, sourceName);
      setUploadStep(4);
      setManifest(res.manifest);
    } catch (err) {
      setError(err.message || 'Failed to upload and introspect database.');
      setUploadStep(0);
    } finally {
      setIsUploading(false);
    }
  };

  const handleStartAnalyzing = () => {
    if (manifest && manifest.source_id) {
      onSourceCreated(manifest.source_id);
      onClose();
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-dialog upload-db-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-wrap">
            <Database size={22} color="var(--accent-blue)" />
            <h3>Connect & Introspect New Database</h3>
          </div>
          <button className="icon-btn close-btn" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <div className="modal-body">
          {!manifest ? (
            <form onSubmit={handleUpload} className="upload-form">
              <p className="modal-subtitle">
                Upload a SQLite database file. Talk2Data will automatically discover tables,
                extract primary/foreign-key relationships, and infer analytical measures & dimensions.
              </p>

              {/* Drag and Drop Area */}
              <div 
                className={`dropzone ${file ? 'has-file' : ''}`}
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                onClick={() => document.getElementById('db-file-input').click()}
              >
                <input 
                  id="db-file-input" 
                  type="file" 
                  accept=".sqlite,.db,.sqlite3" 
                  onChange={handleFileChange} 
                  style={{ display: 'none' }} 
                />
                <div className="dropzone-content">
                  <Upload size={36} className="dropzone-icon" />
                  {file ? (
                    <div className="dropzone-file-info">
                      <span className="file-name">{file.name}</span>
                      <span className="file-size">{(file.size / 1024).toFixed(1)} KB</span>
                    </div>
                  ) : (
                    <div>
                      <p className="dropzone-prompt"><strong>Click to browse</strong> or drag & drop database here</p>
                      <p className="dropzone-hint">Supports SQLite (.sqlite, .db, .sqlite3)</p>
                    </div>
                  )}
                </div>
              </div>

              {/* Source Identifier */}
              <div className="input-group">
                <label htmlFor="source-name-input">Source Identifier (Catalog ID)</label>
                <input 
                  id="source-name-input"
                  type="text" 
                  className="text-input" 
                  placeholder="e.g. ecommerce_production" 
                  value={sourceName}
                  onChange={(e) => setSourceName(e.target.value)}
                  disabled={isUploading}
                />
                <span className="input-hint">Unique identifier used for governance and session pinning.</span>
              </div>

              {/* Error message */}
              {error && (
                <div className="alert-box alert-error">
                  <AlertCircle size={18} />
                  <span>{error}</span>
                </div>
              )}

              {/* Progress Steps during upload */}
              {isUploading && (
                <div className="upload-progress-card">
                  <div className="progress-step-item">
                    <Clock size={16} className="spin-icon" color="var(--accent-blue)" />
                    <span>
                      {uploadStep === 1 && 'Uploading database file to secure sandbox...'}
                      {uploadStep === 2 && 'Scanning tables, column data types & grains...'}
                      {uploadStep === 3 && 'Discovering foreign keys & entity relationships...'}
                      {uploadStep === 4 && 'Registering semantic catalog release...'}
                    </span>
                  </div>
                </div>
              )}

              <div className="modal-actions">
                <button type="button" className="btn btn-secondary" onClick={onClose} disabled={isUploading}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={isUploading || !file}>
                  {isUploading ? 'Introspecting Database...' : 'Upload & Discover Schema'}
                </button>
              </div>
            </form>
          ) : (
            /* Discovery Success Manifest */
            <div className="discovery-success-view">
              <div className="success-banner">
                <CheckCircle size={32} color="var(--accent-emerald)" />
                <div>
                  <h4>Database Successfully Connected!</h4>
                  <p>Catalog registered as <code>{manifest.source_id}</code>.</p>
                </div>
              </div>

              {/* Quick Metrics */}
              <div className="discovery-stats-row">
                <div className="stat-card">
                  <span className="stat-label">Tables</span>
                  <span className="stat-num">{manifest.summary.total_tables}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Relationships</span>
                  <span className="stat-num">{manifest.summary.total_relationships}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Measures</span>
                  <span className="stat-num">{manifest.summary.total_measures}</span>
                </div>
                <div className="stat-card">
                  <span className="stat-label">Dimensions</span>
                  <span className="stat-num">{manifest.summary.total_dimensions}</span>
                </div>
              </div>

              {/* Discovered Relationships */}
              {manifest.relationships && manifest.relationships.length > 0 && (
                <div className="manifest-section">
                  <div className="section-title">
                    <GitBranch size={16} color="var(--accent-blue)" />
                    <span>Auto-Discovered Entity Relationships</span>
                  </div>
                  <div className="manifest-list">
                    {manifest.relationships.map((rel, idx) => (
                      <div key={idx} className="relationship-chip">
                        <span className="rel-from"><strong>{rel.from_table}</strong>.{rel.from_column}</span>
                        <ArrowRight size={14} color="var(--accent-blue)" />
                        <span className="rel-to"><strong>{rel.to_table}</strong>.{rel.to_column}</span>
                        <span className="cardinality-badge">{rel.cardinality}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Discovered Tables */}
              <div className="manifest-section">
                <div className="section-title">
                  <Layers size={16} color="var(--accent-purple)" />
                  <span>Physical Tables ({manifest.tables.length})</span>
                </div>
                <div className="tables-pill-list">
                  {manifest.tables.map((t, idx) => (
                    <div key={idx} className="table-summary-pill">
                      <span className="tbl-name">{t.name}</span>
                      <span className="tbl-rows">{t.row_count} rows</span>
                      <span className="tbl-cols">{t.columns.length} columns</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="modal-actions">
                <button className="btn btn-primary" onClick={handleStartAnalyzing}>
                  Start Analyzing This Database
                  <ArrowRight size={16} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
