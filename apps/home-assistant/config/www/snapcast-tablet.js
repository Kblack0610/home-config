// Snapcast client that runs inside the Home Assistant frontend on a wall
// tablet, so the tablet's speaker becomes a synced Snapcast room. Loaded on
// every HA page via `frontend: extra_module_url`; it only starts on a browser
// whose Voice Satellite assignment is listed in ROOMS. Protocol, clock sync and
// FLAC decoding are ported from snapweb v0.9.2 src/snapstream.ts (GPL-3.0).
// Why this shape, what differs from upstream, and how to debug it:
// docs/wall-panels.md "Snapcast room on a wall panel".

const SNAPSERVER_STREAM_URL = "wss://snapcast.kblab.me/stream";
const LIBFLAC_DIR = "/local/community/libflacjs/";
const LIBFLAC_SCRIPT = "libflac.min.wasm.js";

// Voice Satellite entity -> Snapcast client id (also used as its host name,
// which is the name snapweb and HA's Snapcast integration show).
const ROOMS = {
  "assist_satellite.wall_kitchen": "kitchen",
};

// Snapcast's own time-sync cadence (snapweb and snapclient both send a Time
// message every second). There is no server push to wait on instead: the
// server only answers. The same tick checks that answers are still arriving.
const SYNC_INTERVAL_MS = 1000;
// No message at all from the server for this long means the link is dead even
// if the socket has not reported a close (Wi-Fi drop, NAT reset).
const LINK_TIMEOUT_MS = 5000;
// Reconnect backoff: first retry after RECONNECT_BASE_MS, doubling to RECONNECT_MAX_MS.
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;

const LOG = "[snapcast-tablet]";

function satelliteEntity() {
  try {
    const direct = localStorage.getItem("vs-satellite-entity");
    if (direct) return direct;
    const cfg = localStorage.getItem("vs-panel-config");
    if (cfg) return JSON.parse(cfg).satellite_entity || null;
  } catch (e) {
    // localStorage unavailable: not a panel we manage.
  }
  return null;
}


class Tv {
  constructor(sec = 0, usec = 0) {
    this.sec = sec;
    this.usec = usec;
  }
  setMilliseconds(ms) {
    this.sec = Math.floor(ms / 1000);
    this.usec = Math.floor(ms * 1000) % 1000000;
  }
  getMilliseconds() {
    return this.sec * 1000 + this.usec / 1000;
  }
}

class BaseMessage {
  constructor() {
    this.type = 0;
    this.id = 0;
    this.refersTo = 0;
    this.received = new Tv();
    this.sent = new Tv();
    this.size = 0;
  }
  deserialize(buffer) {
    const view = new DataView(buffer);
    this.type = view.getUint16(0, true);
    this.id = view.getUint16(2, true);
    this.refersTo = view.getUint16(4, true);
    this.received = new Tv(view.getInt32(6, true), view.getInt32(10, true));
    this.sent = new Tv(view.getInt32(14, true), view.getInt32(18, true));
    this.size = view.getUint32(22, true);
  }
  serialize() {
    this.size = 26 + this.getSize();
    const buffer = new ArrayBuffer(this.size);
    const view = new DataView(buffer);
    view.setUint16(0, this.type, true);
    view.setUint16(2, this.id, true);
    view.setUint16(4, this.refersTo, true);
    view.setInt32(6, this.sent.sec, true);
    view.setInt32(10, this.sent.usec, true);
    view.setInt32(14, this.received.sec, true);
    view.setInt32(18, this.received.usec, true);
    view.setUint32(22, this.size, true);
    return buffer;
  }
  getSize() {
    return 0;
  }
}

class CodecMessage extends BaseMessage {
  constructor(buffer) {
    super();
    this.deserialize(buffer);
    const view = new DataView(buffer);
    const codecSize = view.getInt32(26, true);
    this.codec = new TextDecoder("utf-8").decode(buffer.slice(30, 30 + codecSize));
    const payloadSize = view.getInt32(30 + codecSize, true);
    this.payload = buffer.slice(34 + codecSize, 34 + codecSize + payloadSize);
  }
}

