'use strict';
const $ = id => document.getElementById(id);
const state = { models: [], devices: null, settings: null, clip: null, ttsClip: null, health: null, busy: false, filter: 'all', lastSTT: null, credentials: null, voiceCursor: null, voicePage: 0, voiceProvider: 'piper', voiceLibraryBusy: false, voiceProfile: null, studioModel: null };
let toastTimer;

/* HTMLMediaElement output: separate lifecycle from server-side paplay.
   Browser audio may be routed to any current OS sink; it is not proof of
   which speaker produced sound. Visual events are best-effort and optional. */
function instrumentBrowserAudio(player, clipId) {
  let session = null;
  let sequence = 0;
  let reporting = Promise.resolve();
  function report(event) {
    if (!session) return;
    const payload = { clip_id: clipId, playback_session_id: session, sequence: sequence++, event };
    // Serialize one player's observations; never block or interrupt playback.
    reporting = reporting.catch(() => {}).then(() =>
      api('/api/browser-playback', payload, { setup: true })
    ).catch(() => {});
  }
  player.addEventListener('playing', () => {
    if (session) return;
    // One active browser preview at a time in this tab.
    document.querySelectorAll('audio').forEach(other => {
      if (other !== player && !other.paused) other.pause();
    });
    session = crypto.randomUUID().replace(/-/g, '');
    sequence = 0;
    report('playing');
  });
  const stopped = () => {
    if (!session) return;
    report('stopped');
    session = null;
  };
  player.addEventListener('pause', stopped);
  player.addEventListener('ended', stopped);
  player.addEventListener('error', stopped);
  player.addEventListener('emptied', stopped);
}

