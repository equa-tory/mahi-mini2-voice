"use strict";
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem("mahi-token")||"";
let speakerConfig={},voiceConfig={},serverActions={},latestState={};
let frames=[["","","",""],["","","",""]];
let testing=false,testPoll=null,lastHeardAt=0;
let previewRunning=false,previewTimer=null,pollTimer=null,weatherTick=0;
const triggerNames={timer_start:"Start a timer",timer_cancel:"Cancel a timer",timer_status:"Timer remaining",weather:"Weather",time:"Time",volume_down:"Volume down",volume_up:"Volume up"};
const defaults={timer_start:["таймер","засеки","timer"],timer_cancel:["отмени","останови","cancel","stop","отмени таймер","stop timer"],timer_status:["сколько осталось","сколько времени осталось","timer status"],weather:["погода","погоду","weather"],time:["который час","what time"],volume_down:["тише","volume down"],volume_up:["громче","volume up"]};
async function api(path,data){
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
 try{
  const response=await fetch(path,{method:data===undefined?"GET":"POST",headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json"},body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal,cache:"no-store"});
  const value=await response.json();
  if(!response.ok||value.ok===false)throw new Error(value.error||"Request failed");
  return value.data===undefined?value:value.data;
 }finally{clearTimeout(timeout)}
}
function toast(message){$("toast").textContent=String(message);$("toast").hidden=false;clearTimeout(toast.timeout);toast.timeout=setTimeout(()=>$("toast").hidden=true,4500)}
async function busy(button,fn){if(button)button.disabled=true;try{await fn()}catch(error){toast(error.message||"Could not complete request")}finally{if(button)button.disabled=false}}
function bind(id,fn){$(id).addEventListener("click",event=>busy(event.currentTarget,fn))}
async function command(args){const result=await api("/ui/control",{args});await refreshState();return result}
function option(value,label=value){const o=document.createElement("option");o.value=value;o.textContent=label;return o}
function choice(values,value){const select=document.createElement("select");for(const item of values)select.append(option(item));select.value=value;if(!select.value&&values.length)select.value=values[0];return select}
function rgb(hex){return [1,3,5].map(index=>parseInt(hex.slice(index,index+2),16))}
function hex(rgb){return "#"+(rgb||[0,0,0]).map(n=>Math.max(0,Math.min(255,n)).toString(16).padStart(2,"0")).join("")}
function setIfIdle(id,value){if(document.activeElement!==$(id))$(id).value=value}
const points={A:[18,8,52,8],B:[62,18,62,42],C:[62,58,62,82],D:[18,92,52,92],E:[8,58,8,82],F:[8,18,8,42],G:[18,50,52,50]};
function renderSegments(container,patterns,editable=false,frameIndex=0){
 const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 325 100");svg.setAttribute("role","group");
 for(let digit=0;digit<4;digit++)for(const [segment,coords]of Object.entries(points)){
  const line=document.createElementNS(ns,"line");
  for(const [key,value]of Object.entries({x1:coords[0]+digit*82,y1:coords[1],x2:coords[2]+digit*82,y2:coords[3]}))line.setAttribute(key,value);
  const on=(patterns[digit]||"").includes(segment);line.classList.add("segment");line.classList.toggle("on",on);
  if(editable){
   const hit=document.createElementNS(ns,"rect");
   const horizontal=coords[1]===coords[3];
   hit.setAttribute("x",Math.min(coords[0],coords[2])+digit*82-(horizontal?4:10));
   hit.setAttribute("y",Math.min(coords[1],coords[3])-(horizontal?10:4));
   hit.setAttribute("width",horizontal?Math.abs(coords[2]-coords[0])+8:20);
   hit.setAttribute("height",horizontal?20:Math.abs(coords[3]-coords[1])+8);
   hit.classList.add("hit-area");hit.setAttribute("tabindex","0");hit.setAttribute("role","button");
   hit.setAttribute("aria-label",`Frame ${frameIndex+1} digit ${digit+1} segment ${segment}`);hit.setAttribute("aria-pressed",String(on));
   const toggle=()=>{const set=new Set(frames[frameIndex][digit]);set.has(segment)?set.delete(segment):set.add(segment);frames[frameIndex][digit]=[...set].sort().join("");line.classList.toggle("on",set.has(segment));hit.setAttribute("aria-pressed",String(set.has(segment)))};
   hit.addEventListener("click",toggle);hit.addEventListener("keydown",event=>{if(event.key===" "||event.key==="Enter"){event.preventDefault();toggle()}});
   svg.append(hit);
  }
  svg.append(line);
 }
 container.replaceChildren(svg);
}
function renderEditors(){renderSegments($("frame0"),frames[0],true,0);renderSegments($("frame1"),frames[1],true,1)}
function stopPreview(){previewRunning=false;clearTimeout(previewTimer);$("previewAnimation").textContent="Preview animation";$("animationPreview").hidden=true}
function startPreview(){if(previewRunning){stopPreview();return}previewRunning=true;$("animationPreview").hidden=false;$("previewAnimation").textContent="Stop preview";let index=0;const step=()=>{if(!previewRunning)return;renderSegments($("animationPreview"),frames[index]);const ms=Math.max(100,Number($("frameMs"+index).value)||1000);index=1-index;previewTimer=setTimeout(step,ms)};step()}
async function refreshState(){
 const state=await api("/ui/state");latestState=state;$("banner").hidden=true;$("connection").textContent="Connected · LAN";$("mode").textContent=state.mode==="local"?"Local only":state.mode||"Unknown";
 setIfIdle("volume",state.volume_percent);$("volumeValue").textContent=state.volume_percent+"%";
 $("micState").textContent=state.hardware_mic_muted?"Rear switch muted":state.mic_muted?"Muted":"Microphone on";
 $("muteToggle").textContent=state.mic_muted?"Unmute microphone":"Mute microphone";
 $("ringReason").textContent=state.ring_reason==="hardware_microphone_muted"?"Native microphone-mute light":(state.ring_reason||"Idle").replaceAll("_"," ");
 $("lightValue").textContent="Room light "+(state.light_input||"—");
 const color=(state.ring_expected_rgb||state.ring_driver_rgb||[[0,0,0]])[0];
 $("ringPreview").style.borderColor=hex(color);$("ringPreview").style.setProperty("--ring-glow",hex(color));
 renderSegments($("liveClock"),state.clock_segments||["","","",""]);
 const timer=state.timer||{},remaining=timer.remaining_seconds||0;
 $("timerRemaining").textContent=String(Math.floor(remaining/60)).padStart(2,"0")+":"+String(remaining%60).padStart(2,"0");
 $("pickerState").textContent=timer.selection_minutes?`Selecting ${timer.selection_minutes} minutes · confirm on speaker or here`:"";
 $("timerState").textContent=timer.ringing?"Ringing":timer.active?"Counting down":"Ready";setIfIdle("alertLevel",timer.sound_level??40);
 $("timerStart").disabled=!!timer.active;
 if(!refreshState.initialized){
  $("clockAuto").checked=state.clock_auto;$("ringAuto").checked=state.ring_auto;
  $("clockBrightness").value=state.clock_brightness||5;$("ringBrightness").value=state.ring_brightness||100;refreshState.initialized=true;
 }
}
async function refreshTest(){
 try{
  const data=await api("/ui/listen-test");testing=data.testing;
  $("listenState").textContent=testing?"Testing · actions paused":"Stopped";
  $("startListen").disabled=testing;$("stopListen").disabled=!testing;
  const db=data.rms>0?20*Math.log10(data.rms/32768):-80;
  $("micMeter").style.width=Math.max(0,Math.min(100,(db+70)/70*100))+"%";
  $("micMeter").style.background=data.peak>32000?"#b83737":"var(--accent)";
  $("micMetrics").textContent=`Input: ${Math.round(data.rms||0)} RMS · peak ${data.peak||0} · threshold ${data.effective_threshold||0} · recognition ${data.asr_ms||0} ms`+(data.peak>32000?" · clipping: lower sensitivity":"");
  $("micMetrics").textContent+=` · speech ${data.speech_start_seconds??"—"}–${data.speech_end_seconds??"—"} s · capture ${data.capture_seconds??0} s · intent ${data.intent||"—"} · arguments ${JSON.stringify(data.arguments??null)}`;
  if(data.text&&data.text_at>lastHeardAt){$("heardText").value=data.text;lastHeardAt=data.text_at}
 }catch(error){$("micMetrics").textContent="Voice diagnostics unavailable: "+error.message}
}
async function stopTest(){await api("/ui/listen-test",{enabled:false});testing=false;await refreshTest()}
async function refreshVoice(){
 const data=await api("/voice/status");$("voicePhase").textContent=data.phase.replaceAll("_"," ");
 $("voiceSummary").textContent=`Wake detections: ${data.wake_count||0} · Requests: ${data.command_count||0}`;
 $("voiceDetail").textContent=`Status: ${data.phase.replaceAll("_"," ")}. Last command: ${data.last_intent||"none"}.`;
 $("voiceConfigError").textContent=data.config_error||data.error||"";
 const history=$("voiceHistory");history.replaceChildren();
 if(!data.history?.length)history.textContent="No voice requests yet.";
 for(const entry of data.history||[]){
  const row=document.createElement("details"),title=document.createElement("summary"),body=document.createElement("pre");
  title.textContent=`${new Date(entry.at*1000).toLocaleTimeString()} · ${entry.text||entry.wake||"Wake detected"} → ${entry.intent||"—"} · ${entry.outcome}`;
  body.textContent=JSON.stringify(entry,null,2);row.append(title,body);
  if(entry.audio_id){const play=document.createElement("button");play.textContent="Listen to request";play.onclick=()=>busy(play,async()=>{const clip=await api("/ui/voice-audio?id="+encodeURIComponent(entry.audio_id));const player=document.createElement("audio");player.controls=true;player.src="data:audio/wav;base64,"+clip.audio;play.replaceWith(player);await player.play()});row.append(play)}
  history.append(row);
 }
}
async function refreshWeather(){try{const data=await api("/ui/weather");$("weatherTemperature").textContent=Math.round(data.temperature_c)+"°C";$("weatherAge").textContent=(data.forecast?"Hourly forecast":"Current estimate")+" · downloaded "+Math.floor(data.age_seconds/60)+" min ago"+(data.stale?" · older cache":"")}catch(error){$("weatherAge").textContent=error.message}}
async function freshSpeaker(){return api("/ui/speaker-config")}
async function saveSpeaker(config){await api("/ui/speaker-config",{config});speakerConfig=config;await loadSpeaker();toast("Speaker settings saved")}
function bindingRow(button="center",gesture="double_tap",action=""){
 const row=document.createElement("div");row.className="row-editor binding-row";
 const b=choice(["center","plus","minus","mute"],button),g=choice(["press","release","repeat","tap","double_tap","triple_tap","hold"],gesture),a=choice(Object.keys(speakerConfig.actions||{}),action);
 b.setAttribute("aria-label","Button");g.setAttribute("aria-label","Gesture");a.setAttribute("aria-label","Speaker action");
 const remove=document.createElement("button");remove.textContent="Remove";remove.onclick=()=>row.remove();row.append(b,g,a,remove);$("bindingRows").append(row);
}
const conditionLabels={always:"Always", "assistant.active":"Assistant is listening, thinking, or speaking", "timer.active":"Timer is active or ringing", "timer.selecting":"Timer picker is open", "local.active":"A local action or display is active"};
const gestureGroups={};
function conditionalGroup(key){if(gestureGroups[key])return gestureGroups[key];const group=document.createElement("section");group.className="conditional-gesture-group";const heading=document.createElement("h4");heading.textContent=key;const rows=document.createElement("div");rows.className="conditional-gesture-rows";group.append(heading,rows);$("conditionalRuleRows").append(group);gestureGroups[key]=rows;return rows}
function conditionalRuleRow(key="center.tap",rule={when:"timer.active",action:"timer.cancel",consume:true}){
 const row=document.createElement("div");row.className="row-editor conditional-rule-row";
 const keys=[...new Set([key,...Object.keys(speakerConfig.bindings||{}),...Object.keys(speakerConfig.conditional_bindings||{})])].sort();
 const gesture=choice(keys,key);gesture.setAttribute("aria-label","Button and gesture");
 const condition=choice(Object.keys(conditionLabels),rule.when);condition.setAttribute("aria-label","Condition");for(const option of condition.options)option.textContent=conditionLabels[option.value];
 const action=choice(Object.keys(speakerConfig.actions||{}),rule.action);action.setAttribute("aria-label","Action");
 const consumeLabel=document.createElement("label");consumeLabel.className="check";const consume=document.createElement("input");consume.type="checkbox";consume.checked=rule.consume===true;consume.setAttribute("aria-label","Consume gesture when matched");consumeLabel.append(consume,document.createTextNode("Consume"));
 const up=document.createElement("button");up.type="button";up.textContent="↑";up.title="Move rule up";up.setAttribute("aria-label","Move rule up");up.onclick=()=>{const prev=row.previousElementSibling;if(prev)row.parentElement.insertBefore(row,prev)};
 const down=document.createElement("button");down.type="button";down.textContent="↓";down.title="Move rule down";down.setAttribute("aria-label","Move rule down");down.onclick=()=>{const next=row.nextElementSibling;if(next)row.parentElement.insertBefore(next,row)};
 const remove=document.createElement("button");remove.type="button";remove.textContent="Remove";remove.onclick=()=>row.remove();row.append(gesture,condition,action,consumeLabel,up,down,remove);conditionalGroup(key).append(row);
}
function aliasRow(phrase="",action=""){
 const row=document.createElement("div");row.className="row-editor alias-row";
 const input=document.createElement("input");input.value=phrase;input.placeholder="включи компьютер";input.setAttribute("aria-label","Spoken phrase");
 const select=choice(["",...Object.keys(serverActions)],action);select.firstChild.textContent="Choose action";select.setAttribute("aria-label","Named server action");
 const remove=document.createElement("button");remove.textContent="Remove";remove.onclick=()=>row.remove();row.append(input,select,remove);$("aliasRows").append(row);
}
function renderActions(container,actions,target){
 container.replaceChildren();
 for(const [name,definition]of Object.entries(actions)){
  const row=document.createElement("div");row.className="action-row";const label=document.createElement("span");label.textContent=name;
  row.dataset.name=name;
  const type=document.createElement("small");type.textContent=definition.type;label.append(type);
  const button=document.createElement("button");button.textContent="Run";button.addEventListener("click",()=>busy(button,async()=>{target==="speaker"?await command(["action","run",name]):await api("/run",{action:name,args:readActionArgs(row,definition)});toast("Action requested")}));
  row.append(label);
  if(target==="server")for(const [key,spec]of Object.entries(definition.arguments||{})){const field=document.createElement("label");field.textContent=key+(spec.required?" *":"");const input=spec.type==="enum"?choice(["",...spec.values],spec.default??""):document.createElement("input");input.dataset.argument=key;if(spec.type!=="enum")input.value=spec.default??"";if(["number","duration"].includes(spec.type))input.type="number";field.append(input);row.append(field)}
  row.append(button);container.append(row);
 }
}
async function loadSpeaker(){
 speakerConfig=await freshSpeaker();$("activeColor").value=hex(speakerConfig.active_rgb||[0,32,32]);$("idleColor").value=hex(speakerConfig.idle_rgb);
 $("bindingRows").replaceChildren();for(const [key,action]of Object.entries(speakerConfig.bindings||{})){const [button,gesture]=key.split(".");bindingRow(button,gesture,action)}
 $("conditionalRuleRows").replaceChildren();for(const key of Object.keys(gestureGroups))delete gestureGroups[key];const rulesByGesture={...(speakerConfig.conditional_bindings||{})};for(const [key,rules]of Object.entries(rulesByGesture))for(const rule of rules)conditionalRuleRow(key,rule);$("repeatDelay").value=speakerConfig.repeat_delay_ms||500;$("repeatInterval").value=speakerConfig.repeat_interval_ms||150;
 const selected=$("faceSelect").value;$("faceSelect").replaceChildren(option("","New animation"));
 for(const [name,entries]of Object.entries(speakerConfig.faces||{}))if(entries.length<=2)$("faceSelect").append(option(name));
 $("faceSelect").value=selected;
 $("speakerActions").replaceChildren();
 const raw=await api("/ui/speaker-config-yaml");$("speakerYaml").value=raw.yaml;
}
async function loadServerActions(){serverActions=(await api("/ui/actions")).actions;$("httpExisting").replaceChildren(option("","New action"),...Object.entries(serverActions).filter(([,a])=>a.type==="http").map(([name])=>option(name)));$("serverActions").replaceChildren();$("actionsYaml").value=(await api("/ui/actions-yaml")).yaml}
async function loadVoice(){
 voiceConfig=await api("/ui/voice-config");
 $("wakePhrase").value=voiceConfig.wake_word;document.querySelectorAll(".assistantName").forEach(el=>el.textContent=voiceConfig.wake_word);
 $("assistantFace").replaceChildren(option("","Clock only"),...Object.keys(speakerConfig.faces||{}).map(name=>option(name)));$("assistantFace").value=voiceConfig.assistant_face??"eyes";
 $("micChannel").value=voiceConfig.microphone_channel??0;$("maxListen").value=voiceConfig.max_listen_seconds??8;

$("voiceChoice").value=voiceConfig.tts_speaker||"baya";$("speechLevel").value=Math.round((voiceConfig.tts_volume??.65)*100);$("speechLevelValue").textContent=$("speechLevel").value+"%";$("voiceLanguage").value=voiceConfig.speech_language||"ru";
 $("recognitionModel").value=voiceConfig.whisper_model||"base";
 $("micGain").value=voiceConfig.microphone_gain??4;$("gainValue").textContent=$("micGain").value+"×";
 $("speechThreshold").value=voiceConfig.speech_threshold??80;$("thresholdValue").textContent=$("speechThreshold").value;
 $("silenceSeconds").value=voiceConfig.silence_seconds??.65;$("autoThreshold").checked=voiceConfig.auto_threshold??true;
 await loadCatalog();
}
async function poll(){try{await refreshState();await refreshVoice();if(weatherTick++%12===0)await refreshWeather();if(!$("voice").hidden)await refreshRecordings()}catch(error){$("connection").textContent="Connection unavailable";$("banner").textContent=error.message+" — local speaker functions can continue independently.";$("banner").hidden=false}}
async function connect(){
 await refreshState();sessionStorage.setItem("mahi-token",token);$("login").hidden=true;$("app").hidden=false;
 await loadServerActions();await loadSpeaker();await loadVoice();await refreshVoice();await refreshWeather();
 clearInterval(pollTimer);pollTimer=setInterval(poll,2500);
 clearInterval(testPoll);testPoll=setInterval(()=>{if(!$("voice").hidden)refreshTest()},700);
}
$("loginForm").addEventListener("submit",event=>{event.preventDefault();token=$("token").value.trim();busy(event.submitter,async()=>{try{await connect()}catch(error){$("loginError").textContent=error.message;$("login").hidden=false;$("app").hidden=true}})});
bind("logout",()=>{if(testing)api("/ui/listen-test",{enabled:false}).catch(()=>{});token="";sessionStorage.removeItem("mahi-token");clearInterval(pollTimer);clearInterval(testPoll);stopPreview();$("app").hidden=true;$("login").hidden=false});
document.querySelectorAll("[data-page]").forEach(button=>button.addEventListener("click",()=>{document.querySelectorAll(".page").forEach(page=>page.hidden=page.id!==button.dataset.page);document.querySelectorAll("[data-page]").forEach(b=>b.classList.toggle("selected",b===button));if(button.dataset.page!=="display")stopPreview();if(button.dataset.page!=="voice"&&testing)stopTest().catch(()=>{})}));
document.querySelectorAll("[data-control]").forEach(button=>button.addEventListener("click",()=>busy(button,async()=>{await command(JSON.parse(button.dataset.control));toast("Done")})));
$("volume").addEventListener("input",()=>{$("volumeValue").textContent=$("volume").value+"%"});
$("volume").addEventListener("change",()=>busy(null,()=>command(["volume","set",$("volume").value])));
bind("muteToggle",async()=>{await command(["mute",latestState.mic_muted?"unmute":"mute"])});
bind("refresh",async()=>{await poll();toast("Updated")});
bind("timerStart",async()=>{const seconds=Number($("timerMinutes").value)*60+Number($("timerSeconds").value);if(!Number.isInteger(seconds)||seconds<1||seconds>5999)throw new Error("Timer must be 1 second to 99m59s");await command(["timer","start",String(seconds)])});
bind("saveAlert",async()=>{await command(["timer","sound-level",$("alertLevel").value]);toast("Alert level saved")});
bind("weatherRefresh",async()=>{await command(["action","run","weather_refresh"]);toast("Forecast refresh requested");setTimeout(refreshWeather,3000)});
$("faceSelect").addEventListener("change",()=>{const name=$("faceSelect").value;if(!name)return;const face=speakerConfig.faces[name];frames=[face[0].segments.slice(),(face[1]||face[0]).segments.slice()];$("frameMs0").value=face[0].ms||1200;$("frameMs1").value=(face[1]||face[0]).ms||150;$("faceName").value=name;renderEditors()});
bind("copyFrame",()=>{frames[1]=frames[0].slice();renderEditors()});
bind("clearFrames",()=>{frames=[["","","",""],["","","",""]];renderEditors()});
bind("previewAnimation",startPreview);
bind("saveFace",async()=>{
 const name=$("faceName").value.trim();if(!name||name.length>60)throw new Error("Give the face a name, up to 60 characters");
 const entries=frames.map((segments,index)=>{const ms=Number($("frameMs"+index).value);if(!Number.isInteger(ms)||ms<100||ms>30000)throw new Error("Frame duration must be 100..30000 ms");return{segments:segments.slice(),ms}});
 const seconds=$("faceSeconds").value,config=await freshSpeaker();config.faces??={};config.actions??={};
 config.faces[name]=entries;config.actions["face_"+name]={type:"clock",args:["face",name,seconds]};
 await saveSpeaker(config);await command(["clock","face",name,seconds]);$("faceSelect").value=name;toast("Animation saved and shown");
});
bind("showText",async()=>{await command(["clock","text",$("clockText").value]);toast("Text shown")});
bind("saveClockBrightness",async()=>{await command(["brightness","clock",$("clockAuto").checked?"auto":$("clockBrightness").value]);toast("Clock brightness saved")});
bind("saveRingBrightness",async()=>{await command(["brightness","ring",$("ringAuto").checked?"auto":$("ringBrightness").value]);toast("Ring brightness saved")});
bind("saveRingColors",async()=>{const config=await freshSpeaker();config.active_rgb=rgb($("activeColor").value);config.idle_rgb=rgb($("idleColor").value);await saveSpeaker(config)});
bind("testRing",async()=>{await command(["ring","animate",$("ringAnimation").value,$("ringSeconds").value])});
$("speechLevel").addEventListener("input",()=>{$("speechLevelValue").textContent=$("speechLevel").value+"%"});
bind("voicePreview",async()=>{await api("/ui/voice-preview",{speaker:$("voiceChoice").value});toast("Voice sample queued")});
bind("saveVoice",async()=>{
 await api("/ui/voice-config",{wake_word:$("wakePhrase").value,assistant_face:$("assistantFace").value,microphone_channel:Number($("micChannel").value),max_listen_seconds:Number($("maxListen").value),tts_speaker:$("voiceChoice").value,tts_volume:Number($("speechLevel").value)/100,speech_language:$("voiceLanguage").value,whisper_model:$("recognitionModel").value,microphone_gain:Number($("micGain").value),speech_threshold:Number($("speechThreshold").value),silence_seconds:Number($("silenceSeconds").value),auto_threshold:$("autoThreshold").checked});
 await loadVoice();toast("Voice settings saved; apply when idle");setTimeout(refreshVoice,1500);
});
bind("addBinding",()=>bindingRow());
bind("addConditionalRule",()=>conditionalRuleRow());
bind("saveBindings",async()=>{
 const bindings={};for(const row of document.querySelectorAll(".binding-row")){const [button,gesture,action]=[...row.querySelectorAll("select")].map(input=>input.value);const key=button+"."+gesture;if(bindings[key])throw new Error("Duplicate binding: "+key);bindings[key]=action}
 const conditional_bindings={};for(const row of document.querySelectorAll(".conditional-rule-row")){const [key,when,action]=[...row.querySelectorAll("select")].map(input=>input.value);if(!key||!when||!action)throw new Error("Choose a gesture, condition, and action for every rule");(conditional_bindings[key]??=[]).push({when,action,consume:row.querySelector('input[type="checkbox"]').checked})}
 const config=await freshSpeaker();config.bindings=bindings;config.conditional_bindings=conditional_bindings;config.repeat_delay_ms=Number($("repeatDelay").value);config.repeat_interval_ms=Number($("repeatInterval").value);await saveSpeaker(config);
});
bind("saveActions",async()=>{await api("/ui/actions-yaml",{yaml:$("actionsYaml").value});await loadServerActions();toast("Server actions saved")});
bind("saveSpeakerYaml",async()=>{await api("/ui/speaker-config-yaml",{yaml:$("speakerYaml").value});await loadSpeaker();toast("Speaker configuration saved")});
$("micGain").addEventListener("input",()=>{$("gainValue").textContent=$("micGain").value+"×"});
$("speechThreshold").addEventListener("input",()=>{$("thresholdValue").textContent=$("speechThreshold").value});
bind("startListen",async()=>{$("heardText").value="";lastHeardAt=0;await api("/ui/listen-test",{enabled:true});await refreshTest();toast("Listen test active; commands are paused")});
bind("stopListen",async()=>{await stopTest();toast("Normal voice commands restored")});
window.addEventListener("pagehide",()=>{if(testing&&token)fetch("/ui/listen-test",{method:"POST",headers:{"Authorization":"Bearer "+token,"Content-Type":"application/json"},body:JSON.stringify({enabled:false}),keepalive:true}).catch(()=>{})});
renderEditors();renderSegments($("liveClock"),["","","",""]);
if(token)connect().catch(error=>{$("loginError").textContent=error.message;$("login").hidden=false;$("app").hidden=true});

function readActionArgs(row,definition){const args={};for(const input of row.querySelectorAll("[data-argument]")){const key=input.dataset.argument,spec=definition.arguments[key];if(input.value===""){if(spec.required&&spec.default===undefined)throw new Error("Specify "+key);continue}args[key]=["number","duration"].includes(spec.type)?Number(input.value):input.value}return args}
async function refreshRecordings(){const data=await api("/ui/recordings");$("recordStart").disabled=data.recording;$("recordStop").disabled=!data.recording;$("comparisonResults").textContent=data.comparison?JSON.stringify(data.comparison,null,2):"";$("recordingRows").replaceChildren();for(const entry of data.recordings){const row=document.createElement("div"),label=document.createElement("span");label.textContent=`${new Date(entry.created*1000).toLocaleString()} · ${entry.seconds}s · ${entry.expected}`;row.append(label);for(const op of ["replay","compare","delete"]){const button=document.createElement("button");button.textContent=op;button.onclick=()=>busy(button,async()=>{const result=await api("/ui/recordings",{operation:op,id:entry.id});if(op==="replay"){$("recordingAudio").src="data:audio/wav;base64,"+result.audio;await $("recordingAudio").play()}await refreshRecordings()});row.append(button)}$("recordingRows").append(row)}}
bind("recordStart",async()=>{await api("/ui/recordings",{operation:"start",expected:$("expectedText").value});await refreshRecordings()});
bind("recordStop",async()=>{await api("/ui/recordings",{operation:"stop"});await refreshRecordings()});bind("recordRefresh",refreshRecordings);
for(const key of ["query","headers","secret_headers","json","arguments","results"]){const label=document.createElement("label");label.textContent=key;const input=document.createElement("textarea");input.id="http_"+key;input.value="{}";label.append(input);$("httpFields").append(label)}
bind("saveHttp",async()=>{const name=$("httpName").value.trim();if(!name)throw new Error("Action name required");const definition={type:"http",url:$("httpUrl").value,method:$("httpMethod").value};for(const key of ["query","headers","secret_headers","arguments","results"])definition[key]=JSON.parse($("http_"+key).value);if(definition.method==="POST")definition.json=JSON.parse($("http_json").value);await api("/ui/http-action",{name,action:definition});await loadServerActions();toast("HTTP action saved")});

$("httpExisting").addEventListener("change",()=>{const name=$("httpExisting").value,a=serverActions[name];if(!a)return;$("httpName").value=name;$("httpMethod").value=a.method||"GET";$("httpUrl").value=a.url;for(const key of ["query","headers","secret_headers","json","arguments","results"])$("http_"+key).value=JSON.stringify(a[key]||{},null,2)});

let catalogEntries={};
async function loadCatalog(){
 const data=await api("/ui/catalog");catalogEntries=data.entries;$("defaultResponse").value=data.default_response??"Готово.";$("defaultDisplaySeconds").value=data.display_seconds||3;$("actionCatalog").replaceChildren();
 for(const [key,entry] of Object.entries(catalogEntries)){
  const row=document.createElement("details");row.className="catalog-entry";row.dataset.catalog=key;
  const heading=document.createElement("summary");heading.textContent=key;row.append(heading);
  const [target,name]=[key.slice(0,key.indexOf(":")),key.slice(key.indexOf(":")+1)];
  if(target==="server")for(const [argument,spec]of Object.entries(serverActions[name]?.arguments||{})){
   const label=document.createElement("label");label.textContent=argument+(spec.required?" *":"");const input=spec.type==="enum"?choice(["",...spec.values],spec.default??""):document.createElement("input");input.dataset.argument=argument;if(spec.type!=="enum")input.value=spec.default??"";if(["number","duration"].includes(spec.type))input.type="number";label.append(input);row.append(label)
  }
  const fields=[["phrases","Voice phrases",(entry.phrases||[]).join("\n")],["responses","Response variants",(entry.responses||[""]).join("\n")]];
  for(const [name,labelText,value] of fields){const label=document.createElement("label");label.textContent=labelText;const input=document.createElement("textarea");input.dataset.field=name;input.value=value;input.rows=3;if(name==="responses")input.placeholder="Use default response";label.append(input);row.append(label)}
  const label=document.createElement("label");label.textContent="Temporary display seconds (blank: use default)";const timeout=document.createElement("input");timeout.type="number";timeout.min=1;timeout.max=86400;timeout.dataset.field="display_seconds";timeout.value=entry.display_seconds??"";label.append(timeout);row.append(label);

  if(target!=="feedback") {const run=document.createElement("button");run.textContent="Run";run.onclick=event=>{event.preventDefault();event.stopPropagation();return busy(run,async()=>{
   if(target==="speaker")await command(["action","run",name]);
   else if(target==="server")await api("/run",{action:name,args:readActionArgs(row,serverActions[name]||{})})
   else {const args={timer_start:["timer","pick"],timer_cancel:["timer","cancel"],timer_status:["timer","status"],weather:["weather","show"],time:["clock","clear"],volume_down:["volume","down"],volume_up:["volume","up"]};await command(args[name])}
   toast("Action requested")})};heading.append(run)}
  const bound=Object.entries(speakerConfig.bindings||{}).filter(([,action])=>target==="speaker"&&action===name).map(([key])=>key);if(bound.length){const info=document.createElement("p");info.textContent="Buttons: "+bound.join(", ");row.append(info)}
  $("actionCatalog").append(row);
 }
}
bind("saveCatalog",async()=>{
 const entries={};for(const row of document.querySelectorAll("[data-catalog]")){const field=name=>row.querySelector(`[data-field="${name}"]`);entries[row.dataset.catalog]={phrases:field("phrases").value.split("\n").map(x=>x.trim()).filter(Boolean),responses:field("responses").value.split("\n"),display_seconds:field("display_seconds").value===""?null:Number(field("display_seconds").value)}}
 await api("/ui/catalog",{entries,default_response:$("defaultResponse").value,display_seconds:Number($("defaultDisplaySeconds").value)});await loadCatalog();toast("Action catalog saved")
});

$("catalogSearch").addEventListener("input",event=>{
 const query=event.target.value.trim().toLowerCase();
 for(const row of document.querySelectorAll("[data-catalog]"))row.hidden=![row.dataset.catalog,...(catalogEntries[row.dataset.catalog]?.phrases||[])].join(" ").toLowerCase().includes(query);
});