class TimeMessage extends BaseMessage {
  constructor(buffer) {
    super();
    this.latency = new Tv();
    if (buffer) {
      this.deserialize(buffer);
      const view = new DataView(buffer);
      this.latency = new Tv(view.getInt32(26, true), view.getInt32(30, true));
    }
    this.type = 4;
  }
  serialize() {
    const buffer = super.serialize();
    const view = new DataView(buffer);
    view.setInt32(26, this.latency.sec, true);
    view.setInt32(30, this.latency.usec, true);
    return buffer;
  }
  getSize() {
    return 8;
  }
}

class JsonMessage extends BaseMessage {
  constructor(buffer) {
    super();
    this.json = {};
    if (buffer) {
      this.deserialize(buffer);
      const size = new DataView(buffer).getUint32(26, true);
      this.json = JSON.parse(new TextDecoder().decode(buffer.slice(30, 30 + size)));
    }
  }
  serialize() {
    const encoded = new TextEncoder().encode(JSON.stringify(this.json));
    const buffer = super.serialize();
    const view = new DataView(buffer);
    view.setUint32(26, encoded.length, true);
    new Uint8Array(buffer, 30, encoded.length).set(encoded);
    return buffer;
  }
  getSize() {
    return new TextEncoder().encode(JSON.stringify(this.json)).length + 4;
  }
}

class HelloMessage extends JsonMessage {
  constructor(fields) {
    super();
    this.type = 5;
    this.json = fields;
  }
}

class ServerSettingsMessage extends JsonMessage {
  constructor(buffer) {
    super(buffer);
    this.bufferMs = this.json.bufferMs;
    this.latency = this.json.latency;
    this.volumePercent = this.json.volume;
    this.muted = this.json.muted;
  }
}

class SampleFormat {
  constructor() {
    this.rate = 48000;
    this.channels = 2;
    this.bits = 16;
  }
  msRate() {
    return this.rate / 1000;
  }
  toString() {
    return `${this.rate}:${this.bits}:${this.channels}`;
  }
  sampleSize() {
    return this.bits === 24 ? 4 : this.bits / 8;
  }
  frameSize() {
    return this.channels * this.sampleSize();
  }
}

class PcmChunkMessage extends BaseMessage {
  constructor(buffer, sampleFormat) {
    super();
    this.deserialize(buffer);
    const view = new DataView(buffer);
    this.timestamp = new Tv(view.getInt32(26, true), view.getInt32(30, true));
    this.payload = buffer.slice(38);
    this.sampleFormat = sampleFormat;
    this.idx = 0;
    this.type = 2;
  }
  readFrames(frames) {
    let frameCnt = frames;
    const frameSize = this.sampleFormat.frameSize();
    if (this.idx + frames > this.payloadSize() / frameSize) {
      frameCnt = this.payloadSize() / frameSize - this.idx;
    }
    const begin = this.idx * frameSize;
    this.idx += frameCnt;
    return this.payload.slice(begin, begin + frameCnt * frameSize);
  }
  getFrameCount() {
    return this.payloadSize() / this.sampleFormat.frameSize();
  }
  isEndOfChunk() {
    return this.idx >= this.getFrameCount();
  }
  startMs() {
    return this.timestamp.getMilliseconds() + 1000 * (this.idx / this.sampleFormat.rate);
  }
  duration() {
    return 1000 * ((this.getFrameCount() - this.idx) / this.sampleFormat.rate);
  }
  payloadSize() {
    return this.payload.byteLength;
  }
  clearPayload() {
    this.payload = new ArrayBuffer(0);
  }
  addPayload(buffer) {
    const merged = new Uint8Array(this.payload.byteLength + buffer.byteLength);
    merged.set(new Uint8Array(this.payload), 0);
    merged.set(new Uint8Array(buffer), this.payload.byteLength);
    this.payload = merged.buffer;
  }
}


