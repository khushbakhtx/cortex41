import React, { useReducer, useEffect, useRef, useCallback } from 'react';
import Header       from './components/Header.jsx';
import PlanPanel    from './components/PlanPanel.jsx';
import ScreenPreview from './components/ScreenPreview.jsx';
import AgentFeed    from './components/AgentFeed.jsx';
import GoalInput    from './components/GoalInput.jsx';
import StatsPanel   from './components/StatsPanel.jsx';
import SkillLibrary from './components/SkillLibrary.jsx';

/* ─── State ─────────────────────────────────────────────── */

const INITIAL = {
  connected:       false,
  status:          'disconnected',  // disconnected | connecting | ready | thinking | executing | done | error
  plan:            null,
  activeSubTaskId: null,
  events:          [],
  screenshot:      null,
  lastAction:      null,
  stats:           null,
  isRecording:     false,
  showSkills:      false,
};

function reducer(state, action) {
  switch (action.type) {
    case 'WS_OPEN':
      return { ...state, connected: true, status: 'connecting' };
    case 'WS_CLOSE':
      return { ...state, connected: false, status: 'disconnected' };
    case 'SET_STATUS':
      return { ...state, status: action.payload };
    case 'SET_PLAN': {
      // Attach 'pending' status to each task if missing
      const sub_tasks = (action.payload.sub_tasks || []).map(t => ({
        status: 'pending', ...t,
      }));
      return { ...state, plan: { ...action.payload, sub_tasks }, activeSubTaskId: null };
    }
    case 'SET_ACTIVE_SUBTASK': {
      if (!state.plan) return state;
      const sub_tasks = state.plan.sub_tasks.map(t =>
        t.id === action.payload
          ? { ...t, status: 'in_progress' }
          : t.status === 'in_progress'
          ? { ...t, status: 'pending' }
          : t
      );
      return { ...state, plan: { ...state.plan, sub_tasks }, activeSubTaskId: action.payload };
    }
    case 'COMPLETE_SUBTASK': {
      if (!state.plan) return state;
      const sub_tasks = state.plan.sub_tasks.map(t =>
        t.id === action.payload ? { ...t, status: 'done' } : t
      );
      return { ...state, plan: { ...state.plan, sub_tasks } };
    }
    case 'ADD_EVENT':
      return { ...state, events: [...state.events, action.payload].slice(-300) };
    case 'SET_SCREENSHOT':
      return { ...state, screenshot: action.payload };
    case 'SET_LAST_ACTION':
      return { ...state, lastAction: action.payload };
    case 'SET_STATS':
      return { ...state, stats: action.payload };
    case 'SET_RECORDING':
      return { ...state, isRecording: action.payload };
    case 'TOGGLE_SKILLS':
      return { ...state, showSkills: !state.showSkills };
    case 'RESET_RUN':
      return { ...state, status: 'ready', plan: null, activeSubTaskId: null, screenshot: null };
    default:
      return state;
  }
}

/* ─── Audio helpers ──────────────────────────────────────── */

// Convert Float32 PCM → Int16 → base64
function float32ToBase64(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    int16[i] = Math.max(-32768, Math.min(32767, Math.round(float32Array[i] * 32767)));
  }
  const bytes = new Uint8Array(int16.buffer);
  let binary  = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

// Play base64 Int16 PCM audio (Gemini Live outputs at 24kHz)
const PLAYBACK_SAMPLE_RATE = 24000;
let playbackCtx = null;
let nextPlayTime = 0;

function playAudioChunk(b64) {
  if (!b64) return;
  try {
    if (!playbackCtx) {
      playbackCtx = new AudioContext({ sampleRate: PLAYBACK_SAMPLE_RATE });
    }
    const ctx = playbackCtx;

    const binary = atob(b64);
    const bytes  = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

    const int16  = new Int16Array(bytes.buffer);
    const buffer = ctx.createBuffer(1, int16.length, PLAYBACK_SAMPLE_RATE);
    const ch     = buffer.getChannelData(0);
    for (let i = 0; i < int16.length; i++) ch[i] = int16[i] / 32768;

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    const now   = ctx.currentTime;
    const start = Math.max(now, nextPlayTime);
    source.start(start);
    nextPlayTime = start + buffer.duration;
  } catch (e) {
    // Audio playback failure is non-fatal
    console.warn('[Audio playback]', e.message);
  }
}

