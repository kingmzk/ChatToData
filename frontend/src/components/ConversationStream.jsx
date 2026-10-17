import React from 'react';
import { 
  Sparkles, 
  CheckCircle, 
  Clock, 
  XCircle, 
  AlertTriangle, 
  ChevronRight, 
  FileText,
  Compass
} from 'lucide-react';
import ClarificationCard from './ClarificationCard';

export default function ConversationStream({ 
  turns, 
  activeTurnId, 
  onSelectTurn, 
  onSelectChoice, 
  onCancelTurn, 
  onOpenInStudio 
}) {
  return (
    <div id="turns-container" className="turns-timeline">
      {turns.map((turn) => {
        const isAnswering = ['SUBMITTED', 'INTERPRETING', 'ANALYZING', 'COMPOSING'].includes(turn.status);
        const isClarif = turn.status === 'AWAITING_CLARIFICATION';
        const isAnswered = turn.status === 'ANSWERED';
        const isFailed = turn.status === 'FAILED';
        const isDeclined = turn.status === 'DECLINED';
        const isCancelled = turn.status === 'CANCELLED';
        const isActive = activeTurnId === turn.turn_id;

        return (
          <div key={turn.turn_id} className={`turn-card ${isActive ? 'focused-turn' : ''}`}>
            <div className="turn-header">
              <div className="turn-meta">
                <span className="turn-number">Turn #{turn.turn_id.slice(-6)}</span>
                <span className="source-tag">{turn.source_id}</span>
                {turn.proposed_family && (
                  <span className="badge-science">{turn.proposed_family}</span>
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
                  {turn.error_message || (isDeclined ? 'Question declined: metric or scope unsupported by approved catalog.' : 'Analytical pipeline failed.')}
                </span>
              </div>
            )}

            {/* Answer Indicator */}
            {isAnswered && (
              <div className="turn-answered-preview" onClick={() => onOpenInStudio(turn.turn_id)}>
                <CheckCircle size={15} color="var(--accent-emerald)" />
                <span>Analysis Complete — Click "Inspect in Studio" to view full KPIs, charts, and data tables.</span>
                <ChevronRight size={14} className="ml-auto" />
              </div>
            )}
          </div>
        );
      })}

      {turns.length === 0 && (
        <div className="stream-empty">
          <p>No questions submitted in this conversation yet. Type your analytical question below.</p>
        </div>
      )}
    </div>
  );
}
