import type { Message } from '@/types/message'
import type { SSEEvent } from '@/types/sse'
import { ApiError, toApiError } from '@/api/client'

type TokenCb = (text: string, characterId: number) => void
type MessageCb = (message: Message) => void
type DoneCb = () => void
type ErrorCb = (error: ApiError) => void

export interface MessageStream {
  readonly signal: AbortSignal
  readonly aborted: boolean
  onToken(cb: TokenCb): this
  onMessage(cb: MessageCb): this
  onDone(cb: DoneCb): this
  onError(cb: ErrorCb): this
  abort(): void
}

export async function readSSEStream(
  response: Response,
  onEvent: (event: SSEEvent) => void,
): Promise<void> {
  if (!response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const event = parseDataLine(part)
        if (event) onEvent(event)
      }
    }
  } catch (error) {
    // AbortError is expected on stop(); anything else surfaces as a network error.
    if (error instanceof DOMException && error.name === 'AbortError') return
    throw error
  }

  const tail = parseDataLine(buffer)
  if (tail) onEvent(tail)
}

function parseDataLine(part: string): SSEEvent | null {
  const line = part.split('\n').find((l) => l.startsWith('data: '))
  if (!line) return null
  try {
    return JSON.parse(line.slice(6)) as SSEEvent
  } catch {
    return null
  }
}

export class SseMessageStream implements MessageStream {
  private tokenCbs: TokenCb[] = []
  private messageCbs: MessageCb[] = []
  private doneCbs: DoneCb[] = []
  private errorCbs: ErrorCb[] = []
  private abortController = new AbortController()
  private _aborted = false

  get signal(): AbortSignal {
    return this.abortController.signal
  }

  get aborted(): boolean {
    return this._aborted
  }

  onToken(cb: TokenCb): this {
    this.tokenCbs.push(cb)
    return this
  }

  onMessage(cb: MessageCb): this {
    this.messageCbs.push(cb)
    return this
  }

  onDone(cb: DoneCb): this {
    this.doneCbs.push(cb)
    return this
  }

  onError(cb: ErrorCb): this {
    this.errorCbs.push(cb)
    return this
  }

  abort(): void {
    if (this._aborted) return
    this._aborted = true
    this.abortController.abort()
  }

  async consume(promise: Promise<Response>): Promise<void> {
    let response: Response
    try {
      response = await promise
    } catch (error) {
      if (this._aborted) return
      const detail =
        error instanceof Error ? error.message : 'Ошибка сети при отправке сообщения'
      this.emitError(new ApiError(0, detail))
      return
    }

    if (!response.ok) {
      this.emitError(await toApiError(response))
      return
    }

    try {
      await readSSEStream(response, (event) => {
        switch (event.type) {
          case 'token':
            for (const cb of this.tokenCbs) cb(event.text, event.character_id)
            break
          case 'message':
            for (const cb of this.messageCbs) cb(event.message)
            break
          case 'done':
            for (const cb of this.doneCbs) cb()
            break
          case 'error':
            this.emitError(new ApiError(200, event.detail, event.rate_limit ?? false))
            break
        }
      })
    } catch (error) {
      if (this._aborted) return
      const detail = error instanceof Error ? error.message : 'Ошибка чтения потока ответа'
      this.emitError(new ApiError(0, detail))
    }
  }

  private emitError(error: ApiError): void {
    for (const cb of this.errorCbs) cb(error)
  }
}
