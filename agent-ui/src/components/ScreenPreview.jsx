import React, { useState, useEffect } from 'react';

export default function ScreenPreview({ screenshot, lastAction, status }) {
  const [imgKey, setImgKey] = useState(0);

  // Force re-animation on each new screenshot
  useEffect(() => {
    if (screenshot) setImgKey(k => k + 1);
  }, [screenshot]);

  const isThinking = status === 'thinking' || status === 'executing';

  return (
    <div className="screen-wrap">
      {/* Thinking overlay shimmer at top */}
      {isThinking && (
        <div
          className="thinking-shimmer"
          style={{
            position: 'absolute',
            top: 0, left: 0, right: 0,
            height: 2,
            zIndex: 2,
          }}
        />
      )}

      {screenshot ? (
        <>
          <img
            key={imgKey}
            className="screen-img"
            src={`data:image/jpeg;base64,${screenshot}`}
            alt="Live agent screen"
          />

          {/* Corner label */}
          <div
            style={{
              position: 'absolute',
              top: 10, right: 10,
              background: 'rgba(10,10,10,0.55)',
              backdropFilter: 'blur(4px)',
              padding: '4px 9px',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            {isThinking && (
              <span
                style={{
                  width: 5, height: 5,
                  borderRadius: '50%',
                  background: '#fff',
                  display: 'inline-block',
                  animation: 'blink 1s ease-in-out infinite',
                }}
              />
            )}
            <span className="mf" style={{ fontSize: 8, color: 'rgba(255,255,255,0.7)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
              Live
            </span>
          </div>
        </>
      ) : (
        <div className="screen-empty">
          <span className="ef" style={{ fontSize: 64, fontWeight: 300, color: 'var(--text-3)', fontStyle: 'italic' }}>
            41
          </span>
          <span className="label" style={{ textAlign: 'center', lineHeight: 1.8 }}>
            Screen preview<br />will appear here
          </span>
          {status === 'connecting' && (
            <span className="label" style={{ animation: 'blink 1.2s ease-in-out infinite' }}>
              Connecting to agent…
            </span>
          )}
        </div>
      )}
    </div>
  );
}