function toast(message, bad = false) {
  const el = $('toast'); el.textContent = message; el.classList.toggle('error', bad); el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 4800);
}
async function api(path, data, opts = {}) {
  const config = data === undefined ? { method: 'GET' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) };
  if (opts.raw) { config.method = 'POST'; config.headers = {'Content-Type': 'audio/wav'}; config.body = opts.raw; }
  if (opts.setup) config.headers['X-Voice-Lab-Setup'] = 'local';
  const r = await fetch(path, config);
  const result = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(result.error || `HTTP ${r.status}`);
  return result;
}
async function operation(label, fn) {
  if (state.busy) return;
  state.busy = true; document.body.classList.add('working');
  try { await fn(); } catch(e) { toast(`${label}: ${e.message || e}`, true); } finally { state.busy = false; document.body.classList.remove('working'); }
}
function model(id) { return state.models.find(m => m.id === id); }
function shortDevice(name) {
  if (!name) return 'NON DISPONIBILE';
  if (name.includes('BC_Master')) return 'BC-Master 1610USB';
  if (name.includes('bluez_sink.')) return 'Bose Bluetooth';
  if (name.includes('C920')) return 'Logitech C920';
  if (name.includes('CSCTEK')) return 'CSCTEK USB Audio';
  return name.slice(0, 26);
}
function secureCloud(m, kind) {
  if (m.mode !== 'cloud') return true;
  if (m.status !== 'ready') { toast(`Apri Setup e configura la chiave API ${m.provider}.`, true); showView('setup'); return false; }
  return confirm(`TEST CLOUD · ${m.provider}\n\n${kind === 'stt' ? "Invieremo l'audio registrato" : 'Invieremo il testo inserito'} a ${m.provider}.\nIl test potrebbe consumare crediti API. Continuare?`);
}
function selectOptions(target, models, keep) {
  const sel = $(target); sel.replaceChildren();
  models.forEach(m => { const opt = document.createElement('option'); opt.value = m.id; opt.textContent = `${m.name}  ·  ${m.status === 'ready' ? 'pronto' : m.status === 'install' ? 'da installare' : m.status === 'configure' ? 'chiave richiesta' : 'dipendenza assente'}`; sel.appendChild(opt); });
  if (keep && models.some(m => m.id === keep)) sel.value = keep;
}
function showView(view) {
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.route === view));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('shown', v.id === `view-${view}`));
  const labels = {lab:'LABORATORIO',models:'CATALOGO MODELLI',voices:'LIBRERIA VOCI',benchmark:'BENCHMARK A/B',devices:'DISPOSITIVI AUDIO',costs:'COSTI & UTILIZZO',setup:'SETUP API'};
  $('page-crumb').textContent = labels[view];
  if(view === 'benchmark') renderBenchModels();
  if(view === 'voices') loadVoiceLibrary(false);
  if(view === 'setup') refreshCredentials();
  if(view === 'costs') loadCosts();
  window.scrollTo({top: 0, behavior:'smooth'});
}
function setClip(result) {
  state.clip = result;
  const box = $('clip-box'); box.replaceChildren();
  const row = document.createElement('div');
  row.textContent = `✓ WAV selezionato · ${result.duration.toFixed(2)} s`;
  row.style.color = '#8ce8c9'; box.appendChild(row);
  const audio = document.createElement('audio'); audio.controls = true; audio.preload = 'none'; audio.src = `/api/audio/${result.clip_id}`;
  instrumentBrowserAudio(audio, result.clip_id);
  box.appendChild(audio);
  const button = document.createElement('button'); button.textContent = '▶ Riascolta sulla Bose';
  button.onclick = () => playClip(result.clip_id);
  box.appendChild(button);
  const del = document.createElement('button'); del.textContent = 'Elimina';
  del.onclick = () => operation('Eliminazione', async () => { await api('/api/delete', {clip_id: result.clip_id}); state.clip = null; box.textContent = 'Registrazione eliminata.'; $('stt-run').disabled = true; updateBenchClip(); });
  box.appendChild(del);
  $('stt-run').disabled = false; updateBenchClip();
}
async function playClip(clip) {
  await operation('Riproduzione', async () => {
    toast('Riproduzione sullo speaker scelto…');
    const res = await api('/api/play', { clip_id: clip, sink: state.settings?.sink || '' });
    toast(`Riprodotto su ${shortDevice(res.sink)}`);
  });
}
function refreshStats() {
  const d = state.devices || {sources: [],sinks: []};
  const source = state.settings?.source || d.input_suggested;
  const sink = state.settings?.sink || d.output_suggested;
  $('kpi-input').textContent = shortDevice(source);
  $('kpi-output').textContent = shortDevice(sink);
  $('kpi-local').textContent = state.models.filter(m => m.mode === 'local' && m.status === 'ready').length + ' / ' + state.models.filter(m => m.mode === 'local').length;
  $('kpi-latency').textContent = state.lastSTT == null ? '—' : state.lastSTT.toFixed(2) + ' s';
  $('mic-line').textContent = shortDevice(source);
  $('service-dot').className = `dot ${d.available ? 'online' : 'offline'}`;
  $('service-label').textContent = d.available ? 'PulseAudio operativo' : 'Audio non raggiungibile';
  $('input-device-note').textContent = d.sources.length ? `${d.sources.length} sorgenti di ingresso disponibili.` : 'Nessuna sorgente disponibile.';
  $('output-device-note').textContent = d.sinks.length ? `${d.sinks.length} uscite disponibili.` : 'Nessuna uscita disponibile.';
}
function renderDeviceSelects() {
  const d = state.devices || { sources: [], sinks: [] };
  for (const [id, list, prefer] of [['source-select', d.sources, state.settings?.source || d.input_suggested],['sink-select',d.sinks,state.settings?.sink || d.output_suggested]]) {
    const sel = $(id); sel.replaceChildren();
    list.forEach(v => {const opt=document.createElement('option'); opt.value=v; opt.textContent=`${shortDevice(v)}  ·  ${v}`; sel.appendChild(opt); });
    if(list.includes(prefer)) sel.value = prefer;
  }
}
function renderModels() {
  const list = $('model-list'); list.replaceChildren();
  const ms = state.models.filter(m => state.filter === 'all' || m.type === state.filter || m.mode === state.filter);
  ms.forEach(m => {
    const card = document.createElement('article'); card.className='model-card';
    const top = document.createElement('div'); top.className='model-card-top';
    const vendor = document.createElement('span'); vendor.className='provider-stamp'; vendor.textContent=m.provider;
    const tag = document.createElement('span'); tag.className=`model-tag ${m.mode}`; tag.textContent=`${m.type.toUpperCase()} · ${m.mode.toUpperCase()}`;
    top.append(vendor,tag); card.appendChild(top);
    const h = document.createElement('h3'); h.textContent=m.name;
    const p = document.createElement('p'); p.textContent=m.note;
    const foot=document.createElement('div'); foot.className='model-bottom';
    const ready=document.createElement('span'); ready.className=`availability ${m.status==='ready'?'ok':'warn'}`;
    const states={ready:'● PRONTO',install:'○ NON INSTALLATO', configure:'◐ CHIAVE NECESSARIA',missing_dependency:'○ DIPENDENZA ASSENTE'};
    ready.textContent=states[m.status] || m.status;
    const btn=document.createElement('button'); btn.className='mini-btn';
    if(m.status==='install') {btn.textContent='↓ Installa'; btn.onclick=()=>install(m.id);}
    else if(m.status==='ready') {btn.textContent='Prova →'; btn.onclick=()=>{showView('lab'); $(m.type==='stt'?'stt-model':'tts-model').value=m.id; if(m.type==='tts')refreshVoices();};}
    else if(m.status==='configure') {btn.textContent='Configura'; btn.onclick=()=>showView('setup');}
    else {btn.textContent='Ambiente'; btn.disabled=true;}
    foot.append(ready,btn);card.append(top,h,p,foot);list.appendChild(card);
  });
}
async function install(id) {
  const m=model(id);
  if(!confirm(`INSTALLAZIONE LOCALE · ${m.name}\n\nScaricheremo i pesi del modello dalla sorgente del provider e li salveremo nella cartella dati del laboratorio. Potrebbero occupare spazio significativo. Nessun sudo o riavvio. Continuare?`)) return;
  await operation('Installazione',async()=>{
    toast(`Download di ${m.name} avviato; il pulsante resta occupato fino alla verifica…`);
    const res=await api('/api/install',{model_id:id,confirm:true});
    toast(res.message || 'Modello installato');
    await refreshModels();
  });
}
async function refreshModels() {
  const r=await api('/api/models'); state.models=r.models;
  selectOptions('stt-model',state.models.filter(m=>m.type==='stt'),$('stt-model').value || state.settings?.default_stt || 'fw:base:5');
  selectOptions('tts-model',state.models.filter(m=>m.type==='tts'),$('tts-model').value || state.settings?.default_tts || 'piper:it_IT-paola-medium');
  renderModels(); renderBenchModels(); refreshStats();
}
function renderBenchModels() {
  const container=$('bench-models');
  const selected=Array.from(container.querySelectorAll('input:checked')).map(e=>e.value);container.replaceChildren();
  state.models.filter(m=>m.type==='stt').forEach(m=>{
    const label=document.createElement('label');label.className='bench-option';
    const input=document.createElement('input'); input.type='checkbox'; input.value=m.id; input.disabled=m.status!=='ready';
    input.checked=m.status==='ready' && (selected.length ? selected.includes(m.id) : ['fw:base:5','fw:small:1'].includes(m.id));
    label.appendChild(input);label.appendChild(document.createTextNode(m.name));container.appendChild(label);
  });
}
function updateBenchClip() {
  $('benchmark-clip').textContent = state.clip ? `✓ WAV selezionato · ${state.clip.duration.toFixed(2)} s · ID ${state.clip.clip_id.slice(0,8)}` : 'Nessun WAV selezionato. Vai in Laboratorio per registrarne uno.';
  $('benchmark-reference').value = $('reference').value;
}
// === Voice Studio v0.3.0: provider-aware knobs + operator-selected output profile ===
const STUDIO_DEFAULTS = {
  stability:0.5, similarity_boost:0.75, style:0, speaker_boost:true,
  emotion:'neutral', speed:1, pitch_semitones:0
};
function studioModel() { return model($('tts-model').value); }
function studioControls() {
  const m=studioModel(); const eleven=m?.provider==='ElevenLabs'; const v4=eleven && ['eleven_v4','eleven_v4_turbo'].includes(m.model);
  return {
    stability: eleven ? Number($('studio-stability').value)/100 : 0.5,
    similarity_boost: eleven ? Number($('studio-similarity').value)/100 : 0.75,
    style: eleven && !v4 ? Number($('studio-style').value)/100 : 0,
    speaker_boost:true,
    emotion: v4 ? $('studio-emotion').value : 'neutral',
    speed:Number($('studio-speed').value),
    pitch_semitones:Number($('studio-pitch').value)
  };
}
function studioAssignControls(controls = STUDIO_DEFAULTS) {
  const d={...STUDIO_DEFAULTS,...controls};
  $('studio-stability').value=Math.round(d.stability*100);
  $('studio-similarity').value=Math.round(d.similarity_boost*100);
  $('studio-style').value=Math.round(d.style*100);
  $('studio-emotion').value=d.emotion;
  $('studio-speed').value=d.speed;
  $('studio-pitch').value=d.pitch_semitones;
  studioReadouts();
}
function studioReadouts() {
  $('studio-stability-value').textContent=$('studio-stability').value+'%';
  $('studio-similarity-value').textContent=$('studio-similarity').value+'%';
  $('studio-style-value').textContent=$('studio-style').value+'%';
  $('studio-speed-value').textContent=Number($('studio-speed').value).toFixed(2)+'×';
  $('studio-pitch-value').textContent=(Number($('studio-pitch').value)>0?'+':'')+$('studio-pitch').value+' semitoni';
  studioBadge();
}
function studioBadge() {
  const p=state.voiceProfile; const badge=$('studio-default-status');
  if(!p) {badge.textContent='Nessun profilo salvato per Paky-01.';badge.classList.remove('is-default');return;}
  const matching = p.model_id === $('tts-model').value && p.voice_id === $('tts-voice').value &&
    JSON.stringify(p.controls)===JSON.stringify(studioControls());
  badge.classList.toggle('is-default',matching);
  badge.textContent=matching ? '● PROFILO ATTIVO · Paky-01 usa queste impostazioni nell’adapter Voice Studio.' :
    `Salvato: ${p.model_id} · ${p.voice_id} (le modifiche nel Lab non sono ancora salvate)`;
}
function syncStudio() {
  const m=studioModel(); if(!m) return;
  const eleven=m.provider==='ElevenLabs'; const v4=eleven && ['eleven_v4','eleven_v4_turbo'].includes(m.model);
  const changed=m.id!==state.studioModel;
  if(changed) {
    const profile=state.voiceProfile;
    const opts=profile?.model_id===m.id ? profile.controls : STUDIO_DEFAULTS;
    studioAssignControls(opts);
    state.studioModel=m.id;
  }
  $('studio-stability-box').hidden=!eleven;
  $('studio-similarity-box').hidden=!eleven;
  $('studio-style-box').hidden=!eleven || v4;
  $('studio-emotion-box').hidden=!v4;
  if(!v4) $('studio-emotion').value='neutral';
  if(!eleven || v4) $('studio-style').value='0';
  const hasFFmpeg=Boolean(state.health?.ffmpeg);
  for(const id of ['studio-speed','studio-pitch']) {
    $(id).disabled=!hasFFmpeg;
    if(!hasFFmpeg) $(id).value=id==='studio-speed'?'1':'0';
  }
  const description = v4 ? 'Eleven v4/v4 Turbo · stabilità e somiglianza via API, emozione tramite audio tag. Velocità e pitch sono effetti locali, non parametri nativi.' :
    eleven ? 'ElevenLabs v2 · stabilità, somiglianza e stile inviati alla API. Velocità e pitch applicati al WAV locale.' :
    'Voce locale o altro provider: velocità e pitch sono effetti sul WAV; nessun modello cognitivo o RGD viene modificato.';
  $('voice-studio-explain').textContent=description;
  $('studio-postfx-note').textContent=hasFFmpeg ? 'Velocità 0,80–1,20× e pitch ±3 semitoni: effetti FFmpeg locali, opzionali. Possono introdurre artefatti. Nessun cambio al modello originale.' :
    'FFmpeg non rilevato: velocità e pitch disabilitati. Se vuoi usarli, installa ffmpeg su Ubuntu (indipendente da BHL).';
  studioReadouts();
}
async function saveVoiceDefault() {
  const m=studioModel(); const voice=$('tts-voice').value;
  if(!m || m.status!=='ready' || !voice || voice.startsWith('Caricamento')) {
    toast('Seleziona un modello pronto e una voce disponibile prima di salvare.',true);return;
  }
  const controls=studioControls();
  if(!confirm(`PREDEFINITA PAKY-01\n\nModello: ${m.name}\nVoce: ${$('tts-voice').selectedOptions[0]?.textContent || voice}\n\nSalviamo un profilo I/O locale. Non modificherà i modelli ElevenLabs, l’identità RGD o i motori del robot. Continuare?`))return;
  await operation('Voce predefinita',async()=>{
    const result=await api('/api/voice-default',{
      robot_instance_id:'paky-01',model_id:m.id,voice_id:voice,controls,confirm:true
    },{setup:true});
    state.voiceProfile=result.profile;
    studioBadge();
    toast('Voce predefinita di Paky-01 salvata nel profilo audio locale.');
  });
}
async function refreshVoices(preferredVoice='') {
  const m=model($('tts-model').value);const sel=$('tts-voice');sel.replaceChildren();
  if(!m || m.status!=='ready') {const opt=document.createElement('option');opt.textContent='Provider non configurato';sel.appendChild(opt);return;}
  const temp=document.createElement('option');temp.textContent='Caricamento voci…';sel.appendChild(temp);
  try{
    const result=await api(`/api/voices?provider=${encodeURIComponent(m.provider)}&model_id=${encodeURIComponent(m.id)}`);
    sel.replaceChildren();result.voices.forEach(v=>{const o=document.createElement('option');o.value=v.id;o.textContent=v.name;sel.appendChild(o);});
    if(preferredVoice && Array.from(sel.options).some(o=>o.value===preferredVoice)) sel.value=preferredVoice;
    else if(m.provider==='OpenAI' && Array.from(sel.options).some(o=>o.value==='marin')) sel.value='marin';
    if(!result.voices.length){const o=document.createElement('option');o.textContent='Nessuna voce disponibile';sel.appendChild(o);}
  }catch(e){sel.replaceChildren();const o=document.createElement('option');o.textContent='Voci non disponibili';sel.appendChild(o);toast(e.message,true);}
  finally { syncStudio(); }
}
// === Voice Library v0.2 ===
function voiceSearchParams(more = false) {
  const provider = $('voice-library-provider').value;
  const args = new URLSearchParams({provider, query:$('voice-library-query').value.trim(), language:$('voice-library-language').value});
  if (more && provider === 'elevenlabs' && state.voiceCursor) args.set('after', state.voiceCursor);
  if (more && provider === 'elevenlabs_shared') args.set('page', String(state.voicePage));
  return args;
}
function voiceLanguageTag(voice) { return (voice.language || 'Multilingua').replace('_','-'); }
function sampleFor(voice) {
  const lang = (voice.language || 'it').slice(0,2).toLowerCase();
  return ({it:'Ciao, sono Paky zero uno. Questa voce è un test di OpenRGD.',
    en:'Hello, this is a voice test for OpenRGD. How do I sound?',
    es:'Hola, esta es una prueba de voz para OpenRGD.',
    fr:'Bonjour, ceci est un essai vocal pour OpenRGD.',
    de:'Hallo, dies ist ein Sprachtest für OpenRGD.',
    pt:'Olá, esta é uma demonstração de voz para OpenRGD.',
    sw:'Jambo! Habari gani? Huu ni mtihani wa sauti.'})[lang] || 'Hello, this is a voice test for OpenRGD.';
}
async function useVoiceInLab(v) {
  const m = model(v.model_id || (v.provider === 'OpenAI' ? 'oa:gpt-4o-mini-tts' : 'el:eleven_flash_v2_5'));
  if(!m || m.status!=='ready') { toast('Il modello non è pronto. Installa o configura il provider.', true); return; }
  showView('lab');
  $('tts-model').value=m.id;
  await refreshVoices(v.id);
  if([...$('tts-voice').options].some(o=>o.value===v.id)) {
    $('tts-voice').value=v.id;
    $('tts-text').value=sampleFor(v);
    toast(`Voce ${v.name} selezionata: genera una prova nel laboratorio.`);
  } else {
    toast('Voce non presente nell’account. Aggiungila prima alla libreria ElevenLabs.',true);
  }
}
async function previewVoice(v, holder) {
  const m = model(v.model_id || (v.provider==='OpenAI' ? 'oa:gpt-4o-mini-tts' : 'el:eleven_flash_v2_5'));
  if(!m || m.status!=='ready') { toast('Modello non pronto. Installa la voce o configura la chiave.',true); return; }
  if(!secureCloud(m,'tts')) return;
  await operation('Prova voce',async()=>{
    holder.textContent='Sintesi in corso…';
    const result=await api('/api/synthesize',{model_id:m.id,voice_id:v.id,text:sampleFor(v),confirm_cloud:m.mode==='cloud'});
    holder.replaceChildren();
    const player=document.createElement('audio');player.controls=true;player.preload='none';player.src=`/api/audio/${result.clip_id}`;
    const bose=document.createElement('button');bose.className='mini-btn';bose.textContent='▶ Bose';bose.onclick=()=>playClip(result.clip_id);
    const note=document.createElement('span');note.className='micro-note';note.textContent=`${result.seconds.toFixed(2)} s`;
    instrumentBrowserAudio(player, result.clip_id);
    holder.append(player,bose,note);
    toast('Anteprima generata: ascolta il risultato.');
  });
}
function voiceCard(v, shared = false) {
  const card=document.createElement('article');card.className='voice-card';
  const badges=document.createElement('div');badges.className='voice-badges';
  const tag=document.createElement('span');tag.className='voice-provider-tag';tag.textContent=v.provider;
  const lang=document.createElement('span');lang.className='voice-lang';lang.textContent=voiceLanguageTag(v);
  badges.append(tag,lang);
  const h=document.createElement('h3');h.textContent=v.name;
  const desc=document.createElement('p');desc.textContent=v.note || (shared?'Voce condivisa dalla community ElevenLabs.':'Modello disponibile nel catalogo.');
  const qual=document.createElement('div');qual.className='voice-qual';
  const available=v.status==='ready';
  const status=document.createElement('span');status.className=`availability ${available?'ok':'warn'}`;
  status.textContent=available ? '● PRONTA' : shared ? '◐ DA AGGIUNGERE' : v.status==='install' ? '○ INSTALLABILE' : v.status==='missing_dependency' ? '○ DIPENDENZA PYTHON ASSENTE' : '◐ CONFIGURA PROVIDER';
  qual.textContent=[v.quality,v.category].filter(Boolean).join(' · ') || (v.mode==='local'?'Locale / Piper ONNX':'API cloud');
  const actions=document.createElement('div');actions.className='voice-actions';
  if(shared) {
    const add=document.createElement('button');add.className='mini-btn';add.textContent='+ Aggiungi al workspace';
    add.onclick=()=>addSharedVoice(v);actions.appendChild(add);
  } else if(v.status==='install') {
    const installBtn=document.createElement('button');installBtn.className='mini-btn';installBtn.textContent='↓ Installa voce';
    installBtn.onclick=()=>installVoice(v);actions.appendChild(installBtn);
  } else if(available) {
    const use=document.createElement('button');use.className='mini-btn';use.textContent='Usa →';use.onclick=()=>useVoiceInLab(v);
    const listen=document.createElement('button');listen.className='mini-btn subtle';listen.textContent='♫ Prova';
    actions.append(use,listen);
    const preview=document.createElement('div');preview.className='voice-preview';card.append(badges,h,desc,qual,actions,status,preview);
    listen.onclick=()=>previewVoice(v,preview);
    return card;
  } else {
    const setup=document.createElement('button');setup.className='mini-btn';
    setup.textContent=v.status==='missing_dependency'?'Dipendenza →':'Setup →';
    setup.onclick=()=>v.status==='missing_dependency'?showView('models'):showView('setup');actions.appendChild(setup);
  }
  card.append(badges,h,desc,qual,actions,status);
  return card;
}
async function installVoice(v) {
  await install(v.model_id);
  await loadVoiceLibrary(false);
}
async function addSharedVoice(v) {
  if(!confirm(`ELEVENLABS · AGGIUNGI VOCE

Vuoi aggiungere «${v.name}» alla tua raccolta di voci? Questa operazione modifica il tuo workspace ElevenLabs e può essere soggetta a limiti del piano.`))return;
  await operation('Aggiunta voce', async()=>{
    const res=await api('/api/voices/add-shared',{public_owner_id:v.public_owner_id,voice_id:v.id,name:v.name,confirm:true},{setup:true});
    toast('Voce aggiunta al workspace ElevenLabs.');
    $('voice-library-provider').value='elevenlabs';
    await loadVoiceLibrary(false);
    await refreshModels();
    // ID can differ from the source voice ID after adding to an account.
    if(res.voice_id) toast(`Voce aggiunta: «${res.name}». Puoi selezionarla dal catalogo personale.`);
  });
}
async function loadVoiceLibrary(more = false) {
  if(state.voiceLibraryBusy) return;
  state.voiceLibraryBusy=true;
  const provider=$('voice-library-provider').value;
  const list=$('voice-library-grid');
  if(!more) {state.voiceCursor=null;state.voicePage=0;list.replaceChildren();}
  if(more && provider==='elevenlabs_shared') state.voicePage++;
  $('voice-library-more').hidden=true;
  $('voice-library-status').textContent='Ricerca voci in corso…';
  try {
    const data=await api('/api/voice-library?' + voiceSearchParams(more));
    if(!data.configured) {
      $('voice-library-status').textContent=data.message||'Configura la chiave API in Setup.';
      const b=document.createElement('button');b.className='btn line';b.textContent='Apri Setup →';b.onclick=()=>showView('setup');list.appendChild(b);
    } else {
      data.voices.forEach(v=>list.appendChild(voiceCard(v,provider==='elevenlabs_shared')));
      if(!list.children.length){const empty=document.createElement('div');empty.className='empty-result';empty.textContent='Nessuna voce trovata per questa ricerca.';list.appendChild(empty);}
      $('voice-library-status').textContent=`${list.querySelectorAll('.voice-card').length} voci mostrate · ${provider==='piper'?'catalogo locale curato':provider==='elevenlabs_shared'?'tendenze ElevenLabs':provider==='elevenlabs'?'account ElevenLabs':'voci OpenAI'}`;
      state.voiceCursor=data.after||null;
      $('voice-library-more').hidden=!data.has_more;
    }
  } catch(e) {
    $('voice-library-status').textContent='Impossibile caricare il catalogo.';
    toast(`Libreria voci: ${e.message}`,true);
  } finally {state.voiceLibraryBusy=false;}
}