class TimeProvider {
  constructor() {
    this.ctx = null;
    this.diffBuffer = [];
    this.diff = 0;
  }
  setAudioContext(ctx) {
    this.ctx = ctx;
    this.reset();
  }
  reset() {
    this.diffBuffer.length = 0;
    this.diff = 0;
  }
  setDiff(c2s, s2c) {
    if (this.now() === 0) {
      this.reset();
      return;
    }
    if (this.diffBuffer.push((c2s - s2c) / 2) > 100) this.diffBuffer.shift();
    const sorted = [...this.diffBuffer].sort((a, b) => a - b);
    this.diff = sorted[Math.floor(sorted.length / 2)];
  }
  now() {
    if (!this.ctx) return window.performance.now();
    const ts = this.ctx.getOutputTimestamp ? this.ctx.getOutputTimestamp().contextTime : undefined;
    return (ts !== undefined ? ts : this.ctx.currentTime) * 1000;
  }
  nowSec() {
    return this.now() / 1000;
  }
  serverNow() {
    return this.serverTime(this.now());
  }
  serverTime(localTimeMs) {
    return localTimeMs + this.diff;
  }
}

class AudioStream {
  constructor(timeProvider, sampleFormat, bufferMs) {
    this.timeProvider = timeProvider;
    this.sampleFormat = sampleFormat;
    this.bufferMs = bufferMs;
    this.chunks = [];
    this.chunk = undefined;
  }

  addChunk(chunk) {
    this.chunks.push(chunk);
    while (this.chunks.length > 0) {
      const age = this.timeProvider.serverNow() - this.chunks[0].timestamp.getMilliseconds();
      if (age > 5000 + this.bufferMs) this.chunks.shift();
      else break;
    }
  }

  // Fill `buffer` with the audio due at local time playTimeMs, seeking or
  // stretching by a few frames to stay in sync with the server clock.
  getNextBuffer(buffer, playTimeMs) {
    if (!this.chunk) this.chunk = this.chunks.shift();
    const frames = buffer.length;
    const left = new Float32Array(frames);
    const right = new Float32Array(frames);
    let read = 0;
    let pos = 0;
    const serverPlayTimeMs = this.timeProvider.serverTime(playTimeMs);
    if (this.chunk) {
      let age = serverPlayTimeMs - this.chunk.startMs();
      const reqChunkDuration = frames / this.sampleFormat.msRate();
      if (age >= -reqChunkDuration) {
        if (Math.abs(age) > 5) {
          // More than 5 ms off: hard sync by seeking instead of stretching.
          while (this.chunk && age > this.chunk.duration()) {
            this.chunk = this.chunks.shift();
            if (!this.chunk) break;
            age = serverPlayTimeMs - this.chunk.startMs();
          }
          if (this.chunk) {
            if (age > 0) {
              this.chunk.readFrames(Math.floor(age * this.chunk.sampleFormat.msRate()));
            } else if (age < 0) {
              const silentFrames = Math.floor(-age * this.chunk.sampleFormat.msRate());
              left.fill(0, 0, silentFrames);
              right.fill(0, 0, silentFrames);
              read = silentFrames;
              pos = silentFrames;
            }
            age = 0;
          }
        }

        let addFrames = 0;
        let everyN = 0;
        if (age > 0.1) addFrames = Math.ceil(age);
        else if (age < -0.1) addFrames = Math.floor(age);
        const readFrames = frames + addFrames - read;
        if (addFrames !== 0) everyN = Math.ceil((frames + addFrames - read) / (Math.abs(addFrames) + 1));

        while (read < readFrames && this.chunk) {
          const pcmChunk = this.chunk;
          const pcmBuffer = pcmChunk.readFrames(readFrames - read);
          const normalize = 2 ** pcmChunk.sampleFormat.bits;
          const payload = pcmChunk.sampleFormat.bits >= 24 ? new Int32Array(pcmBuffer) : new Int16Array(pcmBuffer);
          for (let i = 0; i < payload.length; i += 2) {
            read++;
            left[pos] = payload[i] / normalize;
            right[pos] = payload[i + 1] / normalize;
            if (everyN !== 0 && read % everyN === 0) {
              if (addFrames > 0) {
                pos--;
              } else {
                left[pos + 1] = left[pos];
                right[pos + 1] = right[pos];
                pos++;
              }
            }
            pos++;
          }
          if (pcmChunk.isEndOfChunk()) this.chunk = this.chunks.shift();
        }
        if (read === readFrames) read = frames;
      }
    }
    if (read < frames) {
      left.fill(0, pos);
      right.fill(0, pos);
    }
    buffer.getChannelData(0).set(left);
    buffer.getChannelData(1).set(right);
  }
}


