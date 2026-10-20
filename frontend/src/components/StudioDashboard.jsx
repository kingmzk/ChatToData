import React, { useState } from 'react';
import { 
  Code2,
  Sparkles, 
  CheckCircle, 
  Clock, 
  Shield, 
  FileText, 
  Copy, 
  Check, 
  ChevronRight, 
  ShieldAlert,
  AlertOctagon,
  XCircle,
  RefreshCw,
  BookOpen,
  ArrowRight,
  TrendingUp,
  BarChart3,
  GitCompare,
  Zap,
  SlidersHorizontal,
  Lock,
  Database
} from 'lucide-react';
import KPIGrid from './KPIGrid';
import AnalyticsChart from './AnalyticsChart';
import DataTable from './DataTable';
import ClarificationCard from './ClarificationCard';

export default function StudioDashboard({ 
  activeTurn, 
  answer, 
  followUps, 
  onSelectChoice, 
  onSubmitQuestion,
  onNavigateToCatalog,
  sourceCatalog,
  selectedSource
}) {
  const [copiedDigest, setCopiedDigest] = useState(false);
  const [copiedSql, setCopiedSql] = useState(false);
  const [copiedSummary, setCopiedSummary] = useState(false);

  const copySql = (sql) => {
    if (!sql) return;
    navigator.clipboard.writeText(sql);
    setCopiedSql(true);
    setTimeout(() => setCopiedSql(false), 2000);
  };

  const copyDigest = (digest) => {
    if (!digest) return;
    navigator.clipboard.writeText(digest);
    setCopiedDigest(true);
    setTimeout(() => setCopiedDigest(false), 2000);
  };

  const copySummary = (text) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedSummary(true);
    setTimeout(() => setCopiedSummary(false), 2000);
  };

  if (!activeTurn) {
    const tableCount = sourceCatalog?.tables?.length || 0;
    const metricCount = Object.keys(sourceCatalog?.measures || {}).length;
    const currentSrc = selectedSource || 'Default Enterprise DB';

    return (
      <div className="studio-empty-state executive-hub">
        {/* Intelligence Studio Hero Section */}
        <div className="hub-hero">
          <div className="hub-badge-pill">
            <span className="hub-pulsing-dot" />
            <span>Deterministic Analytics Engine v2.4</span>
            <span className="hub-badge-divider">•</span>
            <span className="hub-badge-highlight">Zero-Hallucination Active</span>
          </div>

          <h1 className="hub-main-title">
            Enterprise Data Intelligence Studio
          </h1>
          <p className="hub-main-subtitle">
            Query governed databases with natural language. Every question is transpiled to strict AST SQL, verified against approved semantic models, and cryptographically signed.
          </p>

          {/* Quick Telemetry Bar */}
          <div className="hub-telemetry-bar">
            <div className="telemetry-item">
              <Database size={15} className="telemetry-icon" />
              <div className="telemetry-text">
                <span className="telemetry-label">Active Source</span>
                <span className="telemetry-val">{currentSrc}</span>
              </div>
            </div>
            <div className="telemetry-item">
              <BarChart3 size={15} className="telemetry-icon" />
              <div className="telemetry-text">
                <span className="telemetry-label">Approved Schema</span>
                <span className="telemetry-val">{tableCount > 0 ? `${tableCount} Tables • ${metricCount} Metrics` : 'Introspected'}</span>
              </div>
            </div>
            <div className="telemetry-item">
              <Shield size={15} className="telemetry-icon" />
              <div className="telemetry-text">
                <span className="telemetry-label">Audit Perimeter</span>
                <span className="telemetry-val">SHA-256 Release Pin</span>
              </div>
            </div>
            <div className="telemetry-item">
              <Lock size={15} className="telemetry-icon" />
              <div className="telemetry-text">
                <span className="telemetry-label">Privacy Guardrails</span>
                <span className="telemetry-val">K-Anonymity Enforced</span>
              </div>
            </div>
          </div>
        </div>

        {/* Categorized Analytical Query Matrix */}
        <div className="hub-matrix-header">
          <div className="matrix-title-group">
            <Sparkles size={16} color="var(--accent-cyan)" />
            <h3>Curated Analytical Templates & Inquiries</h3>
          </div>
          <span className="matrix-subtitle">Select any question card to execute an instant governed analysis</span>
        </div>

        <div className="hub-query-matrix">
          {/* Card 1: Revenue & Financials */}
          <div className="matrix-card card-revenue">
            <div className="matrix-card-header">
              <div className="card-icon-pill icon-blue">
                <TrendingUp size={18} />
              </div>
              <span className="card-category-tag">Financial Performance</span>
            </div>
            <h4 className="card-title">Revenue & Sales Trajectory</h4>
            <p className="card-desc">Calculate aggregated gross sales, profit margins, and performance KPIs across fiscal periods.</p>
            <div className="card-queries">
              <button 
                onClick={() => onSubmitQuestion('What was total sales revenue in 2024?')}
                className="matrix-query-btn"
              >
                <span>"What was total sales revenue in 2024?"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
              <button 
                onClick={() => onSubmitQuestion('What are the top 5 product categories by revenue in 2024?')}
                className="matrix-query-btn"
              >
                <span>"Top 5 product categories by revenue in 2024"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
            </div>
          </div>

          {/* Card 2: Predictive Forecasting */}
          <div className="matrix-card card-forecast">
            <div className="matrix-card-header">
              <div className="card-icon-pill icon-purple">
                <Zap size={18} />
              </div>
              <span className="card-category-tag">Decision Science</span>
            </div>
            <h4 className="card-title">Predictive Forecasting</h4>
            <p className="card-desc">Holt-Winters statistical extrapolation with confidence bands and seasonal decomposition.</p>
            <div className="card-queries">
              <button 
                onClick={() => onSubmitQuestion('Forecast annual revenue for the next two years.')}
                className="matrix-query-btn"
              >
                <span>"Forecast annual revenue for the next 2 years"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
              <button 
                onClick={() => onSubmitQuestion('What is the projected revenue growth rate for 2025?')}
                className="matrix-query-btn"
              >
                <span>"Projected revenue growth rate for 2025"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
            </div>
          </div>

          {/* Card 3: Comparative Analysis */}
          <div className="matrix-card card-compare">
            <div className="matrix-card-header">
              <div className="card-icon-pill icon-cyan">
                <GitCompare size={18} />
              </div>
              <span className="card-category-tag">Comparative Analytics</span>
            </div>
            <h4 className="card-title">Multi-Period Variance</h4>
            <p className="card-desc">Calculate year-over-year deltas, quarterly variance matrices, and segment divergence.</p>
            <div className="card-queries">
              <button 
                onClick={() => onSubmitQuestion('Compare revenue between 2023 and 2024.')}
                className="matrix-query-btn"
              >
                <span>"Compare revenue between 2023 and 2024"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
              <button 
                onClick={() => onSubmitQuestion('Compare sales volume across product lines.')}
                className="matrix-query-btn"
              >
                <span>"Compare sales volume across product lines"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
            </div>
          </div>

          {/* Card 4: Anomaly & Audit */}
          <div className="matrix-card card-audit">
            <div className="matrix-card-header">
              <div className="card-icon-pill icon-emerald">
                <SlidersHorizontal size={18} />
              </div>
              <span className="card-category-tag">Quality & Anomalies</span>
            </div>
            <h4 className="card-title">Anomaly & Boundary Audit</h4>
            <p className="card-desc">Scan for statistical outliers, unusual volume spikes, and inspect catalog schema lineage.</p>
            <div className="card-queries">
              <button 
                onClick={() => onSubmitQuestion('Detect anomalies and outliers in monthly sales.')}
                className="matrix-query-btn"
              >
                <span>"Detect anomalies and outliers in sales"</span>
                <ArrowRight size={13} className="query-arrow" />
              </button>
              {onNavigateToCatalog && (
                <button 
                  onClick={onNavigateToCatalog}
                  className="matrix-query-btn browse-catalog-btn"
                >
                  <BookOpen size={13} />
                  <span>Browse approved measures & dimensions →</span>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Governed Architecture Guarantee */}
        <div className="hub-architecture-strip">
          <div className="arch-step">
            <span className="step-num">01</span>
            <div className="step-content">
              <strong>Intent Parsing</strong>
              <span>Semantic entity extraction</span>
            </div>
          </div>
          <ChevronRight size={16} className="step-chevron" />
          <div className="arch-step">
            <span className="step-num">02</span>
            <div className="step-content">
              <strong>AST Transpilation</strong>
              <span>Read-only SQL generation</span>
            </div>
          </div>
          <ChevronRight size={16} className="step-chevron" />
          <div className="arch-step">
            <span className="step-num">03</span>
            <div className="step-content">
              <strong>Perimeter Verification</strong>
              <span>Tenant & grain suppression</span>
            </div>
          </div>
          <ChevronRight size={16} className="step-chevron" />
          <div className="arch-step">
            <span className="step-num">04</span>
            <div className="step-content">
              <strong>Cryptographic Seal</strong>
              <span>SHA-256 evidence package</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  const isAnswering = ['SUBMITTED', 'INTERPRETING', 'ANALYZING', 'COMPOSING'].includes(activeTurn.status);
  const isAwaitingClarif = activeTurn.status === 'AWAITING_CLARIFICATION';
  const isDeclined = activeTurn.status === 'DECLINED';
  const isFailed = activeTurn.status === 'FAILED';
  const isCancelled = activeTurn.status === 'CANCELLED';

  return (
    <div className="studio-dashboard-container">
      {/* Turn Header Strip */}
      <div className="studio-headline-card">
        <div className="headline-main">
          <div className="headline-meta">
            <span className="source-tag">
              <Database size={12} />
              <span>{activeTurn.source_id}</span>
            </span>
            <span className="turn-id-tag">Turn: #{activeTurn.turn_id?.slice(-8) || activeTurn.turn_id}</span>
            {activeTurn.proposed_family && (
              <span className="badge-science">{activeTurn.proposed_family}</span>
            )}
            <span className={`status-pill ${activeTurn.status.toLowerCase()}`}>
              {isAnswering ? <Clock size={12} className="spin-icon" /> : null}
              {activeTurn.status === 'ANSWERED' ? <CheckCircle size={12} /> : null}
              {isAwaitingClarif ? <ShieldAlert size={12} /> : null}
              {isDeclined ? <ShieldAlert size={12} /> : null}
              {isFailed ? <AlertOctagon size={12} /> : null}
              {isCancelled ? <XCircle size={12} /> : null}
              <span>{activeTurn.status}</span>
            </span>
          </div>
          <h2 className="turn-question-title">"{activeTurn.question_text}"</h2>
        </div>
      </div>

      {/* Processing State */}
      {isAnswering && (
        <div className="processing-hero-banner">
          <div className="processing-indicator-wrap">
            <Clock size={32} className="spin-icon" color="var(--accent-cyan)" />
          </div>
          <div className="processing-details">
            <h3>Processing analysis with governed validation...</h3>
            <p>Executing deterministic SQL transpilation, tenant perimeter fencing, and differential privacy checks.</p>
            <div className="processing-progress-bar">
              <div className="progress-bar-fill animated-shimmer" />
            </div>
          </div>
        </div>
      )}

      {/* Clarification State */}
      {isAwaitingClarif && (
        <ClarificationCard turn={activeTurn} onSelectChoice={onSelectChoice} />
      )}

      {/* Governed Policy Declined State */}
      {isDeclined && (
        <div className="governance-declined-card">
          <div className="declined-hero-header">
            <div className="declined-icon-wrap">
              <ShieldAlert size={28} color="var(--accent-amber)" />
            </div>
            <div>
              <h3>Query Declined by Governance Engine</h3>
              <p className="declined-lead">
                {activeTurn.decline_reason || 'This question requested metrics, unapproved entities, or grain levels outside the authorized catalog perimeter.'}
              </p>
            </div>
          </div>
          <div className="declined-remediation-box">
            <h4>Why was this query declined?</h4>
            <p>
              Talk2Data strictly enforces zero-hallucination mathematical governance. We only execute queries against
              approved metric formulas, validated dimensions, and authorized join paths in your pinned semantic release.
            </p>
            <div className="declined-actions">
              {onNavigateToCatalog && (
                <button onClick={onNavigateToCatalog} className="btn-explore-catalog">
                  <BookOpen size={14} />
                  <span>Browse Approved Measures & Dimensions</span>
                </button>
              )}
              <button 
                onClick={() => onSubmitQuestion('What was total sales revenue in 2024?')} 
                className="btn-try-sample"
              >
                <span>Try: "What was total sales revenue in 2024?"</span>
                <ArrowRight size={13} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pipeline Error State */}
      {isFailed && (
        <div className="pipeline-error-card">
          <div className="error-hero-header">
            <div className="error-icon-wrap">
              <AlertOctagon size={28} color="var(--accent-rose)" />
            </div>
            <div>
              <h3>Analytical Execution Failure</h3>
              <p className="error-lead">
                {activeTurn.error_message || 'The analytical execution pipeline encountered an unexpected error.'}
              </p>
            </div>
          </div>
          <div className="error-action-row">
            <button 
              onClick={() => onSubmitQuestion(activeTurn.question_text)} 
              className="btn-retry-query"
            >
              <RefreshCw size={14} />
              <span>Retry Question</span>
            </button>
          </div>
        </div>
      )}

      {/* Cancelled State */}
      {isCancelled && (
        <div className="pipeline-cancelled-card">
          <div className="cancelled-hero-header">
            <XCircle size={24} color="var(--text-dim)" />
            <div>
              <h3>Analysis Cancelled</h3>
              <p>This query turn was cancelled by user request before completion.</p>
            </div>
          </div>
          <button 
            onClick={() => onSubmitQuestion(activeTurn.question_text)} 
            className="btn-retry-query"
          >
            <RefreshCw size={14} />
            <span>Re-run Question</span>
          </button>
        </div>
      )}

      {/* Answer View */}
      {answer && (
        <div className="studio-answer-content">
          {/* Executive Summary Banner */}
          {answer.summary_text && (
            <div className="verified-summary-banner">
              <div className="summary-header-row">
                <div className="summary-badge">
                  <CheckCircle size={15} color="var(--accent-emerald)" />
                  <span>Verified Executive Summary</span>
                </div>
                <button 
                  onClick={() => copySummary(answer.summary_text)}
                  className="btn-copy-summary"
                  title="Copy executive summary"
                >
                  {copiedSummary ? <Check size={13} color="#10b981" /> : <Copy size={13} />}
                  <span>{copiedSummary ? 'Copied' : 'Copy'}</span>
                </button>
              </div>
              <p className="summary-paragraph">{answer.summary_text}</p>
            </div>
          )}

          {/* KPI Strip */}
          <KPIGrid kpis={answer.kpi_cards} />

          {/* Visual Analytics & Data Table */}
          <div className="analytics-visual-grid">
            {answer.charts && answer.charts.length > 0 && (
              <AnalyticsChart charts={answer.charts} />
            )}
            {(answer.table || (answer.table_columns && answer.table_columns.length > 0)) && (
              <DataTable 
                table={answer.table || { columns: answer.table_columns, rows: answer.table_rows }} 
                turnId={activeTurn.turn_id} 
              />
            )}
          </div>

          {/* Decision Science & Policy Breakdown */}
          <div className="science-policy-row">
            <div className="policy-card">
              <div className="policy-header">
                <FileText size={16} color="var(--accent-cyan)" />
                <span>Analytical Assumptions & Lineage</span>
              </div>
              <ul className="policy-list">
                {answer.assumptions && answer.assumptions.length > 0 ? (
                  answer.assumptions.map((assump, aIdx) => <li key={aIdx}>{assump}</li>)
                ) : (
                  <li>Standard catalog definitions applied under zero-leakage policy.</li>
                )}
                {answer.limitations && answer.limitations.map((lim, lIdx) => (
                  <li key={`lim-${lIdx}`} className="limitation-item">⚠️ {lim}</li>
                ))}
              </ul>
            </div>

            {/* Cryptographic Evidence Package Seal */}
            {answer.evidence_digest && (
              <div className="evidence-digest-card">
                <div className="evidence-card-header">
                  <div className="seal-title-wrap">
                    <Shield size={16} color="var(--accent-emerald)" />
                    <span>Tamper-Evident SHA-256 Evidence Seal</span>
                  </div>
                  <span className="seal-verified-pill">Cryptographically Verified</span>
                </div>
                <p className="evidence-desc">
                  This analytical payload is cryptographically bound to semantic catalog release <code>{answer.pinned_release_id || 'rel_active'}</code>.
                </p>
                <div className="digest-code-box">
                  <code>{answer.evidence_digest}</code>
                  <button 
                    onClick={() => copyDigest(answer.evidence_digest)} 
                    className="btn-copy-digest"
                    title="Copy SHA-256 Evidence Digest"
                  >
                    {copiedDigest ? <Check size={14} color="#10b981" /> : <Copy size={14} />}
                    <span>{copiedDigest ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Generated Governed SQL Query Section (POC Mode) */}
          {answer.sql_query && (
            <div className="governed-sql-card">
              <div className="sql-card-header">
                <div className="sql-card-title">
                  <div className="sql-window-dots">
                    <span className="dot red" />
                    <span className="dot yellow" />
                    <span className="dot green" />
                  </div>
                  <Code2 size={16} color="var(--accent-cyan)" />
                  <span>Compiled Governed SQL AST (POC Mode)</span>
                  <span className="sql-badge">Strict Parameterized AST</span>
                </div>
                <button 
                  onClick={() => copySql(answer.sql_query)} 
                  className="btn-copy-digest"
                  title="Copy Compiled SQL Query"
                >
                  {copiedSql ? <Check size={14} color="#10b981" /> : <Copy size={14} />}
                  <span>{copiedSql ? 'Copied' : 'Copy SQL'}</span>
                </button>
              </div>
              <div className="sql-code-container">
                <pre className="sql-pre"><code>{answer.sql_query}</code></pre>
              </div>
            </div>
          )}

          {/* Suggested Follow-Ups */}
          {followUps && followUps.length > 0 && (
            <div className="followups-banner">
              <div className="followups-header">
                <Sparkles size={15} color="var(--accent-cyan)" />
                <span className="followups-title">Recommended Analytical Inquiries:</span>
              </div>
              <div className="followup-pills">
                {followUps.map((q, idx) => (
                  <button key={idx} onClick={() => onSubmitQuestion(q)} className="followup-btn">
                    <span>{q}</span>
                    <ChevronRight size={13} />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