// === Provider credentials v0.2 ===
async function refreshCredentials() {
  try {
    const data=await api('/api/credentials');state.credentials=data.providers;state.credentialChecks=data.checks||{};
    if(data.storage) $('config-path').textContent=data.storage;
    for(const provider of ['elevenlabs','openai']) {
      const status=state.credentials[provider]||{};
      const element=$(`credential-${provider}-status`);
      element.className=`credential-state ${status.configured?'is-set':''}`;
      element.textContent=status.configured ? (status.source==='local_env'?'● Chiave salvata nel .env locale':'● Chiave disponibile dall’ambiente di processo') : '○ Nessuna chiave configurata';
      const details=$(`credential-${provider}-verification`);
      if(details) {
        const check=state.credentialChecks[provider];
        if(!check || !status.configured) {details.hidden=true;details.textContent='';}
        else {
          const labels={user_read:'Profilo utente',models_read:'Catalogo modelli',voices_read:'Lettura voci'};
          const icon={available:'✓',restricted:'○',unauthorized:'×',unreachable:'?',rate_limited:'!',payment_required:'!',provider_error:'!',unknown:'?'};
          const scopes=Object.entries(check.checks||{}).map(([scope,item])=>`${icon[item.status]||'?'} ${labels[scope]||scope}: ${item.status}${item.http_status?' (HTTP '+item.http_status+')':''}`);
          details.hidden=false;
          details.textContent=check.message+'\n'+scopes.join(' · ');
          details.classList.toggle('is-limited', check.state==='limited_unconfirmed'||check.state==='verified_limited');
        }
      }
    }
  }catch(e){toast(`Setup: ${e.message}`,true);}
}
async function credentialAction(provider,action) {
  const input=$(`credential-${provider}-input`);
  const value=input.value.trim();
  if(action==='save' && value.length < 12) {toast('Inserisci una chiave API valida (almeno 12 caratteri).',true);return;}
  if(action==='delete' && !confirm(`Rimuovere la chiave ${provider} salvata nel file .env locale?
Le variabili d’ambiente esterne, se presenti, non verranno rimosse.`)) return;
  await operation('Setup provider', async()=>{
    const result=await api('/api/credentials',{provider,action,key:action==='save'?value:undefined,confirm:true},{setup:true});
    input.value='';
    await refreshCredentials();
    await refreshModels();
    await refreshVoices();
    if(action==='verify') toast(result.message || (result.verified ? 'Accesso ai metadati verificato' : 'Verifica non conclusiva'), result.state==='unauthorized');
    else toast(action==='save'?'Chiave salvata in ~/.config/openrgd/voice-lab/.env (permessi 0600)':result.configured?'Chiave .env rimossa; resta una variabile di processo':'Chiave rimossa dal .env locale');
  });
}

