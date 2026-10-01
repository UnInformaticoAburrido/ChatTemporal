import { ApiError } from './api.js';

export function envelope(type, conversation_id, payload) {
  return { type, request_id: crypto.randomUUID(), conversation_id, timestamp: new Date().toISOString(), payload };
}
// Mensajes y borradores solo en RAM. El servidor decide el estado; un socket.send no confirma entrega.
export class Chat {
  constructor(api, key, { changed = () => {}, error = () => {}, socket = url => new WebSocket(url), origin = location.origin } = {}) {
    Object.assign(this, { api, key, changed, error, socketFactory: socket, origin });
    this.rooms = new Map(); this.outbox = new Map(); this.ready = false; this.stopped = false;
    this.selected = null; this.next = null; this.delay = 1000; this.chain = Promise.resolve();
  }
  async start() { await this.list(); if (!this.stopped) { this.connect(); this.poll = setInterval(() => this.sync().catch(e => this.report(e)), 5000); } }
  report(error) { if (!this.stopped) { if (!this.api.signedIn) this.dispose(); this.error(error); } }
  emit() { if (!this.stopped) this.changed(); }
  room(value) {
    let room = this.rooms.get(value.id);
    if (!room) { room = { messages: new Map(), vote: null, publicKey: null, cursor: null }; this.rooms.set(value.id, room); }
    Object.assign(room, value);
    return room;
  }
  async list(more = false) {
    const page = await this.api.conversations(more ? this.next : null);
    if (this.stopped) return;
    page.items.forEach(value => this.room(value)); this.next = page.next_cursor; this.emit();
  }
  async select(id) { this.selected = id; await this.refresh(id); }
  async refresh(id, more = false) {
    const value = await this.api.conversation(id);
    if (this.stopped) return;
    const room = this.room(value);
    const vote = await this.api.currentVote(id);
    if (this.stopped) return;
    this.applyVote(room, vote);
    try { const key = await this.api.peerKey(id); if (!this.stopped) room.publicKey = key.public_key; }
    catch (e) { if (e.code !== 'USER_NOT_FOUND') throw e; }
    if (this.stopped) return;
    if (room.mode === 'stored') {
      const page = await this.api.history(id, more ? room.cursor : null);
      if (this.stopped) return;
      room.cursor = page.next_cursor;
      for (const item of page.items) await this.receive(room, item, false);
    }
    this.emit();
  }
  applyVote(room, vote) {
    room.vote = vote;
    if (vote?.status === 'rejected') {
      for (const [id, message] of room.messages) if (message.grace) room.messages.delete(id);
      for (const [id, item] of this.outbox) if (item.room === room.id && item.grace) this.outbox.delete(id);
    }
  }
  async sync() {
    if (this.stopped || this.syncing) return;
    this.syncing = true;
    try {
      await this.list();
      if (this.selected) await this.refresh(this.selected);
      for (const [id, item] of this.outbox) if (!['delivered', 'failed', 'expired'].includes(item.status)) await this.checkStatus(id);
    } finally { this.syncing = false; }
  }
  async connect() {
    if (this.stopped || this.connecting) return;
    this.connecting = true;
    try {
      const { ticket } = await this.api.ticket();
      if (this.stopped) return;
      const url = new URL('/ws/v1', this.origin); url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'; url.searchParams.set('ticket', ticket);
      const ws = this.socketFactory(url.href); this.ws = ws;
      this.openTimer = setTimeout(() => { if (!this.ready) ws.close(); }, 15000);
      ws.onmessage = event => {
        this.chain = this.chain.then(async () => {
          if (this.stopped || this.ws !== ws) return;
          if (typeof event.data !== 'string' || event.data.length > 16384) throw new ApiError('INVALID_RESPONSE');
          await this.handle(JSON.parse(event.data));
        }).catch(e => this.report(e));
      };
      ws.onclose = event => {
        clearTimeout(this.openTimer); if (this.ws !== ws || this.stopped) return;
        this.ready = false; this.emit();
        if (event.code === 4401) { this.api.clear(); this.report(new ApiError('SESSION_LOST')); return; }
        this.schedule();
      };
      ws.onerror = () => {}; // onclose gobierna la reconexión; jamás imprimir URL/ticket.
    } catch (e) { this.report(e); this.schedule(); }
    finally { this.connecting = false; }
  }
  schedule() {
    if (this.stopped) return;
    clearTimeout(this.reconnect); this.reconnect = setTimeout(() => this.connect(), this.delay);
    this.delay = Math.min(this.delay * 2, 30000);
  }
  transmit(event) {
    if (this.stopped || !this.ready || this.ws?.readyState !== 1) throw new ApiError('NOT_CONNECTED');
    this.ws.send(JSON.stringify(event));
  }
  async handle(event) {
    if (event.type === 'session.ready') {
      clearTimeout(this.openTimer); this.ready = true; this.delay = 1000; this.emit();
      await this.sync(); return;
    }
    if (event.type === 'system.error') {
      const item = [...this.outbox.values()].find(item => item.request === event.request_id);
      if (item) { item.status = 'unknown'; this.emit(); }
      throw new ApiError(event.payload.code);
    }
    const id = event.conversation_id;
    if (!id) return;
    if (!this.rooms.has(id)) await this.refresh(id);
    if (this.stopped) return;
    const room = this.rooms.get(id), payload = event.payload;
    if (!room) return;
    if (event.type.startsWith('vote.')) { this.applyVote(room, payload); await this.refresh(id); }
    else if (event.type.startsWith('conversation.')) await this.refresh(id);
    else if (event.type === 'message.offer') this.transmit(envelope('message.ready', id, { message_id: payload.message_id }));
    else if (event.type === 'message.ready') {
      const item = this.outbox.get(payload.message_id);
      if (item?.room === id && item.status === 'offered') {
        this.transmit(item.frame); item.status = 'unknown'; item.request = item.frame.request_id;
      }
    } else if (event.type === 'message.new') await this.receive(room, payload, true);
    else if (event.type === 'message.delivered' || event.type === 'message.failed') {
      const item = this.outbox.get(payload.message_id);
      if (item?.room === id) { item.status = event.type === 'message.delivered' ? 'delivered' : 'failed'; item.code = payload.code; }
    } else if (event.type.startsWith('recovery.replay.')) this.receiveReplay(room, event);
    this.emit();
  }
  async receive(room, payload, realtime) {
    const own = payload.sender_role === room.role;
    if (own && !room.publicKey) return;
    let text;
    try { text = this.key.decrypt(payload, own ? room.publicKey : null); }
    catch { throw new ApiError('MESSAGE_CORRUPTED'); }
    const grace = payload.is_grace_message ?? (!room.accepted_at || Date.parse(payload.sent_at) < Date.parse(room.accepted_at));
    if (!(grace && room.vote?.status === 'rejected')) {
      room.messages.set(payload.message_id, { id: payload.message_id, text, own, grace, time: payload.sent_at });
      this.trim(room);
    }
    if (!own && this.ready) this.transmit(envelope('message.ack', room.id, { message_id: payload.message_id }));
    // Incluso al recuperar historial solo se confirma contenido que se ha descifrado.
    if (realtime) this.emit();
  }
  trim(room) {
    if (room.messages.size > 500) {
      const oldest = [...room.messages.values()].sort((a,b) => a.time.localeCompare(b.time))[0];
      room.messages.delete(oldest.id);
    }
  }
  async send(id, text) {
    const room = this.rooms.get(id);
    if (!room || room.status === 'closed') throw new ApiError('CONVERSATION_CLOSED');
    if (room.vote?.status === 'open') throw new ApiError('VOTE_OPEN');
    if (!text.trim()) return;
    if (room.status === 'pending' && room.grace_messages_used >= room.grace_message_limit) throw new ApiError('GRACE_LIMIT_REACHED');
    if (!this.ready) throw new ApiError('NOT_CONNECTED');
    const remote = await this.api.peerKey(id); if (this.stopped) return;
    room.publicKey = remote.public_key;
    const message_id = crypto.randomUUID(), payload = { message_id, ...this.key.encrypt(text, room.publicKey) };
    const frame = envelope('message.send', id, payload);
    const item = { room: id, frame, request: frame.request_id, status: 'unknown', mode: room.mode, grace: room.status === 'pending' };
    this.outbox.set(message_id, item);
    room.messages.set(message_id, { id: message_id, text, own: true, grace: item.grace, time: frame.timestamp });
    if (room.mode === 'ephemeral') {
      const offer = envelope('message.offer', id, { message_id }); item.request = offer.request_id; item.status = 'offered';
      this.transmit(offer);
      setTimeout(() => { if (!this.stopped && item.status === 'offered') { item.status = 'expired'; this.emit(); } }, 35000);
    } else this.transmit(frame);
    this.trim(room); this.emit();
  }
  async checkStatus(id) {
    const item = this.outbox.get(id); if (!item) return null;
    try {
      const status = await this.api.status(id);
      if (this.stopped) return null;
      item.status = status.status; this.emit(); return status;
    } catch (e) { if (e.code !== 'MESSAGE_NOT_FOUND') throw e; return null; }
  }
  async retry(id) {
    const item = this.outbox.get(id);
    if (!item || item.mode !== 'stored') return;
    const status = await this.checkStatus(id);
    if (this.stopped || (status && status.status !== 'pending')) return;
    // Conservar UUID, nonce y ciphertext: nunca generar otro mensaje para un resultado incierto.
    this.transmit(item.frame); item.status = 'unknown'; this.emit();
  }
  async change(id, action) {
    try { await this.api.change(id, action); }
    finally {
      if (!this.stopped) {
        if (action === 'leave') { this.rooms.delete(id); if (this.selected === id) this.selected = null; }
        else await this.refresh(id);
        await this.list();
      }
    }
  }
  receiveReplay(room, event) {
    const p = event.payload;
    if (event.type.endsWith('.begin')) room.replay = { id: p.replay_id, next: 0, messages: [] };
    else {
      const replay = room.replay;
      if (!replay || replay.id !== p.replay_id) return;
      if (event.type.endsWith('.item')) {
        if (p.sequence !== replay.next || replay.next >= 500) { room.replay = null; throw new ApiError('INVALID_RESPONSE'); }
        const text = this.key.decrypt(p);
        replay.messages.push({ id: p.original_message_id || crypto.randomUUID(), text, own: false, grace: false,
          time: p.original_timestamp || event.timestamp, recovered: true }); replay.next++;
      } else if (p.item_count === replay.next) {
        replay.messages.forEach(item => { if (!room.messages.has(item.id)) room.messages.set(item.id, item); });
        room.replay = null; this.trim(room);
      } else { room.replay = null; throw new ApiError('INVALID_RESPONSE'); }
    }
  }
  async replay(id) {
    const room = this.rooms.get(id);
    if (room?.status !== 'active' || room.vote?.status === 'open') throw new ApiError('VOTE_OPEN');
    const publicKey = (await this.api.peerKey(id)).public_key, replay_id = crypto.randomUUID();
    const messages = [...room.messages.values()].filter(m => !m.recovered).sort((a,b) => a.time.localeCompare(b.time));
    this.transmit(envelope('recovery.replay.begin', id, { replay_id }));
    for (const [sequence, m] of messages.entries()) {
      if (this.stopped) return;
      this.transmit(envelope('recovery.replay.item', id, { replay_id, sequence, original_message_id: m.id,
        original_timestamp: m.time, ...this.key.encrypt(m.text, publicKey) }));
      await new Promise(resolve => setTimeout(resolve, 60));
    }
    this.transmit(envelope('recovery.replay.end', id, { replay_id, item_count: messages.length }));
  }
  dispose() {
    this.stopped = true; this.ready = false; clearInterval(this.poll); clearTimeout(this.reconnect); clearTimeout(this.openTimer);
    this.ws?.close(); this.rooms.clear(); this.outbox.clear();
  }
}
