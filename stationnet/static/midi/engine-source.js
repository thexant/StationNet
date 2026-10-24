/* Reusable, local SoundFont MIDI renderer. The host owns the playlist clock. */
import { Sequencer, WorkletSynthesizer } from "spessasynth_lib";
import { BasicMIDI, SoundBankLoader, SpessaSynthProcessor, SpessaSynthSequencer } from "spessasynth_core";

export class MidiPlaybackEngine {
  constructor(context, destination, assetBase, onEnded = () => {}, options = {}) {
    this.context = context;
    this.destination = destination;
    this.assetBase = assetBase.replace(/\/$/, "");
    this.onEnded = onEnded;
    this.generation = 0;
    this.key = null;
    this.target = 0;
    this.ready = null;
    this.preferScriptProcessor = Boolean(options.preferScriptProcessor);
  }

  async initialize() {
    if (!this.ready) this.ready = (async () => {
      const fontResponse = await fetch(`${this.assetBase}/gm.sf2`);
      if (!fontResponse.ok) throw new Error("The bundled General MIDI SoundFont is unavailable.");
      const font = await fontResponse.arrayBuffer();
      if (!this.context.audioWorklet || (this.preferScriptProcessor && this.context.createScriptProcessor)) {
        await this.initializeScriptProcessor(font);
        return;
      }
      try {
        await this.context.audioWorklet.addModule(`${this.assetBase}/spessasynth_processor.min.js`);
      } catch (problem) {
        if (!this.context.createScriptProcessor) throw problem;
        await this.initializeScriptProcessor(font);
        return;
      }
      this.synth = new WorkletSynthesizer(this.context);
      this.gain = this.context.createGain();
      this.synth.connect(this.gain);
      this.gain.connect(this.destination);
      await this.synth.soundBankManager.addSoundBank(font, "main");
      this.sequencer = new Sequencer(this.synth, { skipToFirstNoteOn: false });
      this.sequencer.loopCount = 0;
      this.sequencer.eventHandler.addEvent("songEnded", "stationnet-midi-ended", () => {
        if (this.key) this.onEnded(this.key);
      });
    })().catch(error => { this.ready = null; throw error; });
    return this.ready;
  }

  async initializeScriptProcessor(font) {
    if (!this.context.createScriptProcessor)
      throw new Error("This browser cannot synthesize MIDI audio.");
    const processor = new SpessaSynthProcessor(this.context.sampleRate, { eventsEnabled: false });
    processor.soundBankManager.addSoundBank(SoundBankLoader.fromArrayBuffer(font), "main");
    await processor.processorInitialized;
    const sequencer = new SpessaSynthSequencer(processor);
    sequencer.skipToFirstNoteOn = false;
    sequencer.loopCount = 0;
    const events = new Map();
    sequencer.onEventCall = event => {
      for (const [name, listeners] of events)
        if (name === event.type) for (const listener of listeners.values()) listener(event.data);
      if (event.type === "songEnded" && this.key) this.onEnded(this.key);
    };
    const node = this.context.createScriptProcessor(2048, 0, 2);
    node.onaudioprocess = event => {
      if (!this.key) return;
      const left = event.outputBuffer.getChannelData(0);
      const right = event.outputBuffer.getChannelData(1);
      for (let offset = 0; offset < left.length; offset += 128) {
        sequencer.processTick();
        processor.process(left, right, offset, Math.min(128, left.length - offset));
      }
    };
    this.gain = this.context.createGain();
    node.connect(this.gain);
    this.gain.connect(this.destination);
    this.synth = { stopAll: force => processor.stopAllChannels(force) };
    this.sequencer = {
      get midiData() { return sequencer.midiData; },
      get currentTime() { return sequencer.currentTime; },
      set currentTime(value) { sequencer.currentTime = value; },
      get paused() { return sequencer.paused; },
      get isFinished() { return sequencer.isFinished; },
      pause: () => sequencer.pause(),
      play: () => sequencer.play(),
      loadNewSongList: songs => sequencer.loadNewSongList(
        songs.map(song => BasicMIDI.fromArrayBuffer(song.binary))),
      eventHandler: {
        addEvent: (name, id, listener) => {
          if (!events.has(name)) events.set(name, new Map());
          events.get(name).set(id, listener);
        },
        removeEvent: (name, id) => events.get(name)?.delete(id),
      },
    };
  }