class PcmDecoder {
  setHeader(buffer) {
    const view = new DataView(buffer);
    const sf = new SampleFormat();
    sf.channels = view.getUint16(22, true);
    sf.rate = view.getUint32(24, true);
    sf.bits = view.getUint16(34, true);
    return sf;
  }
  decode(chunk) {
    return chunk;
  }
  destroy() {}
}

class FlacDecoder {
  constructor(Flac) {
    this.Flac = Flac;
    this.sampleFormat = new SampleFormat();
    this.header = null;
    this.flacChunk = new ArrayBuffer(0);
    this.pcmChunk = null;
    this.cacheInfo = { isCachedChunk: false, cachedBlocks: 0 };
    this.decoder = Flac.create_libflac_decoder(true);
    Flac.init_decoder_stream(
      this.decoder,
      (size) => this.read(size),
      (data, info) => this.write(data, info),
      (err, msg) => console.error(LOG, "flac decode error", err, msg),
      (meta) => this.metadata(meta),
      false,
    );
  }

  setHeader(buffer) {
    this.header = buffer.slice(0);
    this.Flac.FLAC__stream_decoder_process_until_end_of_metadata(this.decoder);
    return this.sampleFormat;
  }

  decode(chunk) {
    this.flacChunk = chunk.payload.slice(0);
    this.pcmChunk = chunk;
    this.pcmChunk.clearPayload();
    this.cacheInfo = { cachedBlocks: 0, isCachedChunk: true };
    while (this.flacChunk.byteLength > 0) {
      if (!this.Flac.FLAC__stream_decoder_process_single(this.decoder)) return null;
    }
    if (this.cacheInfo.cachedBlocks > 0) {
      const diffMs = this.cacheInfo.cachedBlocks / this.sampleFormat.msRate();
      this.pcmChunk.timestamp.setMilliseconds(this.pcmChunk.timestamp.getMilliseconds() - diffMs);
    }
    return this.pcmChunk;
  }

  read(bufferSize) {
    if (this.header) {
      const data = new Uint8Array(this.header);
      this.header = null;
      return { buffer: data, readDataLength: data.byteLength, error: false };
    }
    if (this.flacChunk) {
      // A fresh read: the next write is not from data libflac had cached.
      this.cacheInfo.isCachedChunk = false;
      const data = new Uint8Array(this.flacChunk.slice(0, Math.min(bufferSize, this.flacChunk.byteLength)));
      this.flacChunk = this.flacChunk.slice(data.byteLength);
      return { buffer: data, readDataLength: data.byteLength, error: false };
    }
    return { buffer: new Uint8Array(0), readDataLength: 0, error: false };
  }

  write(data, frameInfo) {
    if (this.cacheInfo.isCachedChunk) this.cacheInfo.cachedBlocks += frameInfo.blocksize;
    const sampleSize = this.sampleFormat.sampleSize();
    const payload = new ArrayBuffer(this.sampleFormat.frameSize() * frameInfo.blocksize);
    const view = new DataView(payload);
    for (let channel = 0; channel < frameInfo.channels; ++channel) {
      const channelData = new DataView(data[channel].buffer, 0, data[channel].buffer.byteLength);
      for (let i = 0; i < frameInfo.blocksize; ++i) {
        const writeIdx = sampleSize * (frameInfo.channels * i + channel);
        const readIdx = sampleSize * i;
        if (sampleSize === 4) view.setInt32(writeIdx, channelData.getInt32(readIdx, true), true);
        else view.setInt16(writeIdx, channelData.getInt16(readIdx, true), true);
      }
    }
    this.pcmChunk.addPayload(payload);
  }

  metadata(meta) {
    this.sampleFormat.rate = meta.sampleRate;
    this.sampleFormat.channels = meta.channels;
    this.sampleFormat.bits = meta.bitsPerSample;
  }

  destroy() {
    try {
      this.Flac.FLAC__stream_decoder_delete(this.decoder);
    } catch (e) {
      // already gone
    }
  }
}

