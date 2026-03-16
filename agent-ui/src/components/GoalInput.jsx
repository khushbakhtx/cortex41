import React, { useState, useRef } from 'react';

const MicIcon = ({ recording, size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={recording ? '#000' : 'currentColor'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="2" width="6" height="12" rx="3" />
    <path d="M19 10a7 7 0 0 1-14 0" />
    <line x1="12" y1="19" x2="12" y2="22" />
    <line x1="8"  y1="22" x2="16" y2="22" />
  </svg>
);

export default function GoalInput({ onGoal, onStop, status, isRecording, onVoiceToggle, connected }) {
  const [text, setText] = useState('');
  const inputRef = useRef(null);
  const isRunning = status === 'thinking' || status === 'executing';

  const handleSubmit = () => {
    const goal = text.trim();
    if (!goal || !connected || isRunning) return;
    onGoal(goal);
    setText('');
  };

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="goal-bar">
      <input
        ref={inputRef}
        className="goal-input"
        type="text"
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={handleKey}
        disabled={!connected || isRunning}
        placeholder={
          !connected
            ? 'Connecting to agent…'
            : isRunning
            ? 'Agent is working…'
            : 'Type a goal or speak a command…'
        }
      />

      {/* Voice button */}
      <button
        className={`voice-btn${isRecording ? ' recording' : ''}`}
        onClick={onVoiceToggle}
        disabled={!connected}
        title={isRecording ? 'Stop recording' : 'Start voice command'}
      >
        <MicIcon recording={isRecording} />
      </button>

      {/* Submit / Stop */}
      {isRunning ? (
        <button className="submit-btn" style={{ background: 'var(--error)', color: '#fff' }} onClick={onStop}>
          Stop
        </button>
      ) : (
        <button
          className="submit-btn"
          onClick={handleSubmit}
          disabled={!connected || !text.trim()}
        >
          Execute →
        </button>
      )}
    </div>
  );
}
