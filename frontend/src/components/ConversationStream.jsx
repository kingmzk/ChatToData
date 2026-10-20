import React, { useEffect, useRef } from 'react';
import { 
  Terminal,
  CheckCircle, 
  Clock, 
  XCircle, 
  AlertTriangle, 
  ChevronRight, 
  Compass,
  ArrowDown
} from 'lucide-react';
import ClarificationCard from './ClarificationCard';

export default function ConversationStream({ 
  turns = [], 
  answers = {}, 
  activeTurnId, 
  onSelectChoice, 
  onCancelTurn, 
  onOpenInStudio 
}) {
  const streamBottomRef = useRef(null);

  // Automatically scroll down to the latest message in the conversation stream
  useEffect(() => {
    if (streamBottomRef.current) {
      streamBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [turns.length, activeTurnId]);

  const scrollToLatest = () => {
    streamBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div id="turns-container" className="turns-timeline">
      {/* Stream Header */}
      <div className="stream-header-strip">
        <div className="stream-count-info">
          <span>Conversation History ({turns.length} {turns.length === 1 ? 'Turn' : 'Turns'}) — Chronological Order</span>
        </div>
        {turns.length > 2 && (
          <button 
            onClick={scrollToLatest} 
            className="btn-toggle-sort"
            title="Scroll to latest turn at the bottom"
          >
            <ArrowDown size={13} />
            <span>Scroll to Latest</span>
          </button>
        )}
      </div>

      {/* Logical Chronological Order: Turn 1 -> Turn 2 -> ... -> Latest at the bottom */}
      {turns.map((turn, index) => {
        const isAnswering = ['SUBMITTED', 'INTERPRETING', 'ANALYZING', 'COMPOSING'].includes(turn.status);
        const isClarif = turn.status === 'AWAITING_CLARIFICATION';
        const isAnswered = turn.status === 'ANSWERED';
        const isFailed = turn.status === 'FAILED';
        const isDeclined = turn.status === 'DECLINED';
        const isCancelled = turn.status === 'CANCELLED';
        const isActive = activeTurnId === turn.turn_id;
        const isLatest = index === turns.length - 1;

        return (
          <div 
            key={turn.turn_id} 
            className={`turn-card ${isActive ? 'focused-turn' : ''} ${isLatest ? 'latest-turn' : ''}`}
          >
            <div className="turn-header">
              <div className="turn-meta">
                <span className="turn-number">Turn #{turn.turn_id.slice(-6)}</span>
                <span className="source-tag">{turn.source_id}</span>
                {turn.proposed_family && (
                  <span className="badge-science">{turn.proposed_family}</span>
                )}
                {isLatest && (
                  <span className="badge-latest">Latest</span>
                )}
                <span className={`status-pill ${turn.status.toLowerCase()}`}>
                  {isAnswering ? <Clock size={12} className="spin-icon" /> : null}
                  {isAnswered ? <CheckCircle size={12} /> : null}
                  {isClarif ? <AlertTriangle size={12} /> : null}
                  {(isFailed || isDeclined || isCancelled) ? <XCircle size={12} /> : null}
                  <span>{turn.status}</span>
                </span>
              </div>
              <div className="turn-actions-strip">
                {isAnswering && (
                  <button 
                    onClick={() => onCancelTurn(turn.turn_id)} 
                    className="btn-cancel-turn"
                    title="Cancel in-flight analytical execution"
                  >
                    Cancel
                  </button>
                )}
                <button 
                  onClick={() => onOpenInStudio(turn.turn_id)} 
                  className="btn-studio-inspect"
                  title="Open in Studio Dashboard"
                >
                  <Compass size={13} />
                  <span>Inspect in Studio</span>
                </button>
              </div>
            </div>

            <div className="turn-question-text">
              <h3>"{turn.question_text}"</h3>
            </div>

            {/* In-Flight Spinner */}
            {isAnswering && (
              <div className="turn-status-card processing">
                <Clock size={16} className="spin-icon" color="var(--accent-blue)" />
                <span>Processing analysis... executing deterministic pipeline</span>
              </div>
            )}

            {/* Clarification Options */}
            {isClarif && (
              <ClarificationCard turn={turn} onSelectChoice={onSelectChoice} />
            )}

            {/* Error or Decline message */}
            {(isFailed || isDeclined) && (
              <div className="turn-status-card error">
                <XCircle size={16} color="var(--accent-rose)" />
                <span>
                  {turn.decline_reason || turn.error_message || (isDeclined ? 'Question declined: metric or scope unsupported by approved catalog.' : 'Analytical pipeline failed.')}
                </span>
              </div>
            )}

            {/* Answer Indicator */}
            {isAnswered && (
              <div className="turn-answered-block">
                <div className="turn-answered-preview" onClick={() => onOpenInStudio(turn.turn_id)}>
                  <CheckCircle size={15} color="var(--accent-emerald)" />
                  <span>Analysis Complete - Click "Inspect in Studio" to view full KPIs, charts, and data tables.</span>
                  <ChevronRight size={14} className="ml-auto" />
                </div>
                {answers && answers[turn.turn_id] && answers[turn.turn_id].sql_query && (
                  <details className="stream-sql-details">
                    <summary className="stream-sql-summary">
                      <Terminal size={12} color="var(--accent-cyan)" />
                      <span>Show Generated SQL Query (POC)</span>
                    </summary>
                    <pre className="stream-sql-code"><code>{answers[turn.turn_id].sql_query}</code></pre>
                  </details>
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Auto-scroll target anchor at bottom */}
      <div ref={streamBottomRef} style={{ height: '1px' }} />

      {turns.length === 0 && (
        <div className="stream-empty">
          <p>No questions submitted in this conversation yet. Enter an analytical question above.</p>
        </div>
      )}
    </div>
  );
}