  stop() {
    this.generation++;
    this.abort?.abort();
    this.abort = null;
    this.cancelLoad?.();
    this.cancelLoad = null;
    this.key = null;
    this.loadingKey = null;
    if (this.sequencer) this.sequencer.pause();
    if (this.synth) this.synth.stopAll(true);
  }

  async loadBinary(binary) {
    this.stop();
    const generation = this.generation;
    await this.initialize();
    if (generation !== this.generation) return;
    this.key = `local-${generation}`;
    this.sequencer.pause();
    this.synth.stopAll(true);
    await new Promise((resolve, reject) => {
      const listener = `stationnet-local-${generation}`;
      const timeout = setTimeout(() => {
        this.sequencer.eventHandler.removeEvent("songChange", listener);
        reject(new Error("MIDI load timed out."));
      }, 15000);
      this.sequencer.eventHandler.addEvent("songChange", listener, () => {
        clearTimeout(timeout);
        this.sequencer.eventHandler.removeEvent("songChange", listener);
        resolve();
      });
      this.sequencer.loadNewSongList([{ binary }]);
    });
    if (generation === this.generation) this.sequencer.currentTime = 0;
  }

  play() { if (this.key && this.sequencer) this.sequencer.play(); }
  pause() { if (this.sequencer) { this.sequencer.pause(); this.synth?.stopAll(true); } }
  seek(seconds) { if (this.sequencer?.midiData) { this.synth?.stopAll(true); this.sequencer.currentTime = Math.max(0, Number(seconds) || 0); } }
  get position() { return this.sequencer?.currentTime || 0; }

  async sync(key, url, offset, ducking = 1) {
    this.target = Math.max(0, Number(offset) || 0);
    if (this.gain) this.gain.gain.setTargetAtTime(ducking, this.context.currentTime, .03);
    if (this.key === key && this.loadingKey === key) return this.pending;
    if (this.key === key && this.sequencer?.midiData) {
      if (Math.abs(this.sequencer.currentTime - this.target) > 1.5)
        this.sequencer.currentTime = this.target;
      if (this.sequencer.paused && !this.sequencer.isFinished) this.sequencer.play();
      return;
    }
    if (this.key === key) return;
    const previous = this.pending;
    this.stop();
    this.key = key;
    this.loadingKey = key;
    const generation = this.generation;
    const load = async () => {
      if (previous) await previous.catch(() => {});
      if (generation !== this.generation) return;
      await this.initialize();
      if (generation !== this.generation) return;
      this.abort = new AbortController();
      const response = await fetch(url, { credentials: "same-origin", signal: this.abort.signal });
      if (!response.ok) throw new Error(`MIDI download failed (${response.status}).`);
      const binary = await response.arrayBuffer();
      if (generation !== this.generation) return;
      this.sequencer.pause();
      this.synth.stopAll(true);
      await new Promise((resolve, reject) => {
        const listener = `stationnet-load-${generation}`;
        const finish = () => {
          clearTimeout(timeout);
          this.sequencer.eventHandler.removeEvent("songChange", listener);
          if (this.cancelLoad === finish) this.cancelLoad = null;
          resolve();
        };
        const timeout = setTimeout(() => {
          this.sequencer.eventHandler.removeEvent("songChange", listener);
          this.cancelLoad = null;
          reject(new Error("MIDI load timed out."));
        }, 15000);
        this.cancelLoad = finish;
        this.sequencer.eventHandler.addEvent("songChange", listener, finish);
        this.sequencer.loadNewSongList([{ binary }]);
      });
      if (generation !== this.generation) return;
      this.sequencer.currentTime = this.target;
      this.sequencer.play();
      this.gain.gain.setTargetAtTime(ducking, this.context.currentTime, .03);
    };
    this.pending = load().finally(() => {
      if (generation === this.generation) {
        this.loadingKey = null;
        this.pending = null;
      }
    });
    return this.pending;
  }
}
