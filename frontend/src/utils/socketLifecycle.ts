export type SocketCloseDecision = "ignore" | "settle" | "sync" | "reconnect";

interface SocketCloseContext {
  generation: number;
  currentGeneration: number;
  disposed: boolean;
  terminal: boolean;
  reconnectCount: number;
  maxReconnects: number;
}

export function socketCloseDecision(context: SocketCloseContext): SocketCloseDecision {
  if (context.generation !== context.currentGeneration) return "ignore";
  if (context.disposed || context.terminal) return "settle";
  if (context.reconnectCount >= context.maxReconnects) return "sync";
  return "reconnect";
}
