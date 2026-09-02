/**
 * Fish Audio Voice Studio — dashboard plugin bundle.
 *
 * Served as the dashboard half (manifest.json entry: dist/index.js) of the
 * unified fishaudio plugin package; a byte-identical copy lives at
 * desktop/plugin.js as the desktop half (same file loads through both doors).
 *
 * Built as a plain IIFE over window.__HERMES_PLUGIN_SDK__ — the dashboard
 * host injects React + the UI kit; no bundler imports. Styled only with
 * theme variables (var(--ui-*), var(--chrome-*)); no hardcoded colors.
 */
(function () {
  'use strict';
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  var React = SDK.React;
  var C = SDK.components;
  var cn = SDK.utils.cn;
  var fetchJSON = SDK.fetchJSON;
  var _jsx = function (type, props) {
    // Minimal createElement wrapper so the bundle reads like jsx() calls.
    var children = props && props.children;
    delete (props ? props : {}).children;
    if (children === undefined) return React.createElement(type, props);
    if (Array.isArray(children)) return React.createElement.apply(null, [type, props].concat(children));
    return React.createElement(type, props, children);
  };

  var API_BASE = '/api/plugins/fishaudio';

  function api(path, options) {
    return fetchJSON(API_BASE + path, options);
  }

  function SettingsCard() {
    var settingsState = React.useState(null);
    var settings = settingsState[0], setSettings = settingsState[1];
    var dirtyState = React.useState({});
    var dirty = dirtyState[0], setDirty = dirtyState[1];
    var voicesState = React.useState({ loading: false, items: [], error: null });
    var voices = voicesState[0], setVoices = voicesState[1];
    var voiceFilterState = React.useState('');
    var voiceFilter = voiceFilterState[0], setVoiceFilter = voiceFilterState[1];
    var savingState = React.useState(false);
    var saving = savingState[0], setSaving = savingState[1];
    var msgState = React.useState(null);
    var msg = msgState[0], setMsg = msgState[1];
    var previewState = React.useState({ busy: false, url: null, error: null });
    var preview = previewState[0], setPreview = previewState[1];
    var previewTextState = React.useState('Hello — this is how I will sound.');
    var previewText = previewTextState[0], setPreviewText = previewTextState[1];

    var loadSettings = React.useCallback(function () {
      api('/settings')
        .then(function (res) { setSettings(res.settings || {}); })
        .catch(function (err) { setMsg({ kind: 'error', text: String(err && err.message || err) }); });
    }, []);
    React.useEffect(function () { loadSettings(); }, [loadSettings]);

    var loadVoices = React.useCallback(function () {
      setVoices(function (s) { return Object.assign({}, s, { loading: true, error: null }); });
      api('/voices')
        .then(function (res) {
          setVoices({ loading: false, items: res.voices || [], error: null });
        })
        .catch(function (err) {
          setVoices(function (s) { return Object.assign({}, s, { loading: false, error: String(err && err.message || err) }); });
        });
    }, []);
    React.useEffect(function () { loadVoices(); }, [loadVoices]);

    function value(key, fallback) {
      if (dirty[key] !== undefined) return dirty[key];
      if (settings && settings[key] !== null && settings[key] !== undefined) return settings[key];
      return fallback;
    }

    function setFieldSafe(key, val) {
      var next = Object.assign({}, dirty);
      next[key] = val;
      setDirty(next);
    }

    function currentPatch() {
      // Only include fields the user actually changed; treat '' as unset (null).
      var patch = {};
      Object.keys(dirty).forEach(function (k) {
        var v = dirty[k];
        patch[k] = (v === '' || v === null) ? null : v;
      });
      return patch;
    }

    function save() {
      var patch = currentPatch();
      if (patch.voice_id === '') patch.voice_id = null;
      if (typeof patch.chunk_length === 'string' && patch.chunk_length !== '') {
        patch.chunk_length = parseInt(patch.chunk_length, 10);
      }
      setSaving(true);
      setMsg(null);
      api('/settings', { method: 'POST', body: patch })
        .then(function (res) {
          setSaving(false);
          setDirty({});
          setSettings(res.settings || {});
          setMsg({ kind: 'ok', text: 'Settings saved — new settings take effect on the next synthesis.' });
        })
        .catch(function (err) {
          setSaving(false);
          setMsg({ kind: 'error', text: String(err && err.message || err) });
        });
    }

    function runPreview() {
      if (!previewText.trim()) return;
      // Revoke the previous blob URL before replacing it.
      if (preview.url) { try { URL.revokeObjectURL(preview.url); } catch (e) { /* noop */ } }
      setPreview({ busy: true, url: null, error: null });
      var body = { text: previewText };
      var patch = currentPatch();
      if (typeof patch.voice_id === 'string' && patch.voice_id) body.voice_id = patch.voice_id;
      if (typeof patch.model === 'string' && patch.model) body.model = patch.model;
      api('/preview', { method: 'POST', body: body })
        .then(function (res) {
          var bytes = atob(res.audio_base64);
          var arr = new Uint8Array(bytes.length);
          for (var i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
          var blob = new Blob([arr], { type: 'audio/mpeg' });
          setPreview({ busy: false, url: URL.createObjectURL(blob), error: null });
        })
        .catch(function (err) {
          setPreview({ busy: false, url: null, error: String(err && err.message || err) });
        });
    }

    var filtered = (voices.items || []).filter(function (v) {
      if (!voiceFilter) return true;
      var hay = ((v.display || '') + ' ' + (v.id || '') + ' ' + (v.language || '')).toLowerCase();
      return hay.indexOf(voiceFilter.toLowerCase()) !== -1;
    });

    var selectedVoice = value('voice_id', '');

    return _jsx('div', {
      className: 'fs-card', children: [
        _jsx('div', { className: 'fs-card-title', children: 'Voice & synthesis' }),
        _jsx('div', { className: 'fs-row', children: [
          _jsx('div', { className: 'fs-row-control', key: 'f', children:
            _jsx(C.Input, {
              placeholder: 'Search the voice catalog…',
              value: voiceFilter,
              onChange: function (e) { setVoiceFilter(e.target.value); }
            })
          }),
          _jsx(C.Button, {
            key: 'r', outlined: true, onClick: loadVoices,
            disabled: voices.loading,
            children: voices.loading ? 'Loading…' : 'Refresh'
          })
        ]}),
        _jsx('div', {
          className: 'fs-voice-options',
          children: voices.error
            ? _jsx('div', { className: 'fs-error', style: { padding: '0.6rem' }, children:
                'Voice catalog unavailable (' + voices.error + '). You can still paste a voice id below.' })
            : (voices.loading && filtered.length === 0)
            ? _jsx('div', { className: 'fs-hint', style: { padding: '0.6rem' }, children: 'Loading catalog…' })
            : filtered.length === 0
            ? _jsx(C.EmptyState, { title: 'No voices match', description: 'Try a different search, or clear the filter.' })
            : filtered.map(function (v) {
                return _jsx('button', {
                  key: v.id,
                  type: 'button',
                  className: cn('fs-voice-option', selectedVoice === v.id && 'active'),
                  onClick: function () { setFieldSafe('voice_id', v.id); },
                  children: [
                    _jsx('span', { className: 'fs-voice-name', key: 'n', children: v.display || v.id }),
                    _jsx('span', { className: 'fs-voice-meta', key: 'm', children:
                      (v.language ? v.language : '') + (v.id ? '  ·  ' + v.id : '')
                    }),
                    selectedVoice === v.id
                      ? _jsx('span', { className: 'fs-voice-selected', key: 's', children: '● selected' })
                      : null
                  ]
                });
              })
        }),
        _jsx('div', { className: 'fs-hint', children:
          'Pick from the catalog above or paste a voice id. Clear the field to unset (falls back to FISH_VOICE_ID env / server default).' }),
        _jsx('div', { className: 'fs-row', children: [
          _jsx('div', { className: 'fs-row-label', key: 'l', children: 'Voice id' }),
          _jsx('div', { className: 'fs-row-control', key: 'c', children:
            _jsx(C.Input, {
              placeholder: 'unset',
              value: selectedVoice,
              onChange: function (e) { setFieldSafe('voice_id', e.target.value); }
            })
          })
        ]}),
        _jsx('div', { className: 'fs-row', children: [
          _jsx('div', { className: 'fs-row-label', key: 'l', children: 'Model' }),
          _jsx('div', { className: 'fs-row-control', key: 'c', children:
            _jsx(C.Select, {
              value: value('model', 's2.1-pro-free'),
              onValueChange: function (v) { setFieldSafe('model', v); },
              children: [
                _jsx(C.SelectOption, { key: 's2.1-pro-free', value: 's2.1-pro-free', children: 'S2.1-Pro Free (dev tier — default)' }),
                _jsx(C.SelectOption, { key: 's2.1-pro', value: 's2.1-pro', children: 'S2.1-Pro (production)' }),
                _jsx(C.SelectOption, { key: 's2-pro', value: 's2-pro', children: 'S2-Pro (previous generation)' }),
                _jsx(C.SelectOption, { key: 's1', value: 's1', children: 'S1 (legacy, 13 languages)' })
              ]
            })
          })
        ]}),
        _jsx('div', { className: 'fs-row', children: [
          _jsx('div', { className: 'fs-row-label', key: 'l', children: 'Latency' }),
          _jsx('div', { className: 'fs-row-control', key: 'c', children:
            _jsx(C.SegmentedControl, {
              options: [
                { id: 'balanced', label: 'Balanced (~300 ms)' },
                { id: 'normal', label: 'Normal' }
              ],
              value: value('latency', 'balanced'),
              onChange: function (id) { setFieldSafe('latency', id); }
            })
          })
        ]}),
        _jsx('div', { className: 'fs-row', children: [
          _jsx('div', { className: 'fs-row-label', key: 'l', children: 'Chunk length' }),
          _jsx('div', { className: 'fs-row-control', key: 'c', children:
            _jsx(C.Input, {
              type: 'number', min: 20, max: 2000,
              value: value('chunk_length', 120),
              onChange: function (e) { setFieldSafe('chunk_length', e.target.value); }
            })
          }),
          _jsx('div', { className: 'fs-hint', key: 'h', children: '20-2000 (default 120); smaller = faster first audio' })
        ]}),
        _jsx('div', { className: 'fs-row', children: [
          _jsx('div', { className: 'fs-row-label', key: 'l', children: 'Preview text' }),
          _jsx('div', { className: 'fs-row-control', key: 'c', children:
            _jsx(C.Input, {
              value: previewText,
              onChange: function (e) { setPreviewText(e.target.value); }
            })
          }),
          _jsx(C.Button, {
            key: 'p', outlined: true, onClick: runPreview,
            disabled: preview.busy,
            children: preview.busy ? 'Synthesizing…' : 'Preview'
          })
        ]}),
        preview.error ? _jsx('div', { className: 'fs-error', children: preview.error }) : null,
        preview.url ? _jsx('audio', { className: 'fs-audio', controls: true, src: preview.url }) : null,
        msg ? _jsx('div', {
          className: cn(msg.kind === 'error' ? 'fs-error' : 'fs-hint'),
          children: msg.text
        }) : null,
        _jsx('div', { className: 'fs-actions', children: [
          _jsx(C.Button, {
            key: 'save', onClick: save, disabled: saving || Object.keys(dirty).length === 0,
            children: saving ? 'Saving…' : 'Save settings'
          }),
          _jsx(C.Button, {
            key: 'revert', ghost: true, disabled: Object.keys(dirty).length === 0 || saving,
            onClick: function () { setDirty({}); setMsg(null); },
            children: 'Revert changes'
          }),
          _jsx('span', { className: 'fs-hint', key: 'note', children:
            'Saved settings persist under plugins.entries.fishaudio.settings and take precedence over environment variables.' })
        ]})
      ]
    });
  }

  function VoiceStudioPage() {
    return _jsx('div', { className: 'fs-page', children: [
      _jsx('div', { className: 'fs-header', key: 'h', children: [
        _jsx('div', { key: 't', children: [
          _jsx('div', { className: 'fs-title', children: 'Fish Audio Voice Studio' }),
          _jsx('div', { className: 'fs-subtitle', children:
            'Pick a voice, tune synthesis, preview, and save. Changes apply to every spoken reply.' })
        ]})
      ]}),
      _jsx(SettingsCard, { key: 'settings' })
    ]});
  }

  window.__HERMES_PLUGINS__.register('fishaudio', VoiceStudioPage);
})();