function loadLibflac() {
  return new Promise((resolve, reject) => {
    const ready = () => (window.Flac.isReady() ? resolve(window.Flac) : window.Flac.on("ready", () => resolve(window.Flac)));
    if (window.Flac) {
      ready();
      return;
    }
    // Tell the emscripten loader where its .wasm lives.
    window.FLAC_SCRIPT_LOCATION = LIBFLAC_DIR;
    const script = document.createElement("script");
    script.src = LIBFLAC_DIR + LIBFLAC_SCRIPT;
    script.onload = () => (window.Flac ? ready() : reject(new Error("libflac loaded but window.Flac is missing")));
    script.onerror = () => reject(new Error(`could not load ${script.src}`));
    document.head.appendChild(script);
  });
}


class SnapcastTabletClient {
  constructor(Flac, { url, hostId, hostName }) {
    this.Flac = Flac;
    this.url = url;
    this.hostId = hostId;
    this.hostName = hostName;

    this.timeProvider = new TimeProvider();
    this.ctx = null;
    this.gainNode = null;
    this.socket = null;
    this.msgId = 0;
    this.syncHandle = null;
    this.lastMessageAt = 0;
    this.reconnectHandle = null;
    this.reconnectDelayMs = RECONNECT_BASE_MS;

    this.decoder = null;
    this.sampleFormat = null;
    this.serverSettings = null;
    this.stream = null;
    this.bufferMs = 1000;
    this.latency = 0;
    this.playTime = 0;
    this.bufferDurationMs = 80;
    this.bufferFrameCount = 3844;
    this.audioBufferCount = 3;
    this.audioBuffers = [];
    this.freeBuffers = [];
    this.lastError = null;

    window.addEventListener("online", () => this.reconnectNow());
  }

  status() {
    return {
      hostId: this.hostId,
      socket: this.socket ? ["connecting", "open", "closing", "closed"][this.socket.readyState] : "none",
      audioContext: this.ctx ? this.ctx.state : "none",
      sampleFormat: this.sampleFormat ? this.sampleFormat.toString() : null,
      volume: this.serverSettings ? this.serverSettings.volumePercent : null,
      muted: this.serverSettings ? this.serverSettings.muted : null,
      clockOffsetMs: this.timeProvider.diff,
      queuedChunks: this.stream ? this.stream.chunks.length : 0,
      lastError: this.lastError,
    };
  }

  connect() {
    this.reconnectHandle = null;
    const socket = new WebSocket(this.url);
    this.socket = socket;
    socket.binaryType = "arraybuffer";
    socket.onopen = () => {
      window.__snapcastTabletDiag?.(`socket open ${this.url}`);
      console.info(LOG, `connected to ${this.url} as ${this.hostId}`);
      this.reconnectDelayMs = RECONNECT_BASE_MS;
      this.lastMessageAt = performance.now();
      this.send(
        new HelloMessage({
          MAC: "00:00:00:00:00:00",
          HostName: this.hostName,
          Version: "0.9.2",
          ClientName: "Snapweb",
          OS: navigator.platform || "unknown",
          Arch: "web",
          Instance: 1,
          ID: this.hostId,
          SnapStreamProtocolVersion: 2,
        }),
      );
      this.syncTime();
      this.syncHandle = window.setInterval(() => this.tick(), SYNC_INTERVAL_MS);
    };
    socket.onmessage = (ev) => {
      this.lastMessageAt = performance.now();
      try {
        this.onMessage(ev.data);
      } catch (e) {
        this.lastError = String(e);
        console.error(LOG, "message handling failed", e);
      }
    };
    socket.onerror = () => {
      this.lastError = `websocket error on ${this.url}`;
      window.__snapcastTabletDiag?.(this.lastError);
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      window.clearInterval(this.syncHandle);
      this.syncHandle = null;
      this.stopPlayback();
      this.scheduleReconnect();
    };
  }

  tick() {
    if (performance.now() - this.lastMessageAt > LINK_TIMEOUT_MS) {
      console.warn(LOG, `no reply from the server for ${LINK_TIMEOUT_MS} ms, reconnecting`);
      this.lastError = "link timeout";
      this.socket.close();
      return;
    }
    this.syncTime();
  }