async function runSTT() {
  if(!state.clip){toast('Registra o importa prima un WAV',true);return;}
  const m=model($('stt-model').value);
  if(!m || m.status!=='ready'){toast('Modello non pronto. Apri Catalogo modelli.',true);return;}
  if(!secureCloud(m,'stt'))return;
  await operation('Trascrizione',async()=>{
    $('transcript').textContent='Analisi in corso…';$('wer-box').textContent='';
    const r=await api('/api/transcribe',{clip_id:state.clip.clip_id,model_id:m.id,reference:$('reference').value,confirm_cloud:m.mode==='cloud'});
    $('transcript').textContent=r.text||'(Nessuna voce riconosciuta)';
    $('stt-timing').textContent=`${r.seconds.toFixed(2)} s`;
    $('wer-box').textContent=r.wer ? `WER ${r.wer.wer_pct}% · ${r.wer.errors} errori su ${r.wer.words} parole` : 'Per calcolare il WER, inserisci il testo di riferimento.';
    state.lastSTT=r.seconds;refreshStats();toast(`${m.name}: trascrizione completata in ${r.seconds.toFixed(2)} s`);
  });
}
async function runTTS() {
  const m=model($('tts-model').value);if(!m || m.status!=='ready'){toast('Seleziona un TTS pronto nel Catalogo.',true);return;}
  if(!secureCloud(m,'tts'))return;
  await operation('Sintesi',async()=>{
    $('tts-output').textContent='Generazione vocale in corso…';
    const r=await api('/api/synthesize',{model_id:m.id,voice_id:$('tts-voice').value,text:$('tts-text').value,controls:studioControls(),confirm_cloud:m.mode==='cloud'});
    state.ttsClip=r;
    $('tts-timing').textContent=`${r.seconds.toFixed(2)} s · ${r.duration.toFixed(1)} s audio` + (r.time_to_first_audio_seconds != null ? ` · primo chunk ${r.time_to_first_audio_seconds.toFixed(2)}s (buffer completo prima dell’ascolto)` : '');
    const holder=$('tts-output');holder.replaceChildren();
    const player=document.createElement('audio');player.controls=true;player.src=`/api/audio/${r.clip_id}`;player.preload='none';instrumentBrowserAudio(player, r.clip_id);holder.appendChild(player);
    const p=document.createElement('div');
    const play=document.createElement('button');play.className='btn ghost';play.textContent='▶ Ascolta sulla Bose';play.onclick=()=>playClip(r.clip_id);
    const del=document.createElement('button');del.className='btn ghost';del.textContent='Elimina audio';del.onclick=()=>operation('Eliminazione',async()=>{await api('/api/delete',{clip_id:r.clip_id});holder.textContent='Audio eliminato.';});
    p.append(play,del);holder.appendChild(p);toast('Anteprima vocale pronta');
  });
}
async function runBenchmark() {
  if(!state.clip){toast('Registra un WAV in Laboratorio prima del benchmark.',true);return;}
  const targets=Array.from($('bench-models').querySelectorAll('input:checked')).map(x=>model(x.value));
  if(!targets.length){toast('Seleziona almeno un modello STT pronto.',true);return;}
  const clouds=targets.filter(m=>m.mode==='cloud');
  if(clouds.length && !confirm(`BENCHMARK CLOUD\n\nInvieremo lo stesso WAV ${clouds.length} volte, ai provider: ${Array.from(new Set(clouds.map(m=>m.provider))).join(', ')}.\nOgni chiamata può consumare crediti. Continuare?`))return;
  await operation('Benchmark',async()=>{
    const reference=$('benchmark-reference').value;const box=$('benchmark-results');box.replaceChildren();
    let completed=0;
    for(const m of targets){
      $('benchmark-status').textContent=`Analisi ${completed+1}/${targets.length}: ${m.name}…`;
      try{
        const r=await api('/api/transcribe',{clip_id:state.clip.clip_id,model_id:m.id,reference,confirm_cloud:m.mode==='cloud'});
        const row=document.createElement('div');row.className='bench-row';
        const title=document.createElement('div');title.className='row-title';
        const name=document.createElement('strong');name.textContent=m.name;
        const secs=document.createElement('span');secs.textContent=r.seconds.toFixed(2)+' s'; title.append(name,secs);
        const transcript=document.createElement('p');transcript.textContent=r.text||'Nessun testo';
        row.append(title,transcript);
        if(r.wer){const b=document.createElement('div');b.className='bar-track';const fill=document.createElement('div');fill.className='bar-fill';fill.style.width=`${Math.max(0,Math.min(100,100-r.wer.wer_pct))}%`;b.appendChild(fill);const info=document.createElement('div');info.className='bench-metrics';info.textContent=`WER ${r.wer.wer_pct}% · ${r.wer.errors}/${r.wer.words} parole`;row.append(b,info);}
        box.appendChild(row);
      }catch(e){const p=document.createElement('p');p.textContent=`${m.name}: ${e.message}`;box.appendChild(p);}
      completed++;
    }
    $('benchmark-status').textContent=`Completato: ${completed} modelli, stesso file audio.`;toast('Confronto A/B concluso');
  });
}

