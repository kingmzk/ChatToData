import React from 'react';
import { ArrowUpRight } from 'lucide-react';

function formatKpiValue(val) {
  if (val === undefined || val === null) return '0';
  if (typeof val === 'number') return val.toLocaleString();
  const rawStr = String(val).trim();
  const num = Number(rawStr.replace(/,/g, ''));
  if (isNaN(num)) return rawStr;
  return num.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

export default function KPIGrid({ kpis }) {
  if (!kpis || kpis.length === 0) return null;

  return (
    <div className="kpi-grid">
      {kpis.map((kpi, idx) => (
        <div key={idx} className="kpi-card">
          <div className="kpi-header">
            <span className="kpi-title">{kpi.title}</span>
            {kpi.change !== undefined && (
              <span className={`kpi-badge ${Number(String(kpi.change).replace(/,/g, '')) >= 0 ? 'positive' : 'negative'}`}>
                <ArrowUpRight size={13} />
                {Number(String(kpi.change).replace(/,/g, '')) > 0 ? `+${kpi.change}` : kpi.change}%
              </span>
            )}
          </div>
          <div className="kpi-value">
            {kpi.is_suppressed ? (
              <span className="suppression-tag">Suppressed</span>
            ) : (
              `${formatKpiValue(kpi.value)} ${kpi.unit || ''}`
            )}
          </div>
          {kpi.subtitle && <div className="kpi-subtext">{kpi.subtitle}</div>}
        </div>
      ))}
    </div>
  );
}
