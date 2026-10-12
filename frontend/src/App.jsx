import React, { useState, useEffect, useRef } from 'react';
import { 
  Sparkles, 
  Send, 
  Plus, 
  Download, 
  AlertTriangle, 
  CheckCircle, 
  Clock, 
  XCircle, 
  TrendingUp, 
  Database, 
  Layers, 
  HelpCircle,
  BarChart2,
  Table as TableIcon,
  RefreshCw,
  Sliders,
  Target
} from 'lucide-react';

export default function App() {
  const [sources, setSources] = useState([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [turns, setTurns] = useState([]);
  const [answers, setAnswers] = useState({}); // turn_id -> answer_data
  const [followUps, setFollowUps] = useState([]);
  const [questionText, setQuestionText] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isConnected, setIsConnected] = useState(true);
  
  const pollIntervals = useRef({});
  const chatBottomRef = useRef(null);

  // Initial Load: sources & conversation list
  useEffect(() => {
    fetchSources();
    fetchConversations();

    return () => {
      // Clear all active polls on unmount
      Object.values(pollIntervals.current).forEach(clearInterval);
    };
  }, []);

  // Fetch sources
  const fetchSources = async () => {
    try {
      const res = await fetch('/api/v1/sources');
      if (res.ok) {
        const data = await res.json();
        setSources(data);
        if (data.length > 0 && !selectedSource) {
          setSelectedSource(data[0].source_id);
        }
        setIsConnected(true);
      }
    } catch (err) {
      console.error('Failed to fetch sources:', err);
      setIsConnected(false);
    }
  };

  // Fetch conversation history
  const fetchConversations = async () => {
    try {
      const res = await fetch('/api/v1/conversations');
      if (res.ok) {
        const data = await res.json();
        setConversations(data);
        if (data.length > 0 && !currentConversationId) {
          selectConversation(data[0].conversation_id);
        }
      }
    } catch (err) {
      console.error('Failed to fetch conversations:', err);
    }
  };

  // Select a conversation and load its turns
  const selectConversation = async (convId) => {
    setCurrentConversationId(convId);
    try {
      const res = await fetch(`/api/v1/conversations/${convId}`);
      if (res.ok) {
        const data = await res.json();
        const loadedTurns = data.turns || [];
        setTurns(loadedTurns);
        
        // Load answers for answered turns
        loadedTurns.forEach(turn => {
          if (turn.status === 'ANSWERED' && !answers[turn.turn_id]) {
            fetchAnswer(turn.turn_id);
          } else if (['SUBMITTED', 'QUEUED', 'INTERPRETING', 'ANALYZING', 'COMPOSING'].includes(turn.status) || 
                     (turn.status === 'AWAITING_CLARIFICATION' && ['QUEUED', 'RUNNING'].includes(turn.job_status))) {
            startPolling(turn.turn_id, convId);
          }
        });

        // Load follow-up suggestions
        fetchFollowUps(convId);
      }
    } catch (err) {
      console.error('Failed to load conversation details:', err);
    }
  };

  // Create a new conversation
  const createNewConversation = async () => {
    try {
      const title = `Analytics ${new Date().toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`;
      const res = await fetch('/api/v1/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title })
      });
      if (res.ok) {
        const data = await res.json();
        await fetchConversations();
        selectConversation(data.conversation_id);
        return data.conversation_id;
      }
    } catch (err) {
      console.error('Failed to create new conversation:', err);
    }
    return null;
  };

  // Fetch published answer
  const fetchAnswer = async (turnId) => {
    try {
      const res = await fetch(`/api/v1/turns/${turnId}/answer`);
      if (res.ok) {
        const ans = await res.json();
        setAnswers(prev => ({ ...prev, [turnId]: ans }));
      }
    } catch (err) {
      console.error(`Failed to fetch answer for turn ${turnId}:`, err);
    }
  };

  // Fetch follow-ups
  const fetchFollowUps = async (convId) => {
    try {
      const res = await fetch(`/api/v1/conversations/${convId}/follow-ups`);
      if (res.ok) {
        const data = await res.json();
        setFollowUps(data.suggested_questions || []);
      }
    } catch {
      setFollowUps([]);
    }
  };

  // Polling helper
  const startPolling = (turnId, convId) => {
    if (pollIntervals.current[turnId]) {
      clearInterval(pollIntervals.current[turnId]);
    }

    pollIntervals.current[turnId] = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/turns/${turnId}`);
        if (!res.ok) return;
        const updatedTurn = await res.json();

        setTurns(prevTurns => prevTurns.map(t => t.turn_id === turnId ? updatedTurn : t));

        const isFinished = ['ANSWERED', 'DECLINED', 'FAILED', 'CANCELLED'].includes(updatedTurn.status) || 
          (updatedTurn.status === 'AWAITING_CLARIFICATION' && !['QUEUED', 'RUNNING'].includes(updatedTurn.job_status));

        if (isFinished) {
          clearInterval(pollIntervals.current[turnId]);
          delete pollIntervals.current[turnId];

          if (updatedTurn.status === 'ANSWERED') {
            fetchAnswer(turnId);
          }
          fetchFollowUps(convId);
        }
      } catch (err) {
        console.error(`Polling error for turn ${turnId}:`, err);
      }
    }, 1000);
  };

  // Submit new question
  const handleSubmitQuestion = async (customText = null) => {
    const text = (customText || questionText).trim();
    if (!text || !selectedSource) return;

    setIsSubmitting(true);
    let targetConvId = currentConversationId;

    if (!targetConvId) {
      targetConvId = await createNewConversation();
      if (!targetConvId) {
        setIsSubmitting(false);
        return;
      }
    }

    setQuestionText('');

    try {
      const res = await fetch(`/api/v1/conversations/${targetConvId}/questions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_id: selectedSource,
          question: text
        })
      });

      if (res.ok) {
        const data = await res.json();
        // Reload conversation state & poll
        await selectConversation(targetConvId);
        startPolling(data.turn_id, targetConvId);
      }
    } catch (err) {
      console.error('Failed to submit question:', err);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Submit clarification choice
  const handleSelectChoice = async (turnId, choiceId) => {
    try {
      const res = await fetch(`/api/v1/turns/${turnId}/clarification`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ choice_id: choiceId })
      });
      if (res.ok) {
        await selectConversation(currentConversationId);
        startPolling(turnId, currentConversationId);
      }
    } catch (err) {
      console.error('Failed to submit clarification choice:', err);
    }
  };

  // Cancel turn
  const handleCancelTurn = async (turnId) => {
    try {
      const res = await fetch(`/api/v1/turns/${turnId}/cancel`, { method: 'POST' });
      if (res.ok) {
        if (pollIntervals.current[turnId]) {
          clearInterval(pollIntervals.current[turnId]);
          delete pollIntervals.current[turnId];
        }
        selectConversation(currentConversationId);
      }
    } catch (err) {
      console.error('Failed to cancel turn:', err);
    }
  };

  // Render Charts
  const renderCharts = (charts) => {
    if (!charts || charts.length === 0) return null;

    return (
      <div className="charts-container">
        {charts.map((chart, idx) => {
          if (chart.type === 'bar' || chart.type === 'line') {
            const values = chart.values || [];
            const finiteVals = values.filter(v => Number.isFinite(v)).map(v => Math.abs(v));
            const maximum = Math.max(1, ...finiteVals);

            return (
              <div key={idx} className="chart-card">
                <div className="chart-title">
                  <BarChart2 size={16} color="var(--accent-blue)" />
                  <span>{chart.title}</span>
                </div>
                {chart.labels.map((label, lIdx) => {
                  const val = values[lIdx];
                  const isNull = val === null || val === undefined;
                  const pct = isNull ? 0 : Math.max(0, Math.min(100, (Math.abs(Number(val)) / maximum) * 100));
                  return (
                    <div key={lIdx} className="chart-bar-row">
                      <div className="chart-bar-header">
                        <span>{label}</span>
                        <span style={{ fontFamily: 'var(--font-mono)' }}>
                          {isNull ? 'Unavailable / suppressed' : `${val} ${chart.unit || ''}`}
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
            );
          }

          if (chart.type === 'forecast') {
            return (
              <div key={idx} className="chart-card">
                <div className="chart-title" style={{ color: '#60a5fa' }}>
                  <TrendingUp size={16} />
                  <span>{chart.title}</span>
                </div>
                <div className="science-grid">
                  {chart.labels.map((lbl, lIdx) => {
                    const obs = chart.observed_values ? chart.observed_values[lIdx] : null;
                    const fc = chart.forecast_values ? chart.forecast_values[lIdx] : null;
                    const isFc = fc !== null && fc !== undefined;
                    const val = isFc ? fc : obs;
                    const lower = chart.lower_bounds ? chart.lower_bounds[lIdx] : null;
                    const upper = chart.upper_bounds ? chart.upper_bounds[lIdx] : null;
                    const pi = isFc && lower !== null && upper !== null ? ` (PI: [${lower}, ${upper}])` : '';

                    return (
                      <div key={lIdx} className={`science-point ${isFc ? 'predicted' : 'observed'}`}>
                        <div style={{ fontWeight: 600 }}>{lbl}</div>
                        <div style={{ fontFamily: 'var(--font-mono)' }}>
                          {val} {chart.unit || ''}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: isFc ? '#60a5fa' : 'var(--text-dim)' }}>
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
              <div key={idx} className="chart-card">
                <div className="chart-title" style={{ color: '#f59e0b' }}>
                  <AlertTriangle size={16} />
                  <span>{chart.title} (Median Baseline: {chart.baseline_median} {chart.unit})</span>
                </div>
                <div className="science-grid">
                  {chart.labels.map((lbl, lIdx) => {
                    const isAnom = chart.is_anomaly ? chart.is_anomaly[lIdx] : false;
                    const val = chart.values ? chart.values[lIdx] : '';

                    return (
                      <div key={lIdx} className={`science-point ${isAnom ? 'anomaly-flag' : 'normal-flag'}`}>
                        <div style={{ fontWeight: 600 }}>{lbl}</div>
                        <div style={{ fontFamily: 'var(--font-mono)' }}>{val} {chart.unit || ''}</div>
                        <div style={{ fontSize: '0.7rem', color: isAnom ? '#f87171' : '#34d399', fontWeight: 600 }}>
                          {isAnom ? '⚠️ ANOMALY' : '✓ NORMAL'}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          }

          if (chart.type === 'scenario_bar') {
            return (
              <div key={idx} className="chart-card">
                <div className="chart-title" style={{ color: '#06b6d4' }}>
                  <Sliders size={16} />
                  <span>{chart.title}</span>
                </div>
                <div style={{ display: 'flex', gap: '2rem', flexWrap: 'wrap', marginTop: '0.5rem' }}>
                  {chart.labels.map((lbl, lIdx) => (
                    <div key={lIdx} style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '1.25rem', fontWeight: 700, fontFamily: 'var(--font-mono)', color: '#fff' }}>
                        {chart.values[lIdx]} {chart.unit || ''}
                      </div>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                        {lbl}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          }

          return null;
        })}
      </div>
    );
  };

  return (
    <div className="app-layout">
      {/* Header */}
      <header className="app-header">
        <div className="brand-section">
          <div className="brand-title">
            <Sparkles size={20} color="var(--accent-cyan)" />
            <span>Talk2Data</span>
          </div>
          <span className="brand-badge">Governed Decision Science</span>
          <div className="science-badges">
            <span className="badge-science forecast">FORECAST</span>
            <span className="badge-science anomaly">ANOMALY</span>
            <span className="badge-science scenario">SCENARIO</span>
            <span className="badge-science goalseek">GOAL_SEEK</span>
          </div>
        </div>

        <div className="header-status-area">
          <div className="tenant-pill">
            <span>Tenant:</span>
            <strong>tenant_a</strong>
            <span style={{ opacity: 0.3 }}>|</span>
            <span>User:</span>
            <strong>user_alice</strong>
          </div>

          <div className="conn-indicator">
            <div className="conn-dot"></div>
            <span>{isConnected ? 'ONLINE' : 'OFFLINE'}</span>
          </div>
        </div>
      </header>

      {/* Main Area */}
      <div className="main-workspace">
        {/* Sidebar */}
        <aside className="app-sidebar">
          <button className="btn-new-conv" onClick={createNewConversation}>
            <Plus size={16} />
            <span>New Conversation</span>
          </button>

          <div className="sidebar-title">Active Conversations</div>
          <ul className="conv-list" id="conv-list">
            {conversations.map(c => (
              <li
                key={c.conversation_id}
                className={`conv-item ${c.conversation_id === currentConversationId ? 'active' : ''}`}
                onClick={() => selectConversation(c.conversation_id)}
              >
                <span className="conv-item-title">{c.title}</span>
              </li>
            ))}
          </ul>
        </aside>

        {/* Chat / Content Area */}
        <main className="chat-workspace">
          <div className="chat-scroll-area" id="turns-container">
            {turns.length === 0 ? (
              <div className="welcome-hero">
                <div className="welcome-icon-box">
                  <Database size={30} />
                </div>
                <h2>Talk2Data Enterprise Analytics</h2>
                <p>
                  Ask natural language questions across verified data sources. All mathematical facts, joins, forecasts,
                  and scenarios are deterministically calculated and governed.
                </p>
                <div className="starter-chips">
                  <div className="starter-chip" onClick={() => handleSubmitQuestion("What was sales revenue in 2024?")}>
                    💰 What was revenue in 2024?
                  </div>
                  <div className="starter-chip" onClick={() => handleSubmitQuestion("Forecast annual revenue for the next two years.")}>
                    📈 Forecast annual revenue
                  </div>
                  <div className="starter-chip" onClick={() => handleSubmitQuestion("Show revenue by customer region in 2024.")}>
                    🗺️ Revenue by customer region
                  </div>
                  <div className="starter-chip" onClick={() => handleSubmitQuestion("Detect anomalies in revenue over time.")}>
                    🔍 Detect anomalies in revenue
                  </div>
                </div>
              </div>
            ) : (
              turns.map(turn => {
                const answer = answers[turn.turn_id];
                const isProcessing = ['SUBMITTED', 'QUEUED', 'INTERPRETING', 'ANALYZING', 'COMPOSING'].includes(turn.status);
                const isAwaitingClarification = turn.status === 'AWAITING_CLARIFICATION';

                return (
                  <div key={turn.turn_id} className="turn-card" id={`turn-${turn.turn_id}`}>
                    <div className="turn-header">
                      <div className="turn-question">{turn.question_text}</div>
                      <div className="turn-meta">
                        <span className={`status-pill status-${turn.status}`}>{turn.status}</span>
                        {(isProcessing || isAwaitingClarification) && (
                          <button className="btn-cancel-turn" onClick={() => handleCancelTurn(turn.turn_id)}>
                            Cancel
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Status Body */}
                    {isProcessing && (
                      <div className="processing-indicator">
                        <div className="spinner"></div>
                        <span>Processing analysis & compiling verified query...</span>
                      </div>
                    )}

                    {/* Clarification prompt */}
                    {isAwaitingClarification && turn.clarification_request && (
                      <div className="clarification-box">
                        <div className="clarification-prompt">
                          <HelpCircle size={18} color="#f59e0b" />
                          <span>
                            {turn.clarification_request.question_prompt || 
                             turn.clarification_request.prompt || 
                             'Please clarify your question:'}
                          </span>
                        </div>
                        <div className="clarification-options" id={`clar-options-${turn.turn_id}`}>
                          {(turn.clarification_request.options || []).map(opt => (
                            <button
                              key={opt.choice_id}
                              className="choice-btn"
                              id={`choice-${opt.choice_id}`}
                              onClick={() => handleSelectChoice(turn.turn_id, opt.choice_id)}
                            >
                              <strong>{opt.label}</strong>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Declined or Failed message */}
                    {(turn.status === 'DECLINED' || turn.status === 'FAILED') && (
                      <div style={{ color: 'var(--accent-rose)', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <XCircle size={18} />
                        <span>{turn.decline_reason || turn.error_message || 'The request could not be fulfilled.'}</span>
                      </div>
                    )}

                    {/* Cancelled message */}
                    {turn.status === 'CANCELLED' && (
                      <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <Clock size={16} />
                        <span>Analysis turn was cancelled by user.</span>
                      </div>
                    )}

                    {/* Answer View */}
                    {turn.status === 'ANSWERED' && answer && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        {answer.summary_text && (
                          <p className="answer-summary">{answer.summary_text}</p>
                        )}

                        {/* KPI Cards */}
                        {answer.kpi_cards && answer.kpi_cards.length > 0 && (
                          <div className="kpi-grid">
                            {answer.kpi_cards.map((kpi, kIdx) => (
                              <div key={kIdx} className="kpi-card">
                                <div className="kpi-title">{kpi.title}</div>
                                <div className="kpi-value">
                                  {kpi.value} {kpi.unit || ''}
                                </div>
                                {kpi.delta && <div className="kpi-delta">{kpi.delta}</div>}
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Table */}
                        {answer.table_rows && answer.table_rows.length > 0 && (
                          <div className="data-table-container">
                            <table>
                              <thead>
                                <tr>
                                  {(answer.table_columns || Object.keys(answer.table_rows[0])).map((col, cIdx) => (
                                    <th key={cIdx}>{col}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {answer.table_rows.map((row, rIdx) => {
                                  const cols = answer.table_columns || Object.keys(row);
                                  return (
                                    <tr key={rIdx}>
                                      {cols.map((col, cIdx) => (
                                        <td key={cIdx}>{String(row[col] ?? '')}</td>
                                      ))}
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        )}

                        {/* Charts */}
                        {renderCharts(answer.charts)}

                        {/* Assumptions and Limitations */}
                        {((answer.assumptions && answer.assumptions.length > 0) ||
                          (answer.limitations && answer.limitations.length > 0)) && (
                          <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)', display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                            {(answer.assumptions || []).map((a, aIdx) => (
                              <div key={aIdx}><strong>Assumption:</strong> {a}</div>
                            ))}
                            {(answer.limitations || []).map((l, lIdx) => (
                              <div key={lIdx}><strong>Limitation:</strong> {l}</div>
                            ))}
                          </div>
                        )}

                        {/* Export bar */}
                        <div className="turn-actions-bar">
                          <div className="export-group">
                            <a
                              className="btn-export"
                              href={`/api/v1/turns/${turn.turn_id}/export/xlsx`}
                              download
                            >
                              <Download size={14} />
                              <span>Export XLSX</span>
                            </a>
                            <a
                              className="btn-export"
                              href={`/api/v1/turns/${turn.turn_id}/export/csv`}
                              download
                            >
                              <Download size={14} />
                              <span>Export CSV</span>
                            </a>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}

            {/* Follow-up chips */}
            {followUps.length > 0 && (
              <div className="followups-container">
                <div className="followups-title">Suggested Follow-ups</div>
                <div className="followup-chips">
                  {followUps.map((q, qIdx) => (
                    <div
                      key={qIdx}
                      className="followup-chip"
                      onClick={() => handleSubmitQuestion(q)}
                    >
                      💡 {q}
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div ref={chatBottomRef} />
          </div>

          {/* Footer Input Bar */}
          <footer className="query-footer">
            <div className="query-input-row">
              <select
                id="source-select"
                className="source-select"
                value={selectedSource}
                onChange={e => setSelectedSource(e.target.value)}
                disabled={sources.length === 0}
              >
                {sources.map(s => (
                  <option key={s.source_id} value={s.source_id}>
                    {s.source_id}
                  </option>
                ))}
              </select>

              <div className="query-input-wrap">
                <input
                  id="question-input"
                  className="query-input"
                  placeholder="Ask a business data question (e.g. 'What was sales revenue by country in 2024?')"
                  value={questionText}
                  onChange={e => setQuestionText(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSubmitQuestion();
                    }
                  }}
                  disabled={isSubmitting || sources.length === 0}
                />
              </div>

              <button
                id="btn-submit"
                className="btn-submit"
                onClick={() => handleSubmitQuestion()}
                disabled={isSubmitting || !questionText.trim() || sources.length === 0}
              >
                <Send size={16} />
                <span>Submit</span>
              </button>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}