// === v0.3.1 Cost & Usage — conservative pricing, provenance and local telemetry ===
let costData = null;
const costUSD = n => n == null ? 'DA VERIFICARE' : '$' + Number(n).toFixed(Number(n)<1?4:2);
function costModelName(modelId) { return model(modelId)?.name || modelId.replace('el:','ElevenLabs · '); }
function costRateUnit(rate) {
  const value = rate.usd_per_1000_chars ?? rate.usd_per_audio_hour;
  return value == null ? 'Tariffa non disponibile' : costUSD(value) + (rate.usd_per_audio_hour != null ? ' / ora input' : ' / 1.000 caratteri');
}
function costRateLabel(rate) {
  return ({public_reference:'LISTINO PUBBLICO · RIFERIMENTO',operator_override:'INSERITO DALL’OPERATORE',unconfirmed:'V4 · PREZZO DA VERIFICARE',unpriced:'NON QUOTATO'})[rate.rate_status] || rate.rate_status;
}
function renderCosts(d) {
  costData=d;
  $('cost-month').textContent=d.usage.month_utc;
  $('cost-requests').textContent=d.usage.total_requests;
  $('cost-estimated').textContent=costUSD(d.usage.total_estimated_usd_known_only);
  $('cost-unpriced').textContent=d.usage.rows.reduce((n,row)=>n+row.requests_unpriced,0);
  const sync=d.sync;
  $('cost-sync-status').textContent=sync.synced_at_utc ?
    `Ultimo sync: ${new Date(sync.synced_at_utc).toLocaleString('it-IT')} · ${sync.model_count} modelli · lettura piano: ${sync.subscription_state} · prezzi USD non ricavati automaticamente` :
    'Non sincronizzato. Il catalogo modelli sarà letto dalle API (non recupera prezzi USD contrattuali).';
  const cards=$('cost-rate-grid');cards.replaceChildren();
  for(const rate of d.rates){
    const box=document.createElement('div'); box.className='cost-rate-card';
    const title=document.createElement('div'); title.className='cost-rate-title';
    const nm=document.createElement('strong');nm.textContent=costModelName(rate.model_id);
    const pill=document.createElement('span');pill.className='cost-source-tag';pill.textContent=costRateLabel(rate);title.append(nm,pill);
    const amt=document.createElement('div');amt.className='cost-rate-amount';amt.textContent=costRateUnit(rate);
    const meta=document.createElement('p'); meta.className='micro-note';
    const fetched=rate.provider_model_metadata;
    const factor=fetched?.model_rates?.character_cost_multiplier;
    meta.textContent=`Fonte: ${rate.source||'non disponibile'} · aggiornamento base: ${rate.reference_as_of||'—'}`+
      (factor != null ? ` · moltiplicatore credito API: ${factor} (non convertito automaticamente in USD)` : '');
    const row=document.createElement('div');row.className='cost-rate-actions';
    const edit=document.createElement('button');edit.className='btn line';edit.textContent=rate.operator_supplied?'Modifica tariffa':'Inserisci tariffa';
    edit.onclick=()=>editPrice(rate);
    row.appendChild(edit);
    if(rate.operator_supplied){const del=document.createElement('button');del.className='btn ghost';del.textContent='Ripristina listino';del.onclick=()=>removePrice(rate);row.appendChild(del);}
    box.append(title,amt,meta,row);cards.appendChild(box);
  }
  const selected=$('cost-model').value;
  const sel=$('cost-model');sel.replaceChildren();
  d.rates.filter(r=>r.usd_per_audio_hour == null).forEach(r=>{const o=document.createElement('option');o.value=r.model_id;o.textContent=costModelName(r.model_id);sel.appendChild(o);});
  if(d.rates.some(r=>r.model_id===selected))sel.value=selected;
  else if(d.rates.some(r=>r.model_id==='el:eleven_v4_turbo'))sel.value='el:eleven_v4_turbo';
  const list=$('cost-usage-list');list.replaceChildren();
  if(!d.usage.rows.length){const empty=document.createElement('div');empty.className='muted';empty.textContent='Nessun evento ancora registrato. I test successivi appariranno qui.';list.appendChild(empty);}
  for(const row of d.usage.rows){
    const el=document.createElement('div');el.className='cost-usage-row';
    const nm=document.createElement('div');const title=document.createElement('strong');title.textContent=costModelName(row.model_id);
    const more=document.createElement('small'); more.textContent=`${row.requests} richieste · ${row.chars} caratteri · ${(row.audio_seconds/60).toFixed(2)} min audio · ${row.requests_unpriced} non quotate`;
    nm.append(title,more);
    const value=document.createElement('div');value.className='cost-value-right';
    const cost=document.createElement('strong');cost.textContent=costUSD(row.estimated_usd_known_only)+(row.requests_unpriced?' + ?':'');
    const rate=document.createElement('small');rate.textContent=row.usd_per_voiced_hour_estimate == null ? 'costo/ora non calcolabile' : `$${row.usd_per_voiced_hour_estimate.toFixed(3)} / ora parlata`;
    value.append(cost,rate);el.append(nm,value);list.appendChild(el);
  }
}
async function loadCosts() {
  try { renderCosts(await api('/api/costs')); }
  catch(err){toast('Costi: '+err.message,true);}
}
async function syncCosts(){
  if(!confirm('SINCRONIZZAZIONE PROVIDER\n\nInterrogheremo ElevenLabs per il catalogo dei modelli, i moltiplicatori e, se autorizzato, il piano. Non genera audio, non consuma crediti TTS. Non restituisce un tariffario USD contrattuale. Continuare?'))return;
  await operation('Sincronizzazione',async()=>{
    const r=await api('/api/pricing/sync',{confirm:true},{setup:true});
    toast(`Sincronizzati ${r.model_count} modelli · piano ${r.subscription_state}. Tariffe USD non sincronizzabili dall’API.`);
    await loadCosts();
  });
}
async function editPrice(rate){
  const unit=rate.usd_per_audio_hour != null || rate.model_id==='el:scribe_v2' ? 'usd_per_audio_hour' : 'usd_per_1000_chars';
  const old=rate[unit];
  const reply=prompt(`PREZZO INSERITO DALL’OPERATORE\n${costModelName(rate.model_id)}\n\nInserisci USD ${unit==='usd_per_audio_hour'?'per ora di audio STT':'per 1.000 caratteri TTS'}. Usa il tuo listino/contratto; non è un prezzo verificato automaticamente.`,old == null?'':String(old));
  if(reply==null)return;
  if(!/^\d+(?:[.,]\d+)?$/.test(reply.trim())){toast('Inserisci un numero positivo, ad esempio 0.08',true);return;}
  if(!confirm(`Salvare ${reply} USD per unità per ${rate.model_id}? Sarà contrassegnato «inserito dall'operatore» e non come prezzo ufficiale.`))return;
  await operation('Tariffa',async()=>{
    await api('/api/pricing/rate',{model_id:rate.model_id,unit,unit_price_usd:Number(reply.replace(',','.')),action:'save',confirm:true},{setup:true});
    await loadCosts();
    toast('Tariffa manuale salvata localmente.');
  });
}
async function removePrice(rate){
  if(!confirm(`Rimuovere la tariffa manuale per ${rate.model_id}?`))return;
  await operation('Tariffa',async()=>{
    await api('/api/pricing/rate',{model_id:rate.model_id,action:'delete',confirm:true},{setup:true});
    await loadCosts();
  });
}
async function calculateCost(){
  await operation('Proiezione',async()=>{
    const result=await api('/api/pricing/forecast',{
      model_id:$('cost-model').value,minutes_per_day:Number($('cost-minutes').value),
      chars_per_spoken_minute:Number($('cost-cpm').value),days:30
    });
    $('forecast-minute').textContent=costUSD(result.usd_per_voiced_minute);
    $('forecast-hour').textContent=costUSD(result.usd_per_voiced_hour);
    $('forecast-month').textContent=costUSD(result.usd_period);
    $('forecast-note').textContent=(result.usd_period==null ? 'Tariffa da verificare: inseriscila manualmente sulla scheda modello o consulta la tua fattura.' :
        `Proiezione ${result.minutes_per_day} minuti/giorno · ${result.chars_per_spoken_minute_assumed} caratteri/minuto · 30 giorni.`)+
        ` Origine tariffa: ${result.rate_status}. Solo TTS, non include STT, LLM, IVA o costo dell’abbonamento.`;
  });
}

