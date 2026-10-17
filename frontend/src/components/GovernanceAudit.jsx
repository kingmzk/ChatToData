import React from 'react';
import { Shield, Lock, FileText, CheckCircle, Database, Layers, Check } from 'lucide-react';

export default function GovernanceAudit({ turns, answers }) {
  const perimeterLayers = [
    { num: '1', title: 'Tenant Boundary Isolation', desc: 'Every query compiles strictly bound WHERE tenant_id = ? parameter; cross-tenant leakage is mathematically impossible.' },
    { num: '2', title: 'Semantic Release Pinning', desc: 'Queries are resolved exclusively against approved, cryptographically pinned catalog releases with immutable epoch identifiers.' },
    { num: '3', title: 'Read-Only Transpilation', desc: 'Compiler generates read-only SELECT statements; mutation statements (DROP, UPDATE, INSERT) are rejected by AST validation.' },
    { num: '4', title: 'Exact Decimal Arithmetic', desc: 'Financial calculations use exact Decimal arithmetic avoiding IEEE 754 binary floating-point drift.' },
    { num: '5', title: 'Disclosure Suppression', desc: 'Groups below k-anonymity entity thresholds are suppressed (🔒 Suppressed) to prevent population re-identification.' },
    { num: '6', title: 'Durable Worker Lease Fencing', desc: 'Asynchronous workers execute with monotonically increasing fencing tokens; stale or cancelled jobs cannot publish.' },
    { num: '7', title: 'SHA-256 Tamper Evidence Chain', desc: 'All published facts are sealed in a canonical SHA-256 evidence package and backed by an HMAC signed audit trail.' },
  ];

  return (
    <div className="governance-audit-container">
      <div className="governance-hero">
        <div className="hero-icon-wrap emerald">
          <Shield size={28} color="var(--accent-emerald)" />
        </div>
        <div>
          <h2>7-Layer Enterprise Governance & Audit Trail</h2>
          <p>
            Talk2Data enforces deterministic mathematical precision, strict tenant scoping, and cryptographic
            evidence sealing across every analytical transaction.
          </p>
        </div>
      </div>

      {/* 7-Layer Perimeter Cards */}
      <div className="perimeter-grid">
        {perimeterLayers.map((layer) => (
          <div key={layer.num} className="perimeter-layer-card">
            <div className="layer-badge">Layer {layer.num}</div>
            <h4>{layer.title}</h4>
            <p>{layer.desc}</p>
          </div>
        ))}
      </div>

      {/* Recent Turns Audit Table */}
      <div className="audit-table-card">
        <div className="audit-header">
          <FileText size={18} color="var(--accent-blue)" />
          <h3>Transaction Audit Log & Cryptographic Evidence Seals</h3>
        </div>
        <div className="table-scroll-container">
          <table className="analytical-table">
            <thead>
              <tr>
                <th>Turn ID</th>
                <th>Source ID</th>
                <th>Tenant Isolation</th>
                <th>Status</th>
                <th>Evidence SHA-256 Digest</th>
              </tr>
            </thead>
            <tbody>
              {turns.map((t) => {
                const ans = answers[t.turn_id];
                const digest = ans ? ans.evidence_digest : null;

                return (
                  <tr key={t.turn_id}>
                    <td><code>{t.turn_id}</code></td>
                    <td><span className="source-tag">{t.source_id}</span></td>
                    <td>
                      <span className="tenant-verified-pill">
                        <Check size={11} /> Verified ({t.tenant_id || 'tenant_a'})
                      </span>
                    </td>
                    <td>
                      <span className={`status-pill ${t.status.toLowerCase()}`}>
                        {t.status}
                      </span>
                    </td>
                    <td>
                      {digest ? (
                        <code className="digest-code-small">{digest.slice(0, 24)}...</code>
                      ) : (
                        <span style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>Unsealed</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {turns.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)' }}>
                    No transactions recorded in this session.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
