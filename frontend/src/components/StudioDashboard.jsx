import React, { useState } from 'react';
import { 
  Sparkles, 
  CheckCircle, 
  Clock, 
  Shield, 
  FileText, 
  Copy, 
  Check, 
  ChevronRight, 
  Lock,
  Layers,
  Activity,
  AlertTriangle,
  ShieldAlert,
  AlertOctagon,
  XCircle,
  RefreshCw,
  BookOpen,
  ArrowRight
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
  onNavigateToCatalog
}) {
  const [copiedDigest, setCopiedDigest] = useState(false);

  const copyDigest = (digest) => {
    if (!digest) return;
    navigator.clipboard.writeText(digest);
    setCopiedDigest(true);
    setTimeout(() => setCopiedDigest(false), 2000);
  };

  if (!activeTurn) {
    return (
      <div className="studio-empty-state">
        <Sparkles size={48} color="var(--accent-blue)" style={{ opacity: 0.8 }} />
        <h2>Executive Analytical Studio Workspace</h2>
        <p>Ask an analytical question below or pick a query from the catalog dictionary.</p>
        <div className="quick-start-tips">
          <div className="tip-pill" onClick={() => onSubmitQuestion('What was total sales revenue in 2024?')}>
            <span>💡 "What was total sales revenue in 2024?"</span>
          </div>
          <div className="tip-pill" onClick={() => onSubmitQuestion('Compare revenue between 2023 and 2024.')}>
            <span>📊 "Compare revenue between 2023 and 2024"</span>
          </div>
          <div className="tip-pill" onClick={() => onSubmitQuestion('Forecast annual revenue for the next two years.')}>
            <span>📈 "Forecast annual revenue for the next 2 years"</span>
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
            <span className="source-tag">{activeTurn.source_id}</span>
            <span className="turn-id-tag">Turn: {activeTurn.turn_id}</span>
            {activeTurn.proposed_family && (
              <span className="badge-science">{activeTurn.proposed_family}</span>
            )}
            <span className={`status-pill ${activeTurn.status.toLowerCase()}`}>
              {isAnswering ? <Clock size={12} className="spin-icon" /> : null}
              {activeTurn.status === 'ANSWERED' ? <CheckCircle size={12} /> : null}
              {isAwaitingClarif ? <AlertTriangle size={12} /> : null}
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
          <Clock size={28} className="spin-icon" color="var(--accent-blue)" />
          <div>
            <h3>Processing analysis with governed validation...</h3>
            <p>Executing deterministic SQL transpilation and privacy-boundary checks.</p>
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
            <h4>Why was this declined?</h4>
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
              <div className="summary-badge">
                <CheckCircle size={15} color="var(--accent-emerald)" />
                <span>Verified Summary</span>
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
                <FileText size={16} color="var(--accent-blue)" />
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
                  <Shield size={16} color="var(--accent-emerald)" />
                  <span>Tamper-Evident SHA-256 Evidence Seal</span>
                </div>
                <p className="evidence-desc">
                  This analytical payload is cryptographically bound to semantic catalog release {answer.pinned_release_id || 'rel_active'}.
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

          {/* Suggested Follow-Ups */}
          {followUps && followUps.length > 0 && (
            <div className="followups-banner">
              <span className="followups-title">Recommended Follow-Up Inquiries:</span>
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