async function boot(){
  try{
    const [health,devices,settings,models,voiceDefault]=await Promise.all([api('/api/health'),api('/api/devices'),api('/api/settings'),api('/api/models'),api('/api/voice-default')]);
    state.health=health;state.devices=devices;state.settings=settings;state.models=models.models;state.voiceProfile=voiceDefault.profile;
    $('host-label').textContent=`Paky-01 · ${health.host}`;
    selectOptions('stt-model',state.models.filter(m=>m.type==='stt'),settings.default_stt);
    selectOptions('tts-model',state.models.filter(m=>m.type==='tts'),state.voiceProfile?.model_id || settings.default_tts);
    refreshStats();renderModels();renderDeviceSelects();renderBenchModels();updateBenchClip();await refreshVoices(state.voiceProfile?.voice_id || '');
    if(!devices.available)toast('PulseAudio non raggiungibile. Controlla che il server giri nella sessione utente del mini-PC.',true);
  } catch(e){ toast(`Backend non disponibile: ${e.message}`,true);$('service-label').textContent='Backend non disponibile'; }
}

document.querySelectorAll('.nav-btn').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.route)));
document.querySelectorAll('.filter').forEach(b=>b.addEventListener('click',()=>{state.filter=b.dataset.filter;document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active',x===b));renderModels();}));
$('record-btn').onclick=()=>operation('Registrazione',async()=>{
  $('record-btn').textContent='● Registrazione in corso…';$('record-btn').disabled=true;
  try{
    const r=await api('/api/record',{seconds:8,source:state.settings?.source || ''});
    setClip(r);toast(`Registrazione completata: picco ${r.peak_pct}% FS · RMS ${r.rms_pct}% FS`);
  }finally{$('record-btn').textContent='● Registra 8 secondi';$('record-btn').disabled=false;}
});
$('upload-input').addEventListener('change',async(e)=>{
  const file=e.target.files?.[0];if(!file)return;
  await operation('Importazione',async()=>{
    if(file.size>10*1024*1024)throw new Error('WAV troppo grande (max 10MB)');
    const r=await api('/api/import',undefined,{raw:file});setClip(r);toast(`Importato: ${file.name}`);
  }); e.target.value='';
});
$('stt-run').onclick=runSTT;
$('tts-run').onclick=runTTS;
$('tts-model').onchange=()=>refreshVoices();
$('tts-voice').onchange=studioBadge;
$('studio-save-default').onclick=saveVoiceDefault;
['studio-stability','studio-similarity','studio-style','studio-speed','studio-pitch','studio-emotion'].forEach(id=>{
  $(id).addEventListener('input',studioReadouts);
  $(id).addEventListener('change',studioBadge);
});
$('benchmark-run').onclick=runBenchmark;
$('cost-sync').onclick=syncCosts;
$('cost-refresh').onclick=loadCosts;
$('cost-calc').onclick=calculateCost;
$('cost-model').onchange=calculateCost;
$('save-devices').onclick=()=>operation('Salvataggio',async()=>{
  const res=await api('/api/settings',{source:$('source-select').value,sink:$('sink-select').value});state.settings=res.settings;refreshStats();toast('Preferenze audio salvate solo per Voice Lab');
});
$('refresh-devices').onclick=()=>operation('Dispositivi',async()=>{
  state.devices=await api('/api/devices');renderDeviceSelects();refreshStats();toast('Elenco dispositivi aggiornato');
});
$('voice-library-search').onclick=()=>loadVoiceLibrary(false);
$('voice-library-more').onclick=()=>loadVoiceLibrary(true);
$('voice-library-provider').onchange=()=>loadVoiceLibrary(false);
$('voice-library-language').onchange=()=>loadVoiceLibrary(false);
$('voice-library-query').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();loadVoiceLibrary(false);}};
document.querySelectorAll('[data-key-action]').forEach(button=>button.onclick=()=>credentialAction(button.dataset.provider,button.dataset.keyAction));
boot();
