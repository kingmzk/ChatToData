import React from 'react';
import { BarChart2, TrendingUp, AlertTriangle, Target, Sliders } from 'lucide-react';

export default function AnalyticsChart({ charts }) {
  if (!charts || charts.length === 0) return null;

  return (
    <div className="charts-container">
      {charts.map((chart, idx) => {
        if (chart.type === 'bar' || chart.type === 'comparison') {
          const values = chart.values || [];
          const numValues = values.filter(v => v !== null && v !== undefined).map(Number);
          const maximum = Math.max(...numValues, 1);

          return (
            <div key={idx} className="chart-card">
              <div className="chart-title">
                <BarChart2 size={18} color="var(--accent-blue)" />
                <span>{chart.title}</span>
                {chart.unit && <span className="chart-unit-badge">{chart.unit}</span>}
              </div>
              <div className="chart-bars-list">
                {chart.labels.map((label, lIdx) => {
                  const val = values[lIdx];
                  const isNull = val === null || val === undefined;
                  const pct = isNull ? 0 : Math.max(0, Math.min(100, (Math.abs(Number(val)) / maximum) * 100));
                  return (
                    <div key={lIdx} className="chart-bar-row">
                      <div className="chart-bar-header">
                        <span className="chart-bar-label">{label}</span>
                        <span className="chart-bar-value" style={{ fontFamily: 'var(--font-mono)' }}>
                          {isNull ? (
                            <span className="suppression-tag">🔒 Suppressed</span>
                          ) : (
                            `${Number(val).toLocaleString()} ${chart.unit || ''}`
                          )}
                        </span>
                      </div>
                      {!isNull && (
                        <div className="chart-bar-track">
                          <div className="chart-bar-fill" style={{ width: `${pct}%` }}></div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        }

        if (chart.type === 'forecast') {
          return (
            <div key={idx} className="chart-card forecast-chart">
              <div className="chart-title" style={{ color: '#60a5fa' }}>
                <TrendingUp size={18} />
                <span>{chart.title}</span>
                <span className="science-indicator-badge">Decision Science Forecast</span>
              </div>
              <div className="science-grid">
                {chart.labels.map((lbl, lIdx) => {
                  const obs = chart.observed_values ? chart.observed_values[lIdx] : null;
                  const fc = chart.forecast_values ? chart.forecast_values[lIdx] : null;
                  const isFc = fc !== null && fc !== undefined;
                  const val = isFc ? fc : obs;
                  const lower = chart.lower_bounds ? chart.lower_bounds[lIdx] : null;
                  const upper = chart.upper_bounds ? chart.upper_bounds[lIdx] : null;
                  const pi = isFc && lower !== null && upper !== null ? ` [${lower} – ${upper}]` : '';

                  return (
                    <div key={lIdx} className={`science-point ${isFc ? 'predicted' : 'observed'}`}>
                      <div className="science-point-label">{lbl}</div>
                      <div className="science-point-val" style={{ fontFamily: 'var(--font-mono)' }}>
                        {val !== null ? `${Number(val).toLocaleString()} ${chart.unit || ''}` : 'N/A'}
                      </div>
                      <div className="science-point-status" style={{ fontSize: '0.72rem', color: isFc ? '#60a5fa' : 'var(--text-dim)' }}>
                        {isFc ? `PREDICTED${pi}` : 'OBSERVED'}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        }

        if (chart.type === 'anomaly') {
          return (
            <div key={idx} className="chart-card anomaly-chart">
              <div className="chart-title" style={{ color: '#f59e0b' }}>
                <AlertTriangle size={18} />
                <span>{chart.title}</span>
                <span className="science-indicator-badge warning">MAD Outlier Analysis</span>
              </div>
              <div className="science-grid">
                {chart.labels.map((lbl, lIdx) => {
                  const val = chart.values[lIdx];
                  const isAnom = chart.anomalies ? chart.anomalies[lIdx] : false;
                  const dev = chart.deviations ? chart.deviations[lIdx] : null;

                  return (
                    <div key={lIdx} className={`science-point ${isAnom ? 'anomaly-detected' : 'normal'}`}>
                      <div className="science-point-label">{lbl}</div>
                      <div className="science-point-val" style={{ fontFamily: 'var(--font-mono)' }}>
                        {val !== null ? `${Number(val).toLocaleString()} ${chart.unit || ''}` : 'N/A'}
                      </div>
                      <div className="science-point-status" style={{ fontSize: '0.72rem', color: isAnom ? '#ef4444' : '#10b981' }}>
                        {isAnom ? `⚠️ ANOMALOUS (dev: ${dev})` : 'NORMAL'}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        }

        if (chart.type === 'scenario' || chart.type === 'goal_seek') {
          const isGoal = chart.type === 'goal_seek';
          return (
            <div key={idx} className="chart-card scenario-chart">
              <div className="chart-title" style={{ color: isGoal ? '#10b981' : '#a855f7' }}>
                {isGoal ? <Target size={18} /> : <Sliders size={18} />}
                <span>{chart.title}</span>
                <span className={`science-indicator-badge ${isGoal ? 'success' : 'purple'}`}>
                  {isGoal ? 'Bisection Solver' : 'Scenario Simulation'}
                </span>
              </div>
              <div className="scenario-comparison-view">
                <div className="scenario-val-box">
                  <div className="scenario-val-label">{chart.baseline_label || 'Baseline'}</div>
                  <div className="scenario-val-num" style={{ fontFamily: 'var(--font-mono)' }}>
                    {chart.baseline_value !== undefined ? `${Number(chart.baseline_value).toLocaleString()} ${chart.unit || ''}` : 'N/A'}
                  </div>
                </div>
                <div className="scenario-divider">➔</div>
                <div className="scenario-val-box highlighted">
                  <div className="scenario-val-label">{chart.simulated_label || (isGoal ? 'Solution Target' : 'Simulated')}</div>
                  <div className="scenario-val-num" style={{ fontFamily: 'var(--font-mono)', color: isGoal ? '#10b981' : '#c084fc' }}>
                    {chart.simulated_value !== undefined ? `${Number(chart.simulated_value).toLocaleString()} ${chart.unit || ''}` : 'N/A'}
                  </div>
                </div>
              </div>
            </div>
          );
        }

        return null;
      })}
    </div>
  );
}
