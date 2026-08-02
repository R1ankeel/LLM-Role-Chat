import type { Message } from '@/types/message'

export type SSEEvent =
  | { type: 'message'; message: Message }
  | { type: 'token'; text: string; character_id: number }
  | { type: 'done' }
  | { type: 'error'; detail: string; rate_limit?: boolean }