  scheduleReconnect() {
    if (this.reconnectHandle !== null) return;
    const delay = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(this.reconnectDelayMs * 2, RECONNECT_MAX_MS);
    console.info(LOG, `disconnected, retrying in ${delay} ms`);
    this.reconnectHandle = window.setTimeout(() => this.connect(), delay);
  }

  // The network came back: skip whatever is left of the backoff.
  reconnectNow() {
    if (this.reconnectHandle === null) return;
    window.clearTimeout(this.reconnectHandle);
    this.reconnectDelayMs = RECONNECT_BASE_MS;
    this.connect();
  }

  // Drop everything tied to one stream session; the AudioContext is kept so
  // it stays unlocked across reconnects.
  stopPlayback() {
    while (this.audioBuffers.length > 0) {
      const pb = this.audioBuffers.pop();
      pb.source.onended = null;
      try {
        pb.source.stop();
      } catch (e) {
        // not started yet
      }
    }
    this.freeBuffers.length = 0;
    if (this.decoder) this.decoder.destroy();
    this.decoder = null;
    this.stream = null;
  }

  ensureAudioContext(rate) {
    if (this.ctx && this.ctx.sampleRate !== rate) {
      this.ctx.close();
      this.ctx = null;
    }
    if (!this.ctx) {
      this.ctx = new AudioContext({ latencyHint: "interactive", sampleRate: rate });
      this.gainNode = this.ctx.createGain();
      this.gainNode.connect(this.ctx.destination);
      this.ctx.onstatechange = () => console.info(LOG, `AudioContext ${this.ctx.state}`);
    }
    this.ctx.resume().then(() => {
      if (this.ctx.state !== "running") this.resumeOnGesture();
    }, () => this.resumeOnGesture());
  }

  // Fallback for a browser that blocks autoplay: the first touch or key press
  // anywhere on the page unlocks audio.
  resumeOnGesture() {
    if (this.waitingForGesture) return;
    this.waitingForGesture = true;
    this.lastError = "autoplay blocked, waiting for a touch";
    window.__snapcastTabletDiag?.(this.lastError);
    console.warn(LOG, this.lastError);
    const unlock = () => {
      this.ctx.resume();
      this.waitingForGesture = false;
      document.removeEventListener("pointerdown", unlock, true);
      document.removeEventListener("keydown", unlock, true);
    };
    document.addEventListener("pointerdown", unlock, true);
    document.addEventListener("keydown", unlock, true);
  }

  applyVolume() {
    if (this.gainNode && this.serverSettings) {
      this.gainNode.gain.value = this.serverSettings.muted ? 0 : this.serverSettings.volumePercent / 100;
    }
  }

  onMessage(data) {
    const type = new DataView(data).getUint16(0, true);
    if (type === 1) {
      const codec = new CodecMessage(data);
      this.stopPlayback();
      if (codec.codec === "flac") this.decoder = new FlacDecoder(this.Flac);
      else if (codec.codec === "pcm") this.decoder = new PcmDecoder();
      else {
        this.lastError = `codec not supported: ${codec.codec}`;
        console.error(LOG, this.lastError);
        return;
      }
      this.sampleFormat = this.decoder.setHeader(codec.payload);
      if (this.sampleFormat.channels !== 2 || this.sampleFormat.bits < 16) {
        this.lastError = `stream must be stereo, 16 bit or more: ${this.sampleFormat}`;
        console.error(LOG, this.lastError);
        this.decoder = null;
        return;
      }
      this.bufferFrameCount = Math.floor(this.bufferDurationMs * this.sampleFormat.msRate());
      this.ensureAudioContext(this.sampleFormat.rate);
      this.timeProvider.setAudioContext(this.ctx);
      this.applyVolume();
      this.stream = new AudioStream(this.timeProvider, this.sampleFormat, this.bufferMs);
      this.latency = (this.ctx.baseLatency || 0) + (this.ctx.outputLatency || 0);
      console.info(LOG, `codec ${codec.codec} ${this.sampleFormat}, output latency ${this.latency.toFixed(3)} s`);
      this.play();
    } else if (type === 2) {
      if (!this.decoder || !this.stream) return;
      const decoded = this.decoder.decode(new PcmChunkMessage(data, this.sampleFormat));
      if (decoded) this.stream.addChunk(decoded);
    } else if (type === 3) {
      this.serverSettings = new ServerSettingsMessage(data);
      this.bufferMs = this.serverSettings.bufferMs - this.serverSettings.latency;
      if (this.stream) this.stream.bufferMs = this.bufferMs;
      this.applyVolume();
    } else if (type === 4) {
      const time = new TimeMessage(data);
      this.timeProvider.setDiff(time.latency.getMilliseconds(), this.timeProvider.now() - time.sent.getMilliseconds());
    }
  }

