import React from 'react';
import { ArrowUpRight } from 'lucide-react';

export default function KPIGrid({ kpis }) {
  if (!kpis || kpis.length === 0) return null;

  return (
    <div className="kpi-grid">
      {kpis.map((kpi, idx) => (
        <div key={idx} className="kpi-card">
          <div className="kpi-header">
            <span className="kpi-title">{kpi.title}</span>
            {kpi.change !== undefined && (
              <span className={`kpi-badge ${Number(kpi.change) >= 0 ? 'positive' : 'negative'}`}>
                <ArrowUpRight size={13} />
                {Number(kpi.change) > 0 ? `+${kpi.change}` : kpi.change}%
              </span>
            )}
          </div>
          <div className="kpi-value">
            {kpi.is_suppressed ? (
              <span className="suppression-tag">🔒 Suppressed</span>
            ) : (
              `${Number(kpi.value).toLocaleString()} ${kpi.unit || ''}`
            )}
          </div>
          {kpi.subtitle && <div className="kpi-subtext">{kpi.subtitle}</div>}
        </div>
      ))}
    </div>
  );
}