/* ─── Component ─────────────────────────────────────────── */

const SESSION_ID = `s-${Date.now()}`;
const WS_URL     = `ws://localhost:8000/ws/${SESSION_ID}`;
const RECONNECT_DELAY = 3500;

export default function App() {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const wsRef             = useRef(null);
  const reconnectTimer    = useRef(null);

  // Voice recording refs
  const audioCtxRef   = useRef(null);
  const processorRef  = useRef(null);
  const streamRef     = useRef(null);

  /* ── WebSocket ──────────────────────────────────────────── */

  const send = useCallback((payload) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }, []);

  const addEvent = useCallback((msg) => {
    dispatch({
      type: 'ADD_EVENT',
      payload: { id: `${Date.now()}-${Math.random()}`, ...msg, timestamp: new Date() },
    });
  }, []);

  const handleMessage = useCallback((msg) => {
    switch (msg.type) {

      case 'info':
        addEvent(msg);
        if (msg.message?.includes('ready') || msg.message?.includes('Browser ready')) {
          dispatch({ type: 'SET_STATUS', payload: 'ready' });
        }
        break;

      case 'thinking':
        dispatch({ type: 'SET_STATUS', payload: 'thinking' });
        addEvent(msg);
        break;

      case 'plan':
        dispatch({ type: 'SET_PLAN', payload: msg.data || {} });
        dispatch({ type: 'SET_STATUS', payload: 'executing' });
        addEvent(msg);
        break;

      case 'subtask':
        dispatch({ type: 'SET_ACTIVE_SUBTASK', payload: msg.data?.id });
        addEvent(msg);
        break;

      case 'subtask_done':
        dispatch({ type: 'COMPLETE_SUBTASK', payload: msg.data?.id });
        addEvent(msg);
        break;

      case 'action':
        dispatch({ type: 'SET_LAST_ACTION', payload: msg.data });
        addEvent(msg);
        break;

      case 'success':
        dispatch({ type: 'SET_STATUS', payload: 'done' });
        addEvent(msg);
        break;

      case 'error':
        dispatch({ type: 'SET_STATUS', payload: 'error' });
        addEvent(msg);
        break;

      case 'screenshot':
        dispatch({ type: 'SET_SCREENSHOT', payload: msg.data });
        break;

      case 'stats':
        dispatch({ type: 'SET_STATS', payload: msg.data });
        addEvent(msg);
        break;

      case 'audio_response':
        playAudioChunk(msg.data);
        break;

      case 'pong':
        break;

      default:
        if (msg.message) addEvent(msg);
    }
  }, [addEvent]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN ||
        wsRef.current?.readyState === WebSocket.CONNECTING) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      dispatch({ type: 'WS_OPEN' });
      clearTimeout(reconnectTimer.current);
    };

    ws.onmessage = (e) => {
      try { handleMessage(JSON.parse(e.data)); }
      catch (err) { console.error('[WS parse]', err); }
    };

    ws.onclose = () => {
      dispatch({ type: 'WS_CLOSE' });
      reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [handleMessage]);

  useEffect(() => {
    connect();
    const ping = setInterval(() => send({ type: 'ping' }), 20000);
    return () => {
      clearInterval(ping);
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect, send]);

  /* ── Goal / Interrupt ───────────────────────────────────── */

  const sendGoal = useCallback((goal) => {
    dispatch({ type: 'SET_STATUS', payload: 'thinking' });
    dispatch({ type: 'RESET_RUN' });
    send({ type: 'goal', text: goal, user_id: 'user' });
  }, [send]);

  const sendStop = useCallback(() => {
    send({ type: 'interrupt' });
    dispatch({ type: 'SET_STATUS', payload: 'ready' });
  }, [send]);

  /* ── Voice ──────────────────────────────────────────────── */

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      streamRef.current = stream;

      // Request 16kHz if possible; browser may give us a different rate
      const ctx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = ctx;

      const source    = ctx.createMediaStreamSource(stream);
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        const f32  = e.inputBuffer.getChannelData(0);
        const b64  = float32ToBase64(f32);
        send({ type: 'audio_chunk', data: b64 });
      };

      source.connect(processor);
      processor.connect(ctx.destination);

      dispatch({ type: 'SET_RECORDING', payload: true });
    } catch (err) {
      console.error('[Voice] Microphone error:', err);
      addEvent({ type: 'error', message: `Microphone access denied: ${err.message}` });
    }
  }, [send, addEvent]);

  const stopRecording = useCallback(() => {
    try {
      processorRef.current?.disconnect();
      audioCtxRef.current?.close().catch(() => {});
      streamRef.current?.getTracks().forEach(t => t.stop());
    } catch (e) { /* ignore cleanup errors */ }

    processorRef.current = null;
    audioCtxRef.current  = null;
    streamRef.current    = null;

    send({ type: 'audio_end' });
    dispatch({ type: 'SET_RECORDING', payload: false });
  }, [send]);

  const toggleVoice = useCallback(() => {
    if (state.isRecording) stopRecording();
    else startRecording();
  }, [state.isRecording, startRecording, stopRecording]);

  /* ── Render ─────────────────────────────────────────────── */

  const { connected, status, plan, activeSubTaskId, events, screenshot,
          lastAction, stats, isRecording, showSkills } = state;

  return (
    <div className="app-shell">
      <Header
        connected={connected}
        status={status}
        sessionId={SESSION_ID}
        onSkills={() => dispatch({ type: 'TOGGLE_SKILLS' })}
        onStop={sendStop}
      />

      <div className="app-body">
        {/* Left: Plan + Stats */}
        <aside className="panel-left">
          <PlanPanel plan={plan} activeSubTaskId={activeSubTaskId} />
          <StatsPanel stats={stats} />
        </aside>

        {/* Center: Screen + Goal */}
        <main className="panel-center">
          <ScreenPreview
            screenshot={screenshot}
            lastAction={lastAction}
            status={status}
          />

          {/* Action status bar */}
          <div className="screen-status-bar">
            {lastAction ? (
              <>
                <span className="mf" style={{ fontSize: 9, color: 'var(--accent)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                  Step {lastAction.step}
                </span>
                <span className="mf" style={{ fontSize: 9, color: 'var(--text-2)' }}>
                  {lastAction.type}
                  {lastAction.x != null ? ` · (${lastAction.x}, ${lastAction.y})` : ''}
                  {lastAction.confidence ? ` · ${Math.round(lastAction.confidence * 100)}%` : ''}
                </span>
                {lastAction.goal_progress && (
                  <span className="mf" style={{ fontSize: 9, color: 'var(--text-3)', marginLeft: 'auto', fontStyle: 'italic' }}>
                    {lastAction.goal_progress}
                  </span>
                )}
              </>
            ) : (
              <span className="label">
                {connected ? 'Waiting for goal…' : 'Disconnected — reconnecting…'}
              </span>
            )}
          </div>

          <GoalInput
            onGoal={sendGoal}
            onStop={sendStop}
            status={status}
            connected={connected}
            isRecording={isRecording}
            onVoiceToggle={toggleVoice}
          />
        </main>

        {/* Right: Feed */}
        <aside className="panel-right">
          <AgentFeed events={events} />
        </aside>
      </div>

      {/* Skill Library drawer */}
      {showSkills && (
        <SkillLibrary
          userId="user"
          onClose={() => dispatch({ type: 'TOGGLE_SKILLS' })}
        />
      )}
    </div>
  );
}
