import React from 'react';

const STATUS_LABEL = {
  disconnected: 'Disconnected',
  connecting:   'Connecting…',
  ready:        'Ready',
  thinking:     'Thinking',
  executing:    'Executing',
  done:         'Complete',
  error:        'Error',
};

export default function Header({ connected, status, onSkills, onStop, sessionId }) {
  const dotClass = !connected
    ? 'status-dot'
    : status === 'error'
    ? 'status-dot error'
    : status === 'thinking' || status === 'executing'
    ? 'status-dot thinking'
    : 'status-dot connected';

  return (
    <header className="app-header fu">
      {/* Brand */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span className="ef" style={{ fontSize: 22, fontWeight: 300, letterSpacing: '0.02em', color: 'var(--text)' }}>
          cortex
        </span>
        <span className="ef" style={{ fontSize: 22, fontWeight: 600, letterSpacing: '0.01em', color: 'var(--accent)' }}>
          41
        </span>
        <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)', letterSpacing: '0.22em', textTransform: 'uppercase', marginLeft: 4 }}>
          Desktop Agent
        </span>
      </div>

      {/* Status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className={dotClass} />
        <span className="mf" style={{ fontSize: 10, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--text-2)' }}>
          {connected ? STATUS_LABEL[status] || status : 'Disconnected'}
        </span>
        {connected && (
          <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)', letterSpacing: '0.08em', marginLeft: 4 }}>
            · {sessionId}
          </span>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="icon-btn" onClick={onSkills}>
          Skills
        </button>
        {(status === 'thinking' || status === 'executing') && (
          <button className="icon-btn danger" onClick={onStop}>
            Stop
          </button>
        )}
      </div>
    </header>
  );
}
