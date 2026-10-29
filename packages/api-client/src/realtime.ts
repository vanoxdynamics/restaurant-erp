import { WS_CHANNELS } from '@repo/shared-utils';

export type RealtimeEventHandler = (event: any) => void;

export class RealtimeChannelClient {
  private wsUrl: string;
  private sockets: Map<string, WebSocket> = new Map();
  private listeners: Map<string, Set<RealtimeEventHandler>> = new Map();
  private reconnectTimers: Map<string, any> = new Map();

  constructor(wsBaseUrl: string = 'ws://localhost:8000') {
    this.wsUrl = wsBaseUrl;
  }

  subscribe(channelId: string, handler: RealtimeEventHandler): () => void {
    if (!this.listeners.has(channelId)) {
      this.listeners.set(channelId, new Set());
    }
    this.listeners.get(channelId)!.add(handler);

    this.connectChannel(channelId);

    return () => {
      const channelListeners = this.listeners.get(channelId);
      if (channelListeners) {
        channelListeners.delete(handler);
        if (channelListeners.size === 0) {
          this.disconnectChannel(channelId);
        }
      }
    };
  }

  private connectChannel(channelId: string) {
    if (this.sockets.has(channelId)) return;

    try {
      const url = `${this.wsUrl}/ws/channels/${encodeURIComponent(channelId)}`;
      const ws = new WebSocket(url);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.emit(channelId, data);
        } catch {
          this.emit(channelId, event.data);
        }
      };

      ws.onclose = () => {
        this.sockets.delete(channelId);
        this.scheduleReconnect(channelId);
      };

      ws.onerror = () => {
        // Fallback gracefully without throwing uncaught exceptions in mobile
        ws.close();
      };

      this.sockets.set(channelId, ws);
    } catch {
      this.scheduleReconnect(channelId);
    }
  }

  private scheduleReconnect(channelId: string) {
    if (this.reconnectTimers.has(channelId)) return;

    const timer = setTimeout(() => {
      this.reconnectTimers.delete(channelId);
      if (this.listeners.has(channelId) && this.listeners.get(channelId)!.size > 0) {
        this.connectChannel(channelId);
      }
    }, 5000);

    this.reconnectTimers.set(channelId, timer);
  }

  private disconnectChannel(channelId: string) {
    const ws = this.sockets.get(channelId);
    if (ws) {
      ws.close();
      this.sockets.delete(channelId);
    }
    const timer = this.reconnectTimers.get(channelId);
    if (timer) {
      clearTimeout(timer);
      this.reconnectTimers.delete(channelId);
    }
  }

  emit(channelId: string, data: any) {
    const handlers = this.listeners.get(channelId);
    if (handlers) {
      handlers.forEach((h) => {
        try {
          h(data);
        } catch (e) {
          console.error(`Error in realtime handler for ${channelId}:`, e);
        }
      });
    }
  }

  send(channelId: string, payload: any) {
    const ws = this.sockets.get(channelId);
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }
}

export const realtimeClient = new RealtimeChannelClient();
