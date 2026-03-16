import React, { useEffect, useState } from 'react';

export default function SkillLibrary({ userId = 'user', onClose }) {
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(`/skills/${userId}`)
      .then(r => r.json())
      .then(d => { setSkills(d.skills || []); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [userId]);

  const deleteSkill = async (skillId) => {
    try {
      await fetch(`/skills/${userId}/${skillId}`, { method: 'DELETE' });
      setSkills(prev => prev.filter(s => s.id !== skillId));
    } catch (e) {
      console.error('Delete skill error:', e);
    }
  };

  return (
    <div className="skill-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="skill-drawer">
        {/* Header */}
        <div style={{ padding: '20px 24px 14px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexShrink: 0 }}>
          <div>
            <h2 className="ef" style={{ fontSize: 24, fontWeight: 300 }}>Skill Library</h2>
            <p className="label" style={{ marginTop: 4 }}>
              {skills.length} learned skill{skills.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button className="icon-btn" onClick={onClose}>Close</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading && (
            <div style={{ padding: 32, textAlign: 'center' }}>
              <span className="label" style={{ animation: 'blink 1.2s infinite' }}>Loading…</span>
            </div>
          )}
          {error && (
            <div style={{ padding: 24 }}>
              <span className="label" style={{ color: 'var(--error)' }}>Error: {error}</span>
            </div>
          )}
          {!loading && !error && skills.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center' }}>
              <span className="ef" style={{ fontSize: 36, fontWeight: 300, color: 'var(--text-3)', fontStyle: 'italic', display: 'block', marginBottom: 12 }}>—</span>
              <span className="label" style={{ lineHeight: 1.8 }}>
                No skills yet.<br />
                Complete goals to start learning.
              </span>
            </div>
          )}

          {skills.map((skill, i) => (
            <div
              key={skill.id || i}
              className="skill-item fu"
              style={{ animationDelay: `${i * 0.04}s` }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 5 }}>
                    <span className="ef" style={{ fontSize: 16, fontWeight: 400 }}>
                      {skill.name || 'Unnamed Skill'}
                    </span>
                    {skill.category && (
                      <span className="mf" style={{ fontSize: 8, letterSpacing: '0.12em', textTransform: 'uppercase', color: 'var(--accent)', border: '1px solid var(--accent-dim)', padding: '2px 7px' }}>
                        {skill.category}
                      </span>
                    )}
                  </div>
                  <p style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.55, marginBottom: 8 }}>
                    {skill.description || skill.content || '—'}
                  </p>
                  {skill.trigger && (
                    <div style={{ marginTop: 4 }}>
                      <span className="label" style={{ marginRight: 6 }}>Trigger:</span>
                      <span className="mf" style={{ fontSize: 9, color: 'var(--text-2)' }}>{skill.trigger}</span>
                    </div>
                  )}
                  <div style={{ marginTop: 6, display: 'flex', gap: 16 }}>
                    {skill.use_count != null && (
                      <span className="mf" style={{ fontSize: 8, color: 'var(--text-3)', letterSpacing: '0.1em' }}>
                        used {skill.use_count}×
                      </span>
                    )}
                    {skill.created_at && (
                      <span className="mf" style={{ fontSize: 8, color: 'var(--text-3)' }}>
                        {new Date(skill.created_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  className="skill-delete-btn"
                  onClick={() => deleteSkill(skill.id)}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Footer quote */}
        <div style={{ padding: '16px 24px', borderTop: '1px solid var(--border)', flexShrink: 0 }}>
          <p className="ef" style={{ fontSize: 13, fontStyle: 'italic', fontWeight: 300, color: 'var(--text-3)', lineHeight: 1.6 }}>
            "Skills are extracted automatically after each completed goal."
          </p>
        </div>
      </div>
    </div>
  );
}
