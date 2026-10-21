"use strict";

for(const radio of document.querySelectorAll("[data-radio]")){
  const find=name=>radio.querySelector(`[data-radio-${name}]`);
  const csrf=radio.dataset.csrf;
  const input=find("input"), display=find("display"), compact=find("frequency");
  const bandReadout=find("band"), status=find("status"), monitorStatus=find("monitor-status");
  const lamp=find("lamp"), occupancy=find("occupancy"), error=find("error");
  const mode=find("mode"), cwPanel=find("cw"), sstvPanel=find("sstv");
  const cwButton=find("cw-tx"), key=find("key"), toneInput=find("tone"), wpmInput=find("wpm");
  const decoder=find("decoder"), decoderOutput=find("decoder-output");
  const imageInput=find("image"), sstvButton=find("sstv-tx"), saveButton=find("sstv-save");
  const txCanvas=find("tx-picture"), rxCanvas=find("rx-picture"), rxCaption=find("rx-caption");
  const volume=find("volume"), mute=find("mute"), scope=find("scope");

  let frequency=null, currentBand=null, cursor=0, receiving=false, transmissionId=null;
  let transmissionMode=null, heartbeat=null, keyIsDown=false, localKey=null;
  let context=null, analyser=null, outputGain=null, squelchSource=null, muted=false;
  let receiveNodes=new Set(), rxOscillator=null, rxKeyStarted=0, rxAudioTime=0, tuneGeneration=0;
  let preparedImage=null, receivedRows=0, expectedRows=0;
  let decodeMarks="", decodedText="", decodeTimer=null;

  const morseDecode={".-":"A","-...":"B","-.-.":"C","-..":"D",".":"E","..-.":"F","--.":"G","....":"H","..":"I",".---":"J","-.-":"K",".-..":"L","--":"M","-.":"N","---":"O",".--.":"P","--.-":"Q",".-.":"R","...":"S","-":"T","..-":"U","...-":"V",".--":"W","-..-":"X","-.--":"Y","--..":"Z","-----":"0",".----":"1","..---":"2","...--":"3","....-":"4",".....":"5","-....":"6","--...":"7","---..":"8","----.":"9"};

  const setStatus=(label,active=false)=>{
    status.textContent=label;
    monitorStatus.textContent=label;
    lamp.classList.toggle("active",active);
  };
  const showError=problem=>{error.textContent=problem?.message||String(problem||"");};
  const post=async(url,values)=>{
    const data=new FormData(); data.set("csrf_token",csrf);
    for(const [name,value] of Object.entries(values))data.set(name,value);
    const response=await fetch(url,{method:"POST",body:data,headers:{Accept:"application/json"}});
    const payload=await response.json();
    if(!response.ok)throw new Error(payload.error||"Radio operation failed.");
    return payload;
  };
  const permissionLabel=band=>band.tx_permission==="receive"?"RX ONLY":band.tx_permission==="admins"?"SYSOP TX":"OPEN TX";
  const formatKhz=khz=>`${Math.floor(khz/1000)}.${String(khz%1000).padStart(3,"0")}`;
  const parseKhz=value=>{
    const match=/^(\d{1,3})\.(\d{3})$/.exec(String(value).trim());
    if(!match)return null;
    const result=Number(match[1])*1000+Number(match[2]);
    return result>=100&&result<=999999?result:null;
  };

  const ensureAudio=()=>{
    if(!context){
      const Audio=window.AudioContext||window.webkitAudioContext;
      if(!Audio)return null;
      context=new Audio();
      analyser=context.createAnalyser(); analyser.fftSize=2048;
      outputGain=context.createGain();
      analyser.connect(outputGain).connect(context.destination);
      updateVolume();
    }
    if(context.state==="suspended")context.resume().catch(()=>{});
    return context;
  };
  const updateVolume=()=>{
    if(outputGain)outputGain.gain.setTargetAtTime(muted?0:(Number(volume.value)/100)*.35,context.currentTime,.01);
  };
  const connectSignal=node=>{node.connect(analyser); receiveNodes.add(node); node.addEventListener?.("ended",()=>receiveNodes.delete(node)); return node;};
  const stopReception=()=>{
    for(const node of receiveNodes){try{node.stop()}catch(_){}}
    receiveNodes.clear(); rxOscillator=null; rxAudioTime=0; tuneGeneration++;
    setStatus("IDLE");
  };
  const playSquelch=()=>{
    const audio=ensureAudio(); if(!audio||audio.state!=="running"||muted)return;
    try{squelchSource?.stop()}catch(_){}
    const duration=.13, buffer=audio.createBuffer(1,Math.floor(audio.sampleRate*duration),audio.sampleRate);
    const data=buffer.getChannelData(0);
    for(let i=0;i<data.length;i++)data[i]=(Math.random()*2-1)*(1-i/data.length);
    const source=audio.createBufferSource(), filter=audio.createBiquadFilter(), gain=audio.createGain();
    filter.type="bandpass"; filter.frequency.value=1700; filter.Q.value=.65; gain.gain.value=.38;
    source.buffer=buffer; source.connect(filter).connect(gain).connect(analyser); source.start(); squelchSource=source;
    source.onended=()=>{if(squelchSource===source)squelchSource=null};
  };
  const oscillatorOn=(hz,local=false)=>{
    const audio=ensureAudio(); if(!audio||audio.state!=="running")return null;
    const oscillator=audio.createOscillator(), gain=audio.createGain();
    oscillator.frequency.value=hz; gain.gain.setValueAtTime(.0001,audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(local ? .20 : .28,audio.currentTime+.008);
    oscillator.connect(gain).connect(analyser); oscillator.start();
    oscillator._radioGain=gain; receiveNodes.add(oscillator);
    oscillator.addEventListener("ended",()=>receiveNodes.delete(oscillator)); return oscillator;
  };
  const oscillatorOff=(oscillator,delayMs=0)=>{
    if(!oscillator||!context)return;
    try{const stopAt=context.currentTime+Math.max(0,delayMs)/1000;oscillator._radioGain.gain.setTargetAtTime(.0001,stopAt,.008);oscillator.stop(stopAt+.04)}catch(_){}
  };

  const drawScope=()=>{
    const drawing=scope.getContext("2d"), width=scope.width, height=scope.height;
    drawing.fillStyle="#000"; drawing.fillRect(0,0,width,height);
    drawing.strokeStyle="#073b23"; drawing.lineWidth=1;
    drawing.beginPath(); for(let x=0;x<width;x+=45){drawing.moveTo(x,0);drawing.lineTo(x,height)}
    for(let y=0;y<height;y+=25){drawing.moveTo(0,y);drawing.lineTo(width,y)} drawing.stroke();
    drawing.strokeStyle="#35ff8a"; drawing.shadowColor="#18c968"; drawing.shadowBlur=5; drawing.beginPath();
    if(analyser){
      const values=new Uint8Array(analyser.fftSize); analyser.getByteTimeDomainData(values);
      for(let i=0;i<values.length;i++){
        const x=i/(values.length-1)*width, y=(values[i]/255)*height;
        i?drawing.lineTo(x,y):drawing.moveTo(x,y);
      }
    }else{drawing.moveTo(0,height/2);drawing.lineTo(width,height/2)}
    drawing.stroke(); drawing.shadowBlur=0; requestAnimationFrame(drawScope);
  };
  drawScope();
  document.addEventListener("pointerdown",ensureAudio,{once:true,capture:true});

  const endTransmission=async(silent=false)=>{
    if(!transmissionId)return;
    const ending=transmissionId; transmissionId=null; transmissionMode=null;
    clearInterval(heartbeat); heartbeat=null; key.disabled=true; cwButton.textContent="BEGIN CW TRANSMISSION";
    if(keyIsDown){keyIsDown=false;oscillatorOff(localKey);localKey=null}
    try{await post(radio.dataset.endUrl,{transmission_id:ending})}catch(problem){if(!silent)showError(problem)}
    setStatus("IDLE");
  };
  const applyFrequency=(value,band,newCursor,withSquelch=true)=>{
    frequency=value; currentBand=band; cursor=Math.max(0,Number(newCursor)||0);
    input.value=value; display.textContent=value; compact.textContent=value;
    bandReadout.textContent=`${band.name} / ${permissionLabel(band)}`;
    occupancy.textContent="FREQUENCY CLEAR"; localStorage.setItem("stationnet.radio.frequency",value);
    stopReception(); if(withSquelch)playSquelch();
  };
  const tune=async value=>{
    error.textContent=""; const parsed=parseKhz(value);
    if(parsed===null){showError("Use a numeric MHz frequency with exactly three decimal places.");input.value=frequency||"";return false}
    stopReception(); await endTransmission(true); setStatus("TUNING");
    try{const payload=await post(radio.dataset.tuneUrl,{frequency:formatKhz(parsed)});applyFrequency(payload.frequency,payload.band,payload.cursor);return true}
    catch(problem){showError(problem);input.value=frequency||"";setStatus("TUNE ERROR");return false}
  };

  const flushDecode=(wordGap=false)=>{
    if(decodeMarks){decodedText+=(morseDecode[decodeMarks]||"?");decodeMarks=""}
    if(wordGap&&decodedText&&!decodedText.endsWith(" "))decodedText+=" ";
    decoderOutput.textContent=(decodedText||"...").slice(-48);
  };
  const decodeEvent=event=>{
    if(!decoder.checked)return;
    const dot=1200/Math.max(5,Number(wpmInput.value)||18);
    if(event.kind==="key_down"&&event.gap_ms!=null){
      if(event.gap_ms>=dot*6)flushDecode(true); else if(event.gap_ms>=dot*2)flushDecode(false);
    }
    if(event.kind==="key_up"){
      decodeMarks+=event.duration_ms<dot*2?".":"-";
      clearTimeout(decodeTimer); decodeTimer=setTimeout(()=>flushDecode(false),dot*3.2);
    }
    if(event.kind==="end")flushDecode(false);
  };
  const decodePixels=value=>{
    const binary=atob(value), pixels=new Uint8ClampedArray(binary.length);
    for(let i=0;i<binary.length;i++)pixels[i]=binary.charCodeAt(i); return pixels;
  };
  const encodePixels=pixels=>{
    let binary=""; for(let i=0;i<pixels.length;i++)binary+=String.fromCharCode(pixels[i]); return btoa(binary);
  };
  const playScanline=(pixels,generation=tuneGeneration)=>{
    const audio=ensureAudio(); if(!audio||audio.state!=="running"||generation!==tuneGeneration)return;
    const samplesPerPixel=Math.max(20,Math.floor(audio.sampleRate*.09/(pixels.length/3)));
    const buffer=audio.createBuffer(1,samplesPerPixel*(pixels.length/3),audio.sampleRate), data=buffer.getChannelData(0);
    let phase=0;
    for(let pixel=0;pixel<pixels.length/3;pixel++){
      const r=pixels[pixel*3],g=pixels[pixel*3+1],b=pixels[pixel*3+2];
      const frequencyHz=550+(r*.299+g*.587+b*.114)*6.1;
      for(let sample=0;sample<samplesPerPixel;sample++){
        phase+=2*Math.PI*frequencyHz/audio.sampleRate; data[pixel*samplesPerPixel+sample]=Math.sin(phase)*.42;
      }
    }
    const source=connectSignal(audio.createBufferSource()); source.buffer=buffer;
    rxAudioTime=Math.max(audio.currentTime+.01,rxAudioTime); source.start(rxAudioTime); rxAudioTime+=buffer.duration;
  };
  const drawReceivedLine=event=>{
    const pixels=decodePixels(event.pixels), width=pixels.length/3;
    if(rxCanvas.width!==width){rxCanvas.width=width;rxCanvas.height=Math.max(expectedRows||60,event.row+1)}
    const rgba=new Uint8ClampedArray(width*4);
    for(let i=0;i<width;i++){rgba[i*4]=pixels[i*3];rgba[i*4+1]=pixels[i*3+1];rgba[i*4+2]=pixels[i*3+2];rgba[i*4+3]=255}
    rxCanvas.getContext("2d").putImageData(new ImageData(rgba,width),0,event.row);
    receivedRows=Math.max(receivedRows,event.row+1); rxCaption.textContent=`RX PICTURE / LINE ${receivedRows}${expectedRows?` OF ${expectedRows}`:""}`;
    playScanline(pixels);
  };
  const handleEvent=event=>{
    if(event.kind==="start"){
      occupancy.textContent=`BUSY / ${event.callsign} / ${event.mode}`;
      if(!event.mine)setStatus("RECEIVING",true);
      if(event.mode==="SSTV"&&!event.mine){
        rxCanvas.width=event.width||80;rxCanvas.height=event.height||60;rxCanvas.getContext("2d").fillRect(0,0,rxCanvas.width,rxCanvas.height);
        receivedRows=0;expectedRows=event.height||0;saveButton.disabled=true;
      }
    }else if(event.mode==="CW"){
      if(!event.mine&&event.kind==="key_down"){oscillatorOff(rxOscillator);rxOscillator=oscillatorOn(event.tone_hz||650);rxKeyStarted=performance.now();setStatus("RECEIVING",true)}
      if(!event.mine&&event.kind==="key_up"){const remaining=Math.max(0,(event.duration_ms||0)-(performance.now()-rxKeyStarted));oscillatorOff(rxOscillator,remaining);rxOscillator=null;rxKeyStarted=0}
      if(!event.mine)decodeEvent(event);
    }else if(event.mode==="SSTV"&&event.kind==="scanline"&&!event.mine){drawReceivedLine(event);setStatus("RECEIVING",true)}
    if(event.kind==="end"){
      if(!event.mine){oscillatorOff(rxOscillator);rxOscillator=null;setStatus("IDLE");if(event.mode==="SSTV"&&receivedRows){saveButton.disabled=false;rxCaption.textContent=`RX PICTURE / ${receivedRows} LINES RECEIVED`}}
      occupancy.textContent="FREQUENCY CLEAR";
    }
  };
  const receive=async()=>{
    if(receiving||!frequency)return; receiving=true;
    try{
      const response=await fetch(`${radio.dataset.receiveUrl}?frequency=${encodeURIComponent(frequency)}&after=${cursor}`,{headers:{Accept:"application/json"},cache:"no-store"});
      const payload=await response.json(); if(response.status===401||response.redirected){location.reload();return}
      if(!response.ok)throw new Error(payload.error||"Receiver link interrupted.");
      for(const event of payload.events||[])handleEvent(event);
      cursor=Math.max(cursor,Number(payload.cursor)||0);
      if(payload.active){occupancy.textContent=`BUSY / ${payload.active.callsign} / ${payload.active.mode}`;if(!transmissionId&&status.textContent==="IDLE")setStatus("RECEIVING",true)}
      else if(!transmissionId){occupancy.textContent="FREQUENCY CLEAR";if(status.textContent!=="TUNING")setStatus("IDLE")}
    }catch(problem){if(status.textContent!=="TUNING")setStatus("LINK RETRYING")}
    finally{receiving=false}
  };

  const beginCw=async()=>{
    error.textContent="";
    try{
      const payload=await post(radio.dataset.startUrl,{frequency,mode:"CW",tone_hz:toneInput.value});
      transmissionId=payload.transmission_id;transmissionMode="CW";key.disabled=false;cwButton.textContent="END CW TRANSMISSION";
      occupancy.textContent="BUSY / YOUR CW SIGNAL";setStatus("TRANSMITTING",true);
      heartbeat=setInterval(()=>post(radio.dataset.signalUrl,{transmission_id:transmissionId,kind:"heartbeat"}).catch(()=>endTransmission(true)),2000);
    }catch(problem){showError(problem)}
  };
  const keyDown=async()=>{
    if(keyIsDown||transmissionMode!=="CW")return;keyIsDown=true;error.textContent="";
    try{await post(radio.dataset.signalUrl,{transmission_id:transmissionId,kind:"key_down"});localKey=oscillatorOn(Number(toneInput.value)||650,true);key.classList.add("active");setStatus("TRANSMITTING",true)}
    catch(problem){keyIsDown=false;showError(problem);await endTransmission(true)}
  };
  const keyUp=async()=>{
    if(!keyIsDown)return;keyIsDown=false;oscillatorOff(localKey);localKey=null;key.classList.remove("active");
    try{await post(radio.dataset.signalUrl,{transmission_id:transmissionId,kind:"key_up"})}catch(problem){showError(problem);await endTransmission(true)}
  };
  const sendPicture=async()=>{
    if(!preparedImage)return;error.textContent="";sstvButton.disabled=true;
    try{
      const width=txCanvas.width,height=txCanvas.height;
      const payload=await post(radio.dataset.startUrl,{frequency,mode:"SSTV",width,height});
      transmissionId=payload.transmission_id;transmissionMode="SSTV";setStatus("TRANSMITTING",true);occupancy.textContent="BUSY / YOUR SSTV SIGNAL";
      const rgba=txCanvas.getContext("2d").getImageData(0,0,width,height).data;
      for(let row=0;row<height&&transmissionId;row++){
        const rgb=new Uint8Array(width*3);
        for(let x=0;x<width;x++){const source=(row*width+x)*4,target=x*3;rgb[target]=rgba[source];rgb[target+1]=rgba[source+1];rgb[target+2]=rgba[source+2]}
        playScanline(rgb); await post(radio.dataset.signalUrl,{transmission_id:transmissionId,kind:"scanline",row,pixels:encodePixels(rgb)});
        monitorStatus.textContent=`TRANSMITTING LINE ${row+1}/${height}`; await new Promise(resolve=>setTimeout(resolve,90));
      }
      await endTransmission();
    }catch(problem){showError(problem);await endTransmission(true)}
    finally{sstvButton.disabled=!preparedImage}
  };

  radio.querySelector("[data-radio-toggle]").addEventListener("click",event=>{const expanded=event.currentTarget.getAttribute("aria-expanded")==="true";event.currentTarget.setAttribute("aria-expanded",String(!expanded));find("panel").hidden=expanded});
  input.addEventListener("change",()=>tune(input.value));
  input.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();tune(input.value)}});
  for(const button of radio.querySelectorAll("[data-radio-step]"))button.addEventListener("click",()=>{const khz=parseKhz(frequency||input.value);if(khz!==null)tune(formatKhz(khz+Number(button.dataset.radioStep)))});
  for(const button of radio.querySelectorAll("[data-radio-scan]"))button.addEventListener("click",async()=>{error.textContent="";await endTransmission(true);setStatus("SCANNING");try{const payload=await post(radio.dataset.scanUrl,{frequency,direction:button.dataset.radioScan});if(payload.found)applyFrequency(payload.frequency,payload.band,payload.cursor);else setStatus("NO ACTIVITY")}catch(problem){showError(problem);setStatus("SCAN ERROR")}});
  mode.addEventListener("change",async()=>{await endTransmission(true);const cw=mode.value==="CW";cwPanel.hidden=!cw;sstvPanel.hidden=cw});
  cwButton.addEventListener("click",()=>transmissionId?endTransmission():beginCw());
  key.addEventListener("pointerdown",event=>{event.preventDefault();key.setPointerCapture(event.pointerId);keyDown()});
  key.addEventListener("pointerup",keyUp);key.addEventListener("pointercancel",keyUp);
  document.addEventListener("keydown",event=>{if(event.code==="Space"&&transmissionMode==="CW"&&!(event.target instanceof HTMLInputElement)&&!(event.target instanceof HTMLSelectElement)){event.preventDefault();keyDown()}});
  document.addEventListener("keyup",event=>{if(event.code==="Space"&&keyIsDown){event.preventDefault();keyUp()}});
  window.addEventListener("blur",keyUp);
  volume.addEventListener("input",updateVolume);
  mute.addEventListener("click",()=>{muted=!muted;mute.textContent=`MUTE: ${muted?"ON":"OFF"}`;mute.setAttribute("aria-pressed",String(muted));updateVolume()});
  decoder.addEventListener("change",()=>{decodeMarks="";decodedText="";decoderOutput.textContent=decoder.checked?"LISTENING...":"DECODER OFF"});
  imageInput.addEventListener("change",()=>{
    const file=imageInput.files?.[0];preparedImage=null;sstvButton.disabled=true;if(!file)return;
    if(file.size>5*1024*1024){showError("Choose an image smaller than 5 MB.");return}
    const image=new Image(),url=URL.createObjectURL(file);
    image.onload=()=>{const scale=Math.min(80/image.width,60/image.height,1),width=Math.max(1,Math.round(image.width*scale)),height=Math.max(1,Math.round(image.height*scale));txCanvas.width=width;txCanvas.height=height;txCanvas.getContext("2d").drawImage(image,0,0,width,height);URL.revokeObjectURL(url);preparedImage=true;sstvButton.disabled=false};
    image.onerror=()=>{URL.revokeObjectURL(url);showError("The selected image could not be read.")};image.src=url;
  });
  sstvButton.addEventListener("click",sendPicture);
  saveButton.addEventListener("click",()=>{const link=document.createElement("a");link.download=`stationnet-sstv-${frequency}.png`;link.href=rxCanvas.toDataURL("image/png");link.click()});
  window.addEventListener("beforeunload",()=>{if(transmissionId){const data=new FormData();data.set("csrf_token",csrf);data.set("transmission_id",transmissionId);navigator.sendBeacon(radio.dataset.endUrl,data)}});

  (async()=>{
    try{
      const response=await fetch(radio.dataset.stateUrl,{headers:{Accept:"application/json"},cache:"no-store"}),payload=await response.json();
      const saved=localStorage.getItem("stationnet.radio.frequency");
      if(!saved||!await tune(saved))applyFrequency(payload.frequency,payload.band,payload.cursor,false);
      await receive();
    }catch(_){setStatus("RADIO OFFLINE")}
  })();
  setInterval(receive,100);
}