  send(msg) {
    msg.sent = new Tv();
    msg.sent.setMilliseconds(this.timeProvider.now());
    msg.id = ++this.msgId;
    if (this.socket && this.socket.readyState === WebSocket.OPEN) this.socket.send(msg.serialize());
  }

  syncTime() {
    const t = new TimeMessage();
    t.latency.setMilliseconds(this.timeProvider.now());
    this.send(t);
  }

  play() {
    this.playTime = this.timeProvider.nowSec() + 0.1;
    for (let i = 0; i < this.audioBufferCount; ++i) this.playNext();
  }

  // Each scheduled buffer queues the next one when it ends, so audio is driven
  // by the AudioContext clock rather than a timer.
  playNext() {
    if (!this.stream) return;
    const buffer = this.freeBuffers.pop() || this.ctx.createBuffer(this.sampleFormat.channels, this.bufferFrameCount, this.sampleFormat.rate);
    const playTimeMs = (this.playTime + this.latency) * 1000 - this.bufferMs;
    this.stream.getNextBuffer(buffer, playTimeMs);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gainNode);
    const pb = { buffer, source };
    this.audioBuffers.push(pb);
    source.onended = () => {
      const i = this.audioBuffers.indexOf(pb);
      if (i !== -1) this.audioBuffers.splice(i, 1);
      this.freeBuffers.push(buffer);
      this.playNext();
    };
    source.start(this.playTime);
    this.playTime += this.bufferFrameCount / this.sampleFormat.rate;
  }
}


export async function startSnapcastClient({ hostId, hostName = hostId, url = SNAPSERVER_STREAM_URL }) {
  if (window.__snapcastTablet) return window.__snapcastTablet;
  const Flac = await loadLibflac();
  const client = new SnapcastTabletClient(Flac, { url, hostId, hostName });
  window.__snapcastTablet = client;
  client.connect();
  return client;
}

// Diagnostics into HA's system log (Settings > System > Logs, logger
// snapcast_tablet), because a mounted kiosk tablet has no reachable devtools.
// The hass object is attached to <home-assistant> after this module runs and
// there is no event for it, so poll for it on one bounded cadence.
const DIAG_POLL_MS = 1000;
const DIAG_POLL_MAX = 60;
const diagQueue = [];
let diagPolls = 0;
function diagFlush() {
  const hass = document.querySelector("home-assistant")?.hass;
  if (!hass) {
    if (diagPolls++ < DIAG_POLL_MAX) setTimeout(diagFlush, DIAG_POLL_MS);
    return;
  }
  while (diagQueue.length) {
    hass
      .callService("system_log", "write", { message: `${LOG} ${diagQueue.shift()}`, level: "warning", logger: "snapcast_tablet" })
      .catch(() => {});
  }
}
function diag(msg) {
  console.info(LOG, msg);
  diagQueue.push(msg);
  if (diagQueue.length === 1) diagFlush();
}
window.__snapcastTabletDiag = diag;

let rawKeys = "";
try {
  rawKeys = `vs-satellite-entity=${localStorage.getItem("vs-satellite-entity")} vs-panel-config=${localStorage.getItem("vs-panel-config") ? "set" : "unset"}`;
} catch (e) {
  rawKeys = `localStorage error ${e}`;
}
const satellite = satelliteEntity();
const room = ROOMS[satellite];
if (satellite || location.search.includes("kiosk")) diag(`loaded: path=${location.pathname} satellite=${satellite} room=${room || "none"} ${rawKeys} ua=${navigator.userAgent.slice(0, 80)}`);
if (room) {
  startSnapcastClient({ hostId: room })
    .then((c) => diag(`started ${room}: ${JSON.stringify(c.status())}`))
    .catch((e) => diag(`not started: ${e}`));
}
